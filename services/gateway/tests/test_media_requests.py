"""Original requests are explicit local work and preserve the download ceiling."""
import hashlib
import json

import httpx
import pytest
from starlette.testclient import TestClient

from agent_gateway.app import create_app
from agent_gateway.catalog import CATALOG, arguments
from test_policy import config


NAME = 'messages_request_file_download'


def setup(tmp_path, *, selected=True, local_download=True, backend_download=True, supported=True):
    value = config()
    key = tmp_path / 'messaging.key'
    key.write_text('synthetic-backend')
    key.chmod(0o600)
    value['deployment'] = {'modules': ['messaging'],
        'backends': {'messaging': {'url': 'http://messaging-api:8080'}}}
    value['profiles']['reader']['tools'] = ['platform_capabilities'] + ([NAME] if selected else [])
    value['principals'][0]['grants'] = {'messaging': ['read'] + (['download'] if local_download else [])}
    value['principals'][0]['credentials'] = {'messaging': {'key_file': str(key),
        'key_sha256': hashlib.sha256(key.read_bytes()).hexdigest()}}
    path = tmp_path / 'policy.json'
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    seen = []

    def handler(request):
        if request.url.path == '/v1/capabilities':
            return httpx.Response(200, json={
                'permissions': ['read'] + (['download'] if backend_download else []),
                'tool_manifest': {'names': ['request_file_download'] if supported else []}})
        seen.append(request)
        assert request.url.path == '/v1/request_file_download'
        return httpx.Response(200, json={'attachment_id': 'file_synthetic',
            'request_status': 'requested', 'download_status': 'queued', 'next_action': 'get_attachment'})

    return create_app(path, 'http://testserver',
        transports={'messaging': httpx.MockTransport(handler)}), seen


def request(client, args):
    return client.post('/v1/' + NAME, json=args,
        headers={'Authorization': 'Bearer gateway-a'})


def test_gateway_original_request_requires_explicit_selection_and_manifest(tmp_path):
    app, seen = setup(tmp_path)
    with TestClient(app) as client:
        result = request(client, {'attachment_id': 'file_synthetic'})
        assert result.status_code == 200 and result.json()['next_action'] == 'get_attachment'
        assert result.json()['download_status'] == 'queued' and 'download_url' not in result.json()
    assert len(seen) == 1 and json.loads(seen[0].content) == {'attachment_id': 'file_synthetic'}
    assert CATALOG[NAME]['annotations'] == {'readOnlyHint': False, 'destructiveHint': False,
        'idempotentHint': True, 'openWorldHint': False}


@pytest.mark.parametrize('missing', ['selected', 'local_download', 'backend_download', 'supported'])
def test_gateway_original_request_never_adopts_missing_grant_or_implementation(tmp_path, missing):
    app, seen = setup(tmp_path, **{missing: False})
    with TestClient(app) as client:
        assert request(client, {'attachment_id': 'file_synthetic'}).status_code == 403
    assert seen == []


@pytest.mark.parametrize('args', [{}, {'attachment_id': ''}, {'attachment_id': 'x' * 257},
    {'attachment_id': 7}, {'attachment_id': True}, {'attachment_id': None},
    {'attachment_id': 'file_synthetic', 'request_id': 'unaccepted'}])
def test_gateway_original_request_strict_schema(args):
    with pytest.raises(ValueError, match='invalid_arguments'):
        arguments(CATALOG[NAME], args)
