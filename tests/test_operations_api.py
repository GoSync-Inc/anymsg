"""Strict messenger-operation boundaries and SDK transports; synthetic only."""
import json

import httpx
import pytest
from starlette.testclient import TestClient

from anymsg.client import AnyMsgError, Client
from messaging_service.app import create_app


METHODS = ('list_messenger_operations', 'preview_messenger_operation',
           'execute_messenger_operation', 'get_messenger_operation')


class RecordingStore:
    public_url = 'http://testserver'
    def __init__(self): self.calls = []
    def authenticate(self, token): return 'synthetic-principal'
    def audit_boundary(self, code): pass
    def _record(self, method, token, params):
        self.calls.append((method, params))
        return {'operation_id': 'op_synthetic', 'status': 'queued'}
    def list_messenger_operations(self, token, **params):
        return self._record(METHODS[0], token, params)
    def preview_messenger_operation(self, token, **params):
        return self._record(METHODS[1], token, params)
    def execute_messenger_operation(self, token, **params):
        return self._record(METHODS[2], token, params)
    def get_messenger_operation(self, token, **params):
        return self._record(METHODS[3], token, params)


def arguments(method):
    if method == METHODS[0]: return {'account': 'synthetic'}
    if method == METHODS[3]: return {'operation_id': 'op_synthetic'}
    value = dict(account='synthetic', operation='react', chat_id='chat_synthetic',
        message_id='msg_synthetic', expected_revision=3, parameters={'emoji': '👍'})
    if method == METHODS[2]: value.update(request_id='synthetic-action', preview_digest='a'*64)
    return value


def rpc(client, method, params):
    return client.post('/mcp', headers={'Authorization': 'Bearer synthetic-token',
        'Accept': 'application/json, text/event-stream'}, json={'jsonrpc': '2.0', 'id': 1,
        'method': 'tools/call', 'params': {'name': method, 'arguments': params}})


@pytest.mark.parametrize('method', METHODS)
def test_operation_rest_and_mcp_match_exact_payload(method):
    store = RecordingStore()
    params = arguments(method)
    with TestClient(create_app(store, ['testserver'])) as client:
        rest = client.post('/v1/'+method, headers={'Authorization': 'Bearer synthetic-token'}, json=params)
        mcp = rpc(client, method, params)
    assert rest.status_code == 200
    assert not mcp.json().get('error') and not mcp.json()['result'].get('isError')
    assert store.calls == [(method, params), (method, params)]


@pytest.mark.parametrize('method', METHODS)
def test_operation_arguments_reject_unknown_before_backend(method):
    store = RecordingStore()
    with TestClient(create_app(store, ['testserver'])) as client:
        params = {**arguments(method), 'approved': True}
        rest = client.post('/v1/'+method, headers={'Authorization': 'Bearer synthetic-token'}, json=params)
        mcp = rpc(client, method, params)
    assert rest.status_code == 400 and rest.json()['fields'] == ['approved']
    assert mcp.json()['error']['code'] == -32602
    assert store.calls == []


@pytest.mark.parametrize('method, field', [
    (METHODS[0], 'account'), (METHODS[1], 'account'), (METHODS[1], 'operation'),
    (METHODS[2], 'account'), (METHODS[2], 'operation'), (METHODS[2], 'request_id'),
    (METHODS[3], 'operation_id'),
])
def test_operation_required_fields_rejected_before_backend(method, field):
    store = RecordingStore()
    params = arguments(method)
    del params[field]
    with TestClient(create_app(store, ['testserver'])) as client:
        rest = client.post('/v1/'+method, headers={'Authorization': 'Bearer synthetic-token'}, json=params)
        mcp = rpc(client, method, params)
    assert rest.status_code == 400 and rest.json()['fields'] == [field]
    assert mcp.json()['error']['data']['fields'] == [field]
    assert store.calls == []


@pytest.mark.parametrize('extra', [
    {'account': 1}, {'operation': None}, {'chat_id': False}, {'message_id': 4},
    {'expected_revision': True}, {'expected_revision': '3'}, {'expected_revision': 1.5},
    {'parameters': None}, {'parameters': []}, {'request_id': 123}, {'preview_digest': None},
])
def test_operation_strict_types_before_backend(extra):
    store = RecordingStore()
    params = {**arguments(METHODS[2]), **extra}
    with TestClient(create_app(store, ['testserver'])) as client:
        rest = client.post('/v1/'+METHODS[2], headers={'Authorization': 'Bearer synthetic-token'}, json=params)
        mcp = rpc(client, METHODS[2], params)
    assert rest.status_code == 400 and rest.json()['error'] == 'invalid_parameters'
    assert mcp.json()['error']['code'] == -32602
    assert store.calls == []


