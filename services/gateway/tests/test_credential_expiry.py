"""The key holder sees its own gateway key expiry and is warned 7 days ahead."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging

import httpx
import pytest
from starlette.testclient import TestClient

from agent_gateway import cli, dispatch, policy
from agent_gateway.app import create_app
from test_policy import config
from test_transport import rpc

NOW = datetime.now(timezone.utc).replace(microsecond=0)
HEADERS = {"Authorization": "Bearer gateway-a"}
SEARCH = {"items": [{"message_id": "synthetic-1", "text": "Synthetic"}], "coverage": "unknown"}


def stamp(value):
    return value.isoformat().replace("+00:00", "Z")


@pytest.fixture
def frozen(monkeypatch):
    clock = {"now": NOW}
    monkeypatch.setattr(dispatch, "utcnow", lambda: clock["now"])
    return clock


def gateway(tmp_path, expires_at):
    value = config()
    value["deployment"] = {"modules": ["messaging"], "backends": {"messaging": {"url": "http://backend.test"}}}
    value["profiles"]["reader"]["tools"] = ["platform_capabilities", "messages_search"]
    key = tmp_path / "backend.key"
    key.write_text("synthetic-backend-key")
    key.chmod(0o600)
    principal = value["principals"][0]
    principal["expires_at"] = expires_at
    principal["grants"] = {"messaging": ["read"]}
    principal["credentials"] = {"messaging": {
        "key_file": str(key), "key_sha256": hashlib.sha256(key.read_bytes()).hexdigest(),
    }}
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(value))
    path.chmod(0o600)

    def backend(request):
        if request.url.path == "/v1/capabilities":
            return httpx.Response(200, json={"permissions": ["read"], "principal": "backend-a"})
        return httpx.Response(200, json=SEARCH)

    return create_app(path, "http://testserver", transports={"messaging": httpx.MockTransport(backend)}), path


def search(client):
    return rpc(client, "tools/call", {"name": "messages_search", "arguments": {"query": "synthetic"}}).json()


def test_capabilities_report_expiry(tmp_path, frozen):
    app, _ = gateway(tmp_path, stamp(NOW + timedelta(days=30)))
    with TestClient(app) as client:
        rest = client.post("/v1/platform_capabilities", json={}, headers=HEADERS)
        mcp = rpc(client, "tools/call", {"name": "platform_capabilities", "arguments": {}}).json()["result"]
    assert rest.status_code == 200
    assert rest.json()["credential"] == mcp["structuredContent"]["credential"] == {
        "expires_at": stamp(NOW + timedelta(days=30)),
        "expires_in_days": 30,
        "expiry_warning": False,
        "warning_window_days": 7,
        "next_action": "none",
    }


def test_capabilities_expiry_is_normalized_to_utc(tmp_path, frozen):
    local = (NOW + timedelta(days=30)).astimezone(timezone(timedelta(hours=3)))
    app, _ = gateway(tmp_path, local.isoformat())
    with TestClient(app) as client:
        credential = client.post("/v1/platform_capabilities", json={}, headers=HEADERS).json()["credential"]
    assert credential["expires_at"] == stamp(NOW + timedelta(days=30))


def test_warning_in_window(tmp_path, frozen):
    expires_at = stamp(NOW + timedelta(days=6))
    app, _ = gateway(tmp_path, expires_at)
    expected = {
        "expires_at": expires_at,
        "expires_in_days": 6,
        "expiry_warning": True,
        "warning_window_days": 7,
        "next_action": "ask_owner_to_extend_gateway_key",
    }
    with TestClient(app) as client:
        capabilities = client.post("/v1/platform_capabilities", json={}, headers=HEADERS).json()
        result = search(client)["result"]
    assert capabilities["credential"] == expected
    assert result["_meta"]["deedkit.credential"] == expected
    assert [item["type"] for item in result["content"]] == ["text", "text"]
    assert result["content"][1]["text"] == (
        f"DeedKit gateway key expires at {expires_at} (in 6 days). Ask the owner to extend it."
    )


def test_warning_starts_below_seven_full_days(tmp_path, frozen):
    app, _ = gateway(tmp_path, stamp(NOW + timedelta(days=7)))
    with TestClient(app) as client:
        assert client.post("/v1/platform_capabilities", json={}, headers=HEADERS).json()["credential"][
            "expiry_warning"] is False
        frozen["now"] = NOW + timedelta(seconds=1)
        credential = client.post("/v1/platform_capabilities", json={}, headers=HEADERS).json()["credential"]
    assert credential["expiry_warning"] is True and credential["expires_in_days"] == 6


def test_structured_content_unchanged(tmp_path, frozen):
    app, path = gateway(tmp_path, stamp(NOW + timedelta(days=30)))
    with TestClient(app) as client:
        outside = search(client)["result"]
        value = json.loads(path.read_text())
        value["revision"] = 2
        value["principals"][0]["expires_at"] = stamp(NOW + timedelta(days=6))
        path.write_text(json.dumps(value))
        inside = search(client)["result"]
    assert outside["structuredContent"] == SEARCH
    assert len(outside["content"]) == 1 and "_meta" not in outside
    assert inside["structuredContent"] == outside["structuredContent"] == SEARCH
    assert inside["content"][0] == outside["content"][0]
    assert not inside["isError"]


def test_errors_get_no_mcp_warning(tmp_path, frozen):
    app, _ = gateway(tmp_path, stamp(NOW + timedelta(days=2)))
    with TestClient(app) as client:
        denied = rpc(client, "tools/call", {"name": "messages_search", "arguments": {"limit": "x"}}).json()["result"]
    assert denied["isError"] and len(denied["content"]) == 1 and "_meta" not in denied


def test_rest_header_window_only(tmp_path, frozen):
    expires_at = stamp(NOW + timedelta(days=6))
    app, path = gateway(tmp_path, expires_at)
    with TestClient(app) as client:
        inside = client.post("/v1/messages_search", json={"query": "synthetic"}, headers=HEADERS)
        denied = client.post("/v1/messages_search", json={"limit": "x"}, headers=HEADERS)
        mcp = rpc(client, "tools/call", {"name": "messages_search", "arguments": {"query": "synthetic"}})
        value = json.loads(path.read_text())
        value["revision"] = 2
        value["principals"][0]["expires_at"] = stamp(NOW + timedelta(days=60))
        path.write_text(json.dumps(value))
        outside = client.post("/v1/messages_search", json={"query": "synthetic"}, headers=HEADERS)
        result = search(client)["result"]
    assert inside.headers["X-DeedKit-Key-Expires"] == expires_at
    assert inside.json() == SEARCH
    assert denied.status_code == 422 and denied.headers["X-DeedKit-Key-Expires"] == expires_at
    assert "X-DeedKit-Key-Expires" not in mcp.headers
    assert "X-DeedKit-Key-Expires" not in outside.headers
    assert outside.json() == SEARCH
    assert len(result["content"]) == 1 and "_meta" not in result


def test_check_lists_expiring(tmp_path, frozen, capsys):
    value = config()
    soon, later = value["principals"][0], dict(value["principals"][0])
    soon["expires_at"] = stamp(NOW + timedelta(days=3))
    later.update(id="agent-b", key_sha256=hashlib.sha256(b"gateway-b").hexdigest(),
                 expires_at=stamp(NOW + timedelta(days=60)))
    gone = dict(later, id="agent-c", key_sha256=hashlib.sha256(b"gateway-c").hexdigest(),
                expires_at=stamp(NOW - timedelta(days=1)))
    value["principals"] = [soon, later, gone]
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    cli.main(["--policy", str(path), "--public-url", "http://localhost:13240", "--check"])
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["valid"] is True
    assert report["expiring"] == [
        {"id": "agent-a", "expires_at": stamp(NOW + timedelta(days=3)), "expired": False},
        {"id": "agent-c", "expires_at": stamp(NOW - timedelta(days=1)), "expired": True},
    ]
    for agent in value["principals"]:
        assert agent["key_sha256"] not in output
    assert "key_file" not in output and "gateway-a" not in output


def test_running_gateway_logs_once_per_principal_per_day(tmp_path, frozen, caplog):
    app, path = gateway(tmp_path, stamp(NOW + timedelta(days=5)))
    caplog.set_level(logging.WARNING, logger="agent_gateway")
    with TestClient(app) as client:
        for _ in range(3):
            search(client)
        first = [r.getMessage() for r in caplog.records if r.name == "agent_gateway"]
        frozen["now"] = NOW + timedelta(days=1)
        search(client)
        client.post("/v1/messages_search", json={"query": "synthetic"}, headers=HEADERS)
    messages = [r.getMessage() for r in caplog.records if r.name == "agent_gateway"]
    assert first == ["gateway key for principal agent-a expires at " + stamp(NOW + timedelta(days=5))]
    assert len(messages) == 2
    secret = json.loads(path.read_text())["principals"][0]["key_sha256"]
    assert all(secret not in m and "gateway-a" not in m for m in messages)


def test_no_log_outside_window(tmp_path, frozen, caplog):
    app, _ = gateway(tmp_path, stamp(NOW + timedelta(days=30)))
    caplog.set_level(logging.WARNING, logger="agent_gateway")
    with TestClient(app) as client:
        search(client)
    assert not [r for r in caplog.records if r.name == "agent_gateway"]


def test_expired_key_gets_existing_unauthorized(tmp_path, monkeypatch):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(policy, "datetime", Frozen)
    monkeypatch.setattr(dispatch, "utcnow", lambda: NOW)
    app, path = gateway(tmp_path, stamp(NOW + timedelta(microseconds=1)))
    with TestClient(app) as client:
        assert client.post("/v1/messages_search", json={"query": "synthetic"}, headers=HEADERS).status_code == 200
        value = json.loads(path.read_text())
        value["revision"] = 2
        value["principals"][0]["expires_at"] = stamp(NOW)
        path.write_text(json.dumps(value))
        rest = client.post("/v1/messages_search", json={"query": "synthetic"}, headers=HEADERS)
        mcp = rpc(client, "tools/call", {"name": "messages_search", "arguments": {"query": "synthetic"}})
    assert rest.status_code == mcp.status_code == 401
    assert rest.json() == mcp.json() == {"error": "unauthorized"}
    assert "X-DeedKit-Key-Expires" not in rest.headers
