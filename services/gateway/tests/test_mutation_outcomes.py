import hashlib
import json

import httpx
from starlette.testclient import TestClient

from agent_gateway.app import create_app
from test_policy import config
from test_transport import rpc


def test_timeout_after_mutation_dispatch_is_unknown_and_never_retried(tmp_path):
    value = config()
    key = tmp_path / "calendar.key"
    key.write_text("backend-calendar-a")
    key.chmod(0o600)
    value["principals"][0]["credentials"]["calendar"]["key_file"] = str(key)
    value["principals"][0]["grants"]["calendar"] = ["plan:execute"]
    value["profiles"]["reader"]["tools"] = ["calendar_apply"]
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    attempts = []

    def handler(request):
        if request.url.path == "/v1/capabilities":
            return httpx.Response(200, json={"permissions": ["plan:execute"]})
        attempts.append(json.loads(request.content))
        raise httpx.ReadTimeout("synthetic ambiguous receipt")

    app = create_app(path, "http://testserver", transports={"calendar": httpx.MockTransport(handler)})
    with TestClient(app) as client:
        args = {"plan_id": "synthetic-plan", "request_id": "synthetic-request"}
        response = client.post("/v1/calendar_apply", json=args, headers={"Authorization": "Bearer gateway-a"})
        assert response.status_code == 503
        assert response.json() == {
            "schema_version": 1,
            "error": "mutation_outcome_unknown",
            "outcome": "unknown",
            "automatic_retry": False,
            "reason": "gateway_timeout",
        }
        assert attempts == [args]
        value["revision"] += 1
        value["principals"][0]["grants"]["calendar"] = []
        path.write_text(json.dumps(value))
        assert (
            client.post(
                "/v1/calendar_apply", json=args, headers={"Authorization": "Bearer gateway-a"}
            ).status_code
            == 403
        )
        assert attempts == [args]


def test_operation_field_errors_relayed(tmp_path):
    value = config()
    value["deployment"] = {"modules": ["messaging"], "backends": {"messaging": {"url": "http://backend.test"}}}
    value["profiles"]["reader"] = {"tools": ["messages_preview_messenger_operation"],
                                   "actions": {"messenger_operations": ["create_group"]}}
    agent = value["principals"][0]
    agent["grants"] = {"messaging": ["groups-manage"]}
    agent["actions"] = {"messenger_operations": ["create_group"]}
    key = tmp_path / "backend.key"
    key.write_text("backend-synthetic")
    key.chmod(0o600)
    agent["credentials"] = {"messaging": {"key_file": str(key),
                                          "key_sha256": hashlib.sha256(b"backend-synthetic").hexdigest()}}
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    field_errors = [{"field": "user_ids/0", "reason": "format",
                     "expected": "Telegram user ID as a decimal string of 1-19 digits"},
                    {"field": "title", "reason": "required"}]
    seen = []

    def handler(request):
        if request.url.path == "/v1/capabilities":
            return httpx.Response(200, json={"permissions": ["groups-manage"], "messenger_operations": {
                "supported_by_account": {"synthetic": {"operations": ["create_group"]}}}})
        seen.append(json.loads(request.content))
        return httpx.Response(400, json={"error": "invalid_operation_parameters", "request_id": "b" * 32,
                                         "retryable": False, "next_action": "check_parameters",
                                         "field_errors": field_errors})

    args = {"account": "synthetic", "operation": "create_group", "parameters": {"user_ids": ["person_abc"]}}
    app = create_app(path, "http://testserver", transports={"messaging": httpx.MockTransport(handler)})
    with TestClient(app) as client:
        rest = client.post("/v1/messages_preview_messenger_operation", json=args,
                           headers={"Authorization": "Bearer gateway-a"})
        mcp = rpc(client, "tools/call", {"name": "messages_preview_messenger_operation",
                                         "arguments": args}).json()["result"]
    assert rest.status_code == 400 and mcp["isError"]
    assert rest.json() == mcp["structuredContent"]
    assert rest.json()["error"] == "invalid_operation_parameters"
    assert rest.json()["field_errors"] == field_errors
    assert rest.json()["next_action"] == "check_parameters"
    assert len(seen) == 2 and seen[0] == seen[1]
