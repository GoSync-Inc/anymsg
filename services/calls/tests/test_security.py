"""Synthetic adversarial requests: no provider sessions or outbound traffic."""
import asyncio
import base64
import hashlib
import hmac
import os
import secrets
from urllib.parse import urlencode

import httpx
import pytest

from app import create_app
from outbound import TwilioCalls
from security import CallbackLimits, OwnerCredential
from test_tasks import Archive, HEADERS, owner_headers

CFG = {'account_sid': 'AC' + '1' * 32, 'auth_token': 'synthetic-only',
       'public_url': 'https://voice.example.test', 'phone_from': '+15555550101'}
PATH = '/provider/twilio/inbound'


@pytest.fixture
def application(tmp_path):
    app = create_app(tmp_path, Archive(), control_token='control-fixture')
    app.state.outbound.providers['twilio'] = TwilioCalls(CFG)
    yield app
    app.state.service.close()


def callback_headers(params=None):
    params = params or {'AccountSid': CFG['account_sid']}
    message = CFG['public_url'] + PATH + ''.join(key + params[key] for key in sorted(params))
    signature = base64.b64encode(hmac.new(CFG['auth_token'].encode(), message.encode(), hashlib.sha1).digest()).decode()
    return {'Host': 'voice.example.test', 'Content-Type': 'application/x-www-form-urlencoded',
            'X-Twilio-Signature': signature}


async def invoke(app, *, headers=None, chunks=None, receive=None, path=PATH):
    """Raw ASGI permits chunked/slow/ambiguous headers hidden by normal clients."""
    scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
             'scheme': 'https', 'method': 'POST', 'path': path, 'raw_path': path.encode(),
             'query_string': b'', 'root_path': '', 'server': ('voice.example.test', 443),
             'client': ('192.0.2.1', 1234), 'headers': headers or [
                 (key.lower().encode(), value.encode()) for key, value in callback_headers().items()]}
    messages = []
    chunks = iter(chunks or [urlencode({'AccountSid': CFG['account_sid']}).encode()])

    async def next_chunk():
        try:
            return {'type': 'http.request', 'body': next(chunks), 'more_body': True}
        except StopIteration:
            return {'type': 'http.request', 'body': b'', 'more_body': False}

    async def send(message):
        messages.append(message)

    await app(scope, receive or next_chunk, send)
    return next((m['status'] for m in messages if m['type'] == 'http.response.start'), None)


async def test_all_owner_routes_reject_host_origin_spoof_before_side_effects(application, tmp_path):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(application), base_url='http://127.0.0.1') as c:
        for path in ['/', '/setup.html', '/docs', '/openapi.json', '/api/config', '/api/calling', '/api/calls']:
            response = await c.get(path)
            assert response.status_code == 401
            assert response.headers['www-authenticate'].startswith('Basic ')
            assert response.headers['cache-control'] == 'no-store'
        for path in ['/api/calling', '/api/credentials', '/api/briefs', '/api/calls/start', '/api/learning/activate']:
            response = await c.post(path, headers={'Origin': 'http://127.0.0.1', 'X-Voice-Service': '1'},
                                    json={'enabled': True, 'gemini': 'unauthorized'})
            assert response.status_code == 401
        for token in ['control-fixture', 'agent-fixture', '']:
            assert (await c.get('/api/config', headers={'Authorization': 'Bearer ' + token})).status_code == 401
        assert not (tmp_path / 'calling.json').exists()
        assert not (tmp_path / 'model-key.json').exists()
        assert application.state.service.list_calls() == []


