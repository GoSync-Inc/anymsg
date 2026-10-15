"""Explicit original retrieval adapters; synthetic stores and HTTP doubles only."""
import json
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from anymsg import Client
from messaging_service.app import create_app


class Store:
    public_url = 'http://testserver'

    def __init__(self):
        self.calls = []

    def authenticate(self, token):
        assert token == 'synthetic-token'
        return 'synthetic-agent'

    def audit_boundary(self, code):
        pass

    def request_file_download(self, token, attachment_id):
        self.calls.append((token, attachment_id))
        return {'attachment_id': attachment_id, 'request_status': 'requested',
            'download_status': 'queued', 'next_action': 'get_attachment'}


def rpc(client, name, arguments=None, method='tools/call'):
    return client.post('/mcp', headers={'Authorization': 'Bearer synthetic-token',
        'Accept': 'application/json, text/event-stream'}, json={
        'jsonrpc': '2.0', 'id': 7, 'method': method,
        'params': {'name': name, 'arguments': arguments} if method == 'tools/call' else {}})


def test_request_download_rest_and_mcp_schedule_once_per_explicit_invocation():
    store = Store()
    with TestClient(create_app(store, ['testserver'])) as client:
        args = {'attachment_id': 'file_synthetic'}
        rest = client.post('/v1/request_file_download', json=args,
            headers={'Authorization': 'Bearer synthetic-token'})
        mcp = rpc(client, 'request_file_download', args)
        assert rest.status_code == 200
        assert mcp.json()['result']['structuredContent'] == rest.json()
        assert rest.json()['next_action'] == 'get_attachment'
    assert store.calls == [('synthetic-token', 'file_synthetic')] * 2


@pytest.mark.parametrize('arguments', [{}, {'attachment_id': ''}, {'attachment_id': 'x' * 257},
    {'attachment_id': 7}, {'attachment_id': True}, {'attachment_id': None},
    {'attachment_id': 'file_synthetic', 'request_id': 'unaccepted'}])
def test_request_download_strict_envelope_before_store(arguments):
    store = Store()
    with TestClient(create_app(store, ['testserver'])) as client:
        rest = client.post('/v1/request_file_download', json=arguments,
            headers={'Authorization': 'Bearer synthetic-token'})
        mcp = rpc(client, 'request_file_download', arguments)
    assert rest.status_code == 400 and rest.json()['error'] == 'invalid_parameters'
    assert mcp.json()['error']['code'] == -32602
    assert store.calls == []


def test_download_request_advertises_local_idempotent_mutation_and_bounded_id():
    with TestClient(create_app(Store(), ['testserver'])) as client:
        listed = rpc(client, '', method='tools/list').json()['result']['tools']
    tool = next(row for row in listed if row['name'] == 'request_file_download')
    assert tool['annotations'] == {'readOnlyHint': False, 'destructiveHint': False,
        'idempotentHint': True, 'openWorldHint': False}
    assert tool['inputSchema']['properties']['attachment_id']['minLength'] == 1
    assert tool['inputSchema']['properties']['attachment_id']['maxLength'] == 256
    assert tool['inputSchema']['additionalProperties'] is False
    packaged = json.loads((Path(__file__).resolve().parents[1] /
        'services/gateway/src/agent_gateway/catalog.json').read_text())
    routed = next(row for row in packaged if row['name'] == 'messages_request_file_download')
    assert routed['inputSchema'] == tool['inputSchema']
    assert routed['annotations'] == tool['annotations']
    assert routed['scopes'] == ['download'] and routed['required_manifest'] is True


def test_python_download_request_sends_only_existing_reference_without_retry():
    seen = []
    def handler(request):
        seen.append(request)
        raise httpx.ReadTimeout('synthetic timeout')
    with Client('https://archive.example', 'synthetic-token',
            transport=httpx.MockTransport(handler)) as client:
        from anymsg import AnyMsgError
        with pytest.raises(AnyMsgError, match='request_failed'):
            client.request_file_download(attachment_id='file_synthetic')
    assert len(seen) == 1
    assert str(seen[0].url).endswith('/v1/request_file_download')
    assert json.loads(seen[0].content) == {'attachment_id': 'file_synthetic'}


@pytest.mark.parametrize('arguments', [{}, {'attachment_id': ''}, {'attachment_id': 'x' * 257},
    {'attachment_id': 7}, {'attachment_id': True}, {'attachment_id': None},
    {'attachment_id': 'file_synthetic', 'request_id': 'unaccepted'}])
def test_python_request_download_rejects_invalid_envelope_before_transport(arguments):
    def forbidden(_):
        raise AssertionError('invalid request must not leave the client')
    with Client('https://archive.example', 'synthetic-token',
            transport=httpx.MockTransport(forbidden)) as client:
        with pytest.raises(ValueError, match='invalid_parameters'):
            client.call('request_file_download', arguments)


def test_python_request_download_limit_is_safe_and_not_retried():
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(429, json={'error': 'media_request_limit', 'detail': 'synthetic-private'})
    from anymsg import AnyMsgError
    with Client('https://archive.example', 'synthetic-token',
            transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AnyMsgError) as failure:
            client.request_file_download(attachment_id='file_synthetic')
    assert failure.value.code == 'media_request_limit'
    assert 'private' not in repr(failure.value) and len(seen) == 1
