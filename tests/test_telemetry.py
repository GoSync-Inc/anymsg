"""Synthetic privacy, delivery isolation and gateway instrumentation checks."""
import builtins
import json
import threading
import time

import pytest
from starlette.testclient import TestClient
from tiden_telemetry import Client

from messaging_service import telemetry
from messaging_service.app import create_app
from messaging_service.store import APIError

SECRET = 'synthetic-private-token phone=+123456 chat=secret passport=example'


class Transport:
    def __init__(self):
        self.envelopes = []
        self.ready = threading.Event()

    def send(self, envelope):
        self.envelopes.append(envelope)
        self.ready.set()


def error():
    try:
        # A public code location, but exception text and locals contain private data.
        exec(compile('raise RuntimeError(secret)', str(telemetry.ROOT / 'app.py'), 'exec'),
             {'secret': SECRET})
    except RuntimeError as exc:
        return exc


def reporter(transport, component='gateway'):
    client = Client('https://synthetic-key@ingest.example.test/synthetic-project',
                    before_send=telemetry.before_send, transport=transport)
    return telemetry.Reporter(client, component, 'synthetic')


def test_sdk_wire_has_only_allowlisted_diagnostics():
    transport = Transport()
    worker = reporter(transport)
    try:
        assert worker.capture(error(), 'request_failed')
        assert transport.ready.wait(1)
    finally:
        worker.close()
    raw = transport.envelopes[0].decode()
    event = json.loads(raw.splitlines()[2])
    assert SECRET not in raw and 'synthetic-key' not in raw
    assert 'abs_path' not in raw and 'locals' not in raw and 'request' not in event
    assert event['exception']['values'][0]['stacktrace']['frames'] == [
        {'filename': 'messaging_service/app.py', 'lineno': 1, 'in_app': True}]
    assert event['tags'] == {'component': 'gateway', 'error_code': 'request_failed'}
    assert event['environment'] == 'synthetic'


def test_exception_stringification_is_never_called():
    class DangerousError(Exception):
        def __str__(self):
            raise AssertionError('must not stringify')
    event = telemetry.safe_event(DangerousError(SECRET), 'gateway', 'request_failed')
    assert event['exception']['values'][0]['type'] == 'UnexpectedError'
    assert SECRET not in json.dumps(event)


def test_before_send_removes_nested_extras():
    event = telemetry.safe_event(error(), 'gateway', 'request_failed')
    event.update(event_id='a'*32, timestamp=1, environment='synthetic', request={'body':SECRET},
                 breadcrumbs=[SECRET], user={'email':SECRET}, sdk={'secret':SECRET})
    event['tags']['account'] = SECRET
    value = event['exception']['values'][0]
    value.update(value=SECRET, secret=SECRET)
    value['stacktrace']['frames'][0].update(abs_path=SECRET, vars={'secret':SECRET})
    event['exception']['values'].append({'value': SECRET})
    result = telemetry.before_send(event)
    assert result is not None and SECRET not in json.dumps(result)


@pytest.mark.parametrize('field,value', [('tags', {'component':SECRET,'error_code':'request_failed'}),
    ('environment',SECRET), ('event_id',SECRET), ('timestamp',float('nan')), ('exception', {})])
def test_invalid_events_fail_closed(field, value):
    event = telemetry.safe_event(error(), 'gateway', 'request_failed')
    event.update(event_id='b'*32, timestamp=1, environment='synthetic')
    event[field] = value
    assert telemetry.before_send(event) is None


def test_blocked_network_never_blocks_capture_and_close_is_bounded():
    class SlowTransport(Transport):
        release = threading.Event()
        def send(self, envelope):
            self.ready.set()
            self.release.wait(5)
    transport = SlowTransport()
    worker = reporter(transport)
    try:
        start = time.monotonic()
        assert worker.capture(error(), 'request_failed')
        assert time.monotonic() - start < 0.1
        assert transport.ready.wait(1)
        # A second independent event is accepted while HTTP is stalled.
        start = time.monotonic()
        assert worker.capture(error(), 'tool_failed')
        assert time.monotonic() - start < 0.1
        worker.close()
        assert worker.thread.is_alive()
        assert not worker.capture(error(), 'indexer_failed')
    finally:
        transport.release.set()
        worker.thread.join(timeout=2)
    assert not worker.thread.is_alive()


def test_deduplication_and_rate_budget():
    transport = Transport()
    worker = reporter(transport)
    try:
        assert worker.capture(error(), 'request_failed')
        assert not worker.capture(error(), 'request_failed')
        with worker.lock:
            worker.count = 20
        assert not worker.capture(error(), 'tool_failed')
        assert worker.queue.maxsize == 32
        assert not worker.capture(error(), SECRET)
    finally:
        worker.close()


def test_disabled_configuration_has_no_import_or_thread(monkeypatch):
    monkeypatch.setattr(telemetry, '_reporter', None)
    monkeypatch.delenv('ANYMSG_TIDEN_ENABLED', raising=False)
    previous = builtins.__import__
    def guarded(name, *args, **kwargs):
        assert name != 'tiden_telemetry'
        return previous(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded)
    assert not telemetry.configure('gateway')
    assert telemetry._reporter is None


@pytest.mark.parametrize('dsn', ['http://key@example.test/p', 'https://example.test/p',
                                'https://key:password@example.test/p'])
def test_invalid_dsn_does_not_crash_or_expose_configuration(dsn, tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(telemetry, '_reporter', None)
    filename = tmp_path/'test.dsn'
    filename.write_text(dsn)
    monkeypatch.setenv('ANYMSG_TIDEN_ENABLED', '1')
    monkeypatch.setenv('ANYMSG_TIDEN_DSN_FILE', str(filename))
    assert not telemetry.configure('gateway')
    assert dsn not in caplog.text
    assert 'telemetry_configuration_unavailable' in caplog.text


@pytest.mark.parametrize('channel', ['rest','mcp'])
@pytest.mark.parametrize('unexpected', [True,False])
def test_gateway_reports_unexpected_errors_only(channel, unexpected, monkeypatch):
    class Store:
        public_url = 'http://testserver'
        def authenticate(self, token): return 'synthetic'
        def audit_boundary(self, code): pass
        def capabilities(self, token):
            if unexpected:
                raise RuntimeError(SECRET)
            raise APIError('not_found',404)
    transport = Transport()
    worker = reporter(transport)
    monkeypatch.setattr(telemetry, '_reporter', worker)
    try:
        with TestClient(create_app(Store(), ['testserver'])) as client:
            if channel == 'rest':
                response = client.post('/v1/capabilities',json={},headers={'Authorization':'Bearer synthetic'})
                # Preserve Starlette's already-started generic 500 response.
                assert response.status_code == (500 if unexpected else 404)
            else:
                response = client.post('/mcp',json={'jsonrpc':'2.0','id':1,'method':'tools/call',
                    'params':{'name':'capabilities','arguments':{}}},headers={
                        'Authorization':'Bearer synthetic','Accept':'application/json, text/event-stream'})
                assert response.json()['result']['isError']
            assert SECRET not in response.text
    finally:
        worker.close()
    assert bool(transport.envelopes) == unexpected
    assert SECRET not in repr(transport.envelopes)