async def test_owner_basic_bearer_csrf_and_rotation(application, tmp_path):
    token = (tmp_path / 'owner.token').read_text().strip()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(application), base_url='http://testserver') as c:
        assert (await c.get('/api/config', auth=('owner', token))).status_code == 200
        assert (await c.get('/api/config', auth=('other', token))).status_code == 401
        for auth in ['Basic bad', 'Basic ' + base64.b64encode(b'no-colon').decode(), 'Bearer ÿ']:
            # HTTP header bytes allow the non-ASCII credential rejection path.
            assert (await c.get('/api/config', headers={b'authorization': auth.encode('latin1')})).status_code == 401
        auth = owner_headers(tmp_path)
        assert (await c.post('/api/credentials', headers=auth, json={'gemini': 'synthetic'})).status_code == 403
        assert (await c.post('/api/credentials', headers={**auth, **HEADERS}, json={'gemini': 'synthetic'})).status_code == 200
        assert (await c.post('/api/credentials', headers={**auth, **HEADERS, 'Origin': 'https://evil.test'}, json={})).status_code == 403
        assert (await c.post('/control/mcp', headers=auth, json={})).status_code == 401
        assert (await c.get('/api/config', headers=[('authorization', auth['Authorization'])] * 2)).status_code == 401
        (tmp_path / 'owner.token').chmod(0o644)
        assert (await c.get('/api/config', headers=auth)).status_code == 401
        (tmp_path / 'owner.token').chmod(0o600)
        replacement = tmp_path / 'new-owner.token'
        replacement.write_text(secrets.token_urlsafe(32) + '\n'); replacement.chmod(0o600)
        replacement.replace(tmp_path / 'owner.token')
        assert (await c.get('/api/config', headers=auth)).status_code == 401
        fresh = owner_headers(tmp_path)
        assert (await c.get('/api/config', headers=fresh)).status_code == 200
        (tmp_path / 'owner.token').unlink()
        assert (await c.get('/api/config', headers=fresh)).status_code == 401
        assert not (tmp_path / 'owner.token').exists()


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'permissions', 'invalid', 'fifo'])
def test_unsafe_owner_credentials_fail_closed(tmp_path, kind):
    source = tmp_path / 'source'
    source.write_text(secrets.token_urlsafe(32)); source.chmod(0o600)
    target = tmp_path / 'owner.token'
    if kind == 'symlink':
        target.symlink_to(source)
    elif kind == 'hardlink':
        os.link(source, target)
    elif kind == 'fifo':
        os.mkfifo(target, 0o600)
    else:
        target.write_text(source.read_text() if kind == 'permissions' else 'short')
        target.chmod(0o644 if kind == 'permissions' else 0o600)
    with pytest.raises((OSError, ValueError)):
        OwnerCredential(tmp_path)


async def test_signed_callback_includes_unknown_fields_and_binds_account(application):
    params = {'AccountSid': CFG['account_sid'], 'FutureField': 'new + поле', 'Empty': ''}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(application), base_url=CFG['public_url']) as c:
        response = await c.post(PATH, data=params, headers=callback_headers(params))
        assert response.status_code == 200 and '<Reject ' in response.text
        changed = {**params, 'FutureField': 'tampered'}
        assert (await c.post(PATH, data=changed, headers=callback_headers(params))).status_code == 403
        wrong_account = {'AccountSid': 'AC' + '2' * 32}
        assert (await c.post(PATH, data=wrong_account, headers=callback_headers(wrong_account))).status_code == 403
        assert (await c.post(PATH + '?extra=1', data=params, headers=callback_headers(params))).status_code == 400


@pytest.mark.parametrize(('extra', 'status'), [
    ({'X-Twilio-Signature': ''}, 403), ({'Host': 'evil.test'}, 403),
    ({'Content-Length': '65537'}, 413), ({'Content-Length': '-1'}, 400),
    ({'Content-Type': 'multipart/form-data; boundary=x'}, 415),
])
async def test_callback_rejects_before_reading_body(application, extra, status):
    async def never_read():
        pytest.fail('Rejected callback consumed body')
    headers = [(key.lower().encode(), value.encode()) for key, value in {**callback_headers(), **extra}.items()]
    assert await invoke(application, headers=headers, receive=never_read) == status


@pytest.mark.parametrize('header', ['content-length', 'content-type', 'x-twilio-signature', 'host'])
async def test_ambiguous_callback_headers_rejected_before_body(application, header):
    async def never_read():
        pytest.fail('Ambiguous request consumed body')
    headers = [(key.lower().encode(), value.encode()) for key, value in callback_headers().items()]
    if header == 'content-length':
        headers += [(b'content-length', b'0')] * 2
    else:
        headers += [pair for pair in headers if pair[0] == header.encode()]
    assert await invoke(application, headers=headers, receive=never_read) in {400, 403, 415}