def test_operation_default_payload_and_required_action_id():
    store = RecordingStore()
    with TestClient(create_app(store, ['testserver'])) as client:
        minimal = {'account': 'synthetic', 'operation': 'chat_info'}
        assert rpc(client, METHODS[1], minimal).json()['result']['isError'] is False
        denied = rpc(client, METHODS[2], minimal).json()
    assert denied['error']['data']['fields'] == ['request_id']
    assert store.calls == [(METHODS[1], {**minimal, 'chat_id': '', 'message_id': '',
        'expected_revision': 0, 'parameters': {}})]


def test_operation_manifest_has_30_actual_tools_and_truthful_annotations():
    store = RecordingStore()
    with TestClient(create_app(store, ['testserver'])) as client:
        response = client.post('/mcp', headers={'Authorization': 'Bearer synthetic-token',
            'Accept': 'application/json, text/event-stream'}, json={'jsonrpc': '2.0', 'id': 1,
            'method': 'tools/list', 'params': {}})
        initialize = client.post('/mcp', headers={'Authorization': 'Bearer synthetic-token',
            'Accept': 'application/json, text/event-stream'}, json={'jsonrpc': '2.0', 'id': 2,
            'method': 'initialize', 'params': {'protocolVersion': '2025-11-25',
            'capabilities': {}, 'clientInfo': {'name': 'synthetic', 'version': '1'}}})
    tools = {row['name']: row for row in response.json()['result']['tools']}
    assert len(tools) == 33 and set(METHODS) <= set(tools)
    for method in METHODS:
        assert tools[method]['inputSchema']['additionalProperties'] is False
        annotations = tools[method]['annotations']
        execution = method == METHODS[2]
        assert annotations['readOnlyHint'] is not execution
        assert annotations['openWorldHint'] is execution
        assert annotations['destructiveHint'] is execution
    assert tools[METHODS[2]]['annotations']['idempotentHint'] is True
    instructions = initialize.json()['result']['instructions']
    assert 'owner confirmation' in instructions and 'groups' in instructions
    assert 'contacts' in instructions and 'profile' in instructions
    assert 'not human approval' in instructions and 'submitted' in instructions


def test_python_operation_methods_preserve_exact_intent_without_retries():
    captured = []
    def transport(request):
        captured.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={'status': 'submitted', 'operation_id': 'op_synthetic'})
    with Client('https://archive.example/base', 'synthetic-token',
                transport=httpx.MockTransport(transport)) as client:
        for method in METHODS:
            assert getattr(client, method)(**arguments(method))['status'] == 'submitted'
    assert captured == [('/base/v1/'+method, arguments(method)) for method in METHODS]


@pytest.mark.parametrize('method', METHODS)
def test_python_generic_operation_call_forbids_unknown_outer_parameters(method):
    captured = []
    with Client('https://archive.example', 'synthetic-token',
                transport=httpx.MockTransport(lambda request: captured.append(request))) as client:
        with pytest.raises(ValueError, match='invalid_parameters'):
            client.call(method, {**arguments(method), 'approved': True})
    assert captured == []


@pytest.mark.parametrize('extra', [
    {'account': 1}, {'operation': None}, {'chat_id': False}, {'message_id': 4},
    {'expected_revision': True}, {'expected_revision': '3'}, {'expected_revision': -1},
    {'parameters': None}, {'parameters': []}, {'parameters': {1: 'not a string key'}},
    {'request_id': 123}, {'preview_digest': None},
])
def test_python_operations_validate_before_http(extra):
    captured = []
    with Client('https://archive.example', 'synthetic-token',
                transport=httpx.MockTransport(lambda request: captured.append(request))) as client:
        with pytest.raises(ValueError, match='invalid_parameters'):
            client.execute_messenger_operation(**{**arguments(METHODS[2]), **extra})
    assert captured == []


@pytest.mark.parametrize('code', ['unsupported_operation', 'invalid_operation_parameters',
    'operation_disabled', 'sender_not_ready', 'preview_required', 'preview_mismatch',
    'operation_target_changed', 'operation_access_changed', 'operation_result_unknown'])
def test_python_operation_errors_are_safe_and_never_retried(code):
    captured = []
    def transport(request):
        captured.append(request.url.path)
        return httpx.Response(400, json={'error': code, 'detail': 'synthetic-private-content'})
    with Client('https://archive.example', 'synthetic-token',
                transport=httpx.MockTransport(transport)) as client:
        with pytest.raises(AnyMsgError) as failure:
            client.execute_messenger_operation(**arguments(METHODS[2]))
    assert failure.value.code == code and 'synthetic-private-content' not in str(failure.value)
    assert len(captured) == 1
