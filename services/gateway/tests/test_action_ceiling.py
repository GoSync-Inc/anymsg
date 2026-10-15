import hashlib
import json

import httpx
from starlette.testclient import TestClient

from agent_gateway.app import create_app
from test_policy import config
from test_transport import rpc


def setup(tmp_path):
    value = config()
    value["deployment"] = {
        "modules": ["messaging"],
        "backends": {"messaging": {"url": "http://backend.test"}},
    }
    names = [
        "list_messenger_operations",
        "preview_messenger_operation",
        "execute_messenger_operation",
        "get_messenger_operation",
    ]
    value["profiles"]["reader"] = {
        "tools": ["messages_" + n for n in names],
        "actions": {"messenger_operations": ["rename_group"]},
    }
    agent = value["principals"][0]
    agent["grants"] = {"messaging": ["groups-manage"]}
    agent["actions"] = {"messenger_operations": ["rename_group", "leave_group"]}
    backend_key = tmp_path / "backend.key"
    backend_key.write_text("backend-synthetic")
    backend_key.chmod(0o600)
    agent["credentials"] = {
        "messaging": {
            "key_file": str(backend_key),
            "key_sha256": hashlib.sha256(b"backend-synthetic").hexdigest(),
        }
    }
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    seen = []

    def handler(request):
        if request.url.path == "/v1/capabilities":
            return httpx.Response(200, json={"permissions": ["groups-manage"]})
        args = json.loads(request.content)
        seen.append((request.url.path, args))
        if request.url.path.endswith("/list_messenger_operations"):
            return httpx.Response(
                200,
                json={
                    "items": [
                        {"operation": n, "available": True}
                        for n in ["rename_group", "leave_group", "remove_participant"]
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "operation": args.get("operation", "leave_group"),
                "preview_digest": "unchanged",
                "status": "queued",
                "operation_id": "synthetic-id",
            },
        )

    return (
        create_app(path, "http://testserver", transports={"messaging": httpx.MockTransport(handler)}),
        value,
        path,
        seen,
    )


def test_only_exact_actions_are_discovered_and_admitted_in_both_transports(tmp_path):
    app, value, path, seen = setup(tmp_path)
    headers = {"Authorization": "Bearer gateway-a"}
    with TestClient(app) as c:
        tools = rpc(c, "tools/list").json()["result"]["tools"]
        for tool in tools:
            if "operation" in tool["inputSchema"]["properties"]:
                assert tool["inputSchema"]["properties"]["operation"]["enum"] == ["rename_group"]
        listed = c.post(
            "/v1/messages_list_messenger_operations", json={"account": "synthetic"}, headers=headers
        ).json()
        assert [x["operation"] for x in listed["items"]] == ["rename_group"]
        for operation in ["leave_group", "remove_participant", "rename_group ", "Rename_Group"]:
            args = {"account": "synthetic", "operation": operation, "parameters": {"title": "Synthetic"}}
            count = len(seen)
            assert (
                c.post("/v1/messages_preview_messenger_operation", json=args, headers=headers).status_code
                == 403
            )
            assert rpc(
                c, "tools/call", {"name": "messages_preview_messenger_operation", "arguments": args}
            ).json()["result"]["isError"]
            assert len(seen) == count
        args = {"account": "synthetic", "operation": "rename_group", "parameters": {"title": "Synthetic"}}
        result = c.post("/v1/messages_preview_messenger_operation", json=args, headers=headers)
        assert result.status_code == 200 and result.json()["preview_digest"] == "unchanged"
        execute = {**args, "request_id": "synthetic-request", "preview_digest": "unchanged"}
        assert (
            c.post("/v1/messages_execute_messenger_operation", json=execute, headers=headers).status_code
            == 200
        )
        assert seen[-1][1]["preview_digest"] == "unchanged"
        # Receipts for an action outside this ceiling are not disclosed.
        assert (
            c.post(
                "/v1/messages_get_messenger_operation",
                json={"operation_id": "foreign-action"},
                headers=headers,
            ).status_code
            == 403
        )
        value["revision"] += 1
        value["principals"][0]["actions"] = {}
        path.write_text(json.dumps(value))
        count = len(seen)
        assert (
            c.post("/v1/messages_execute_messenger_operation", json=execute, headers=headers).status_code
            == 403
        )
        assert rpc(c, "tools/list").json()["result"]["tools"] == []
        assert len(seen) == count