@pytest.mark.parametrize('chunks', [[b'x' * 65537], [b'x' * 40000, b'x' * 30000]])
async def test_chunked_callback_actual_byte_limit(application, chunks):
    assert await invoke(application, chunks=chunks) == 413


async def test_declared_length_does_not_override_actual_body(application):
    headers = [(key.lower().encode(), value.encode()) for key, value in callback_headers().items()]
    headers.append((b'content-length', b'1'))
    assert await invoke(application, headers=headers, chunks=[b'x' * 65537]) == 413
    assert await invoke(application, headers=headers, chunks=[b'x=1']) == 400


@pytest.mark.parametrize('body', [b'a=1&a=2', b'a=%ZZ', b'a=%ff', b'a=\xff', b'a', b'=x',
                                 b'a=' + b'x' * 4097, b'x' * 129 + b'=v',
                                 b'&'.join(f'k{i}=v'.encode() for i in range(65))])
async def test_callback_form_limits(application, body):
    assert await invoke(application, chunks=[body]) == 400


async def test_callback_total_deadline_capacity_disconnect_and_cancel_release(tmp_path):
    app = create_app(tmp_path, Archive(), callback_limits=CallbackLimits(read_seconds=0.1, concurrent=1))
    app.state.outbound.providers['twilio'] = TwilioCalls(CFG)
    entered = asyncio.Event()

    async def stalled():
        entered.set()
        await asyncio.Event().wait()

    try:
        task = asyncio.create_task(invoke(app, receive=stalled))
        await entered.wait()
        assert await invoke(app) == 429
        # A slow callback does not block owner reads or share its admission budget.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://testserver') as c:
            assert (await c.get('/api/calls', headers=owner_headers(tmp_path))).status_code == 200
        assert await task == 408
        assert await invoke(app) == 200
        entered.clear()
        task = asyncio.create_task(invoke(app, receive=stalled)); await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await invoke(app) == 200

        async def disconnected():
            return {'type': 'http.disconnect'}
        assert await invoke(app, receive=disconnected) is None
        assert await invoke(app) == 200

        async def trickle():
            await asyncio.sleep(0.03)
            return {'type': 'http.request', 'body': b'x', 'more_body': True}
        assert await invoke(app, receive=trickle) == 408
        assert await invoke(app) == 200
    finally:
        app.state.service.close()


async def test_health_is_owner_only_and_skips_neighbours(application, tmp_path):
    async def unavailable(*args, **kwargs):
        raise AssertionError('health must not probe neighbours')
    application.state.archive.capabilities = unavailable
    async with httpx.AsyncClient(transport=httpx.ASGITransport(application), base_url='http://127.0.0.1') as c:
        assert (await c.get('/api/health')).status_code == 401
        token = (tmp_path / 'owner.token').read_text().strip()
        response = await c.get('/api/health', headers={'Authorization': 'Bearer ' + token})
        assert response.status_code == 200 and response.json() == {'ok': True}


@pytest.mark.parametrize('kind', ['symlink', 'mode', 'short'])
def test_unsafe_control_token_fails_closed(tmp_path, kind):
    from control import control_key
    path = tmp_path / 'control.token'
    if kind == 'symlink':
        target = tmp_path / 'elsewhere'
        target.write_text(secrets.token_urlsafe(32))
        os.chmod(target, 0o600)
        path.symlink_to(target)
    else:
        path.write_text('short' if kind == 'short' else secrets.token_urlsafe(32))
        os.chmod(path, 0o644 if kind == 'mode' else 0o600)
    with pytest.raises((OSError, ValueError)):
        control_key(tmp_path)


def test_control_token_created_private_and_stable(tmp_path):
    from control import control_key
    first = control_key(tmp_path)
    assert os.stat(tmp_path / 'control.token').st_mode & 0o777 == 0o600
    assert control_key(tmp_path) == first
