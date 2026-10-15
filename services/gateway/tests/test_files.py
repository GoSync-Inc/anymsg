import hashlib
import json
import time
from urllib.parse import urlsplit

import httpx
from starlette.testclient import TestClient

from agent_gateway.app import create_app
from test_policy import config


def setup(tmp_path, *, ttl=120, source="https://archive.example.test", redirect=False):
    path = tmp_path / "policy.json"
    key = tmp_path / "backend.key"
    key.write_text("backend-messaging-a")
    key.chmod(0o600)
    value = config()
    value["deployment"] = {
        "modules": ["messaging"],
        "backends": {
            "messaging": {
                "url": "http://messaging-api:8080",
                "download_origin": "https://archive.example.test",
            }
        },
    }
    value["profiles"]["reader"]["tools"] = ["messages_get_attachment", "platform_capabilities"]
    value["principals"][0]["grants"] = {"messaging": ["read", "download"]}
    value["principals"][0]["credentials"] = {
        "messaging": {"key_file": str(key), "key_sha256": hashlib.sha256(key.read_bytes()).hexdigest()}
    }
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    data = b"synthetic original\x00" * 10000
    requests = []

    def handler(request):
        if request.url.path == "/v1/capabilities":
            return httpx.Response(200, json={"permissions": ["read", "download"]})
        requests.append(request)
        if request.url.path == "/v1/get_attachment":
            return httpx.Response(
                200,
                json={
                    "id": "file-a",
                    "filename": "fixture.bin",
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "download_url": source + "/v1/download/" + "a" * 43,
                    "download_expires_in": ttl,
                },
            )
        assert request.url.host == "messaging-api"
        assert request.url.path == "/v1/download/" + "a" * 43
        return (
            httpx.Response(302, headers={"location": "https://foreign.invalid"})
            if redirect
            else httpx.Response(200, stream=httpx.ByteStream(data))
        )

    app = create_app(path, "http://testserver", transports={"messaging": httpx.MockTransport(handler)})
    return app, path, data, requests


def issue(client):
    return client.post(
        "/v1/messages_get_attachment",
        json={"attachment_id": "file-a"},
        headers={"Authorization": "Bearer gateway-a"},
    )


def test_ticket_streams_original_bytes_and_retains_integrity_metadata(tmp_path):
    app, path, data, requests = setup(tmp_path)
    with TestClient(app) as client:
        result = issue(client).json()
        assert urlsplit(result["download_url"]).netloc == "testserver"
        assert "a" * 43 not in result["download_url"]
        response = client.get(result["download_url"])
        assert response.content == data
        assert hashlib.sha256(response.content).hexdigest() == result["sha256"]
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["content-disposition"].startswith("attachment;")
        assert response.headers["content-type"] == "application/octet-stream"
    assert app.state.clients.closed
    assert len(requests) == 2


def test_ticket_revocation_is_checked_without_a_gateway_bearer_header(tmp_path):
    app, path, _, requests = setup(tmp_path)
    with TestClient(app) as client:
        url = issue(client).json()["download_url"]
        value = json.loads(path.read_text())
        value["revision"] += 1
        value["principals"][0]["enabled"] = False
        path.write_text(json.dumps(value))
        assert client.get(url).status_code == 404
    assert len(requests) == 1


def test_expired_ticket_and_range_do_not_contact_backend(tmp_path):
    app, _, _, requests = setup(tmp_path)
    with TestClient(app) as client:
        url = issue(client).json()["download_url"]
        assert client.get(url, headers={"Range": "bytes=0-1"}).status_code == 416
        for row in app.state.tickets.entries.values():
            row["expires"] = time.time() - 1
        assert client.get(url).status_code == 404
    assert len(requests) == 1


def test_unreviewed_download_origin_and_redirect_never_followed(tmp_path):
    app, _, _, requests = setup(tmp_path, source="https://foreign.invalid")
    with TestClient(app) as client:
        assert issue(client).status_code == 503
    assert len(requests) == 1
    app, _, _, requests = setup(tmp_path, redirect=True)
    with TestClient(app) as client:
        assert client.get(issue(client).json()["download_url"]).status_code == 503
    assert len(requests) == 2
