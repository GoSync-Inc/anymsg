"""Formatting client transports use synthetic responses and never real providers."""
import json

import httpx
import pytest

from anymsg.client import AnyMsgError, Client


ERRORS = (
    'invalid_message_format', 'unsafe_message_markup', 'invalid_message_entities',
    'formatting_too_complex', 'unsupported_message_formatting',
    'lossy_formatting_requires_confirmation', 'message_too_long',
    'preview_required', 'preview_mismatch',
)


def test_python_preview_and_send_preserve_exact_formatting_contract():
    captured = []
    def transport(request):
        captured.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={'preview_digest': 'a'*64})
    source = dict(account='synthetic', chat_id='synthetic-chat', text='**Текст 😀**',
        reply_to='synthetic-message', format='markdown', link_preview=True, allow_lossy=False)
    with Client('https://archive.example/base', 'synthetic-token',
                transport=httpx.MockTransport(transport)) as client:
        assert client.preview_message(**source)['preview_digest'] == 'a'*64
        client.send_message(**source, request_id='synthetic-action', preview_digest='a'*64)
    assert captured == [('/base/v1/preview_message', source),
        ('/base/v1/send_message', {**source, 'request_id':'synthetic-action', 'preview_digest':'a'*64})]


def test_python_plain_preview_defaults_are_explicit_and_old_sends_stay_compatible():
    captured = []
    def transport(request):
        captured.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={})
    with Client('https://archive.example', 'synthetic-token',
                transport=httpx.MockTransport(transport)) as client:
        client.preview_message(account='synthetic', chat_id='synthetic-chat', text='Literal 5*3')
        client.send_message(account='synthetic', chat_id='synthetic-chat', text='Literal 5*3',
            request_id='synthetic-action')
    defaults = dict(account='synthetic', chat_id='synthetic-chat', text='Literal 5*3',
        reply_to='', format='plain', link_preview=False, allow_lossy=False)
    assert captured == [('/v1/preview_message', defaults),
        ('/v1/send_message', dict(account='synthetic',chat_id='synthetic-chat',text='Literal 5*3',
            reply_to='',request_id='synthetic-action'))]


@pytest.mark.parametrize('code', ERRORS)
def test_python_format_errors_are_safe_and_never_retried(code):
    calls = []
    def transport(request):
        calls.append(request.url.path)
        return httpx.Response(400, json={'error':code, 'detail':'synthetic-private-draft',
            'request_id':'b'*32})
    with Client('https://archive.example', 'synthetic-token',
                transport=httpx.MockTransport(transport)) as client:
        with pytest.raises(AnyMsgError) as failure:
            client.preview_message(account='synthetic', chat_id='synthetic-chat', text='draft')
    assert failure.value.code == code and failure.value.request_id == 'b'*32
    assert 'synthetic-private-draft' not in str(failure.value)
    assert calls == ['/v1/preview_message']
