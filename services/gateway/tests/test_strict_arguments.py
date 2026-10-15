"""Reject invalid JSON numbers before backend admission or ambiguous write reporting."""
import json

import httpx
import pytest
from starlette.testclient import TestClient

from agent_gateway.catalog import CATALOG, arguments
from test_diagnostics import configured, HEADERS


SEND = {"account": "account-1", "chat_id": "chat-1", "text": "Synthetic", "request_id": "request-1"}
ACK = {"subscription_id": "subscription-1", "receipt": "r" * 43, "version": 1}


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("name,args,field", [
    ("messages_send_message", SEND, "wait_s"),
    ("messages_wait_for_subscription_events", {"subscription_id": "subscription-1"}, "timeout"),
])
def test_wait_numbers_are_finite(name, args, field, value):
    with pytest.raises(ValueError, match="invalid_arguments"):
        arguments(CATALOG[name], {**args, field: value})


@pytest.mark.parametrize("name,values", [
    ("messages_ack_subscription_events", {**ACK, "version": 1.0}),
    ("messages_search", {"limit": 1.0}),
    ("messages_upload_file_chunk", {"upload_id": "up_" + "a" * 32, "offset": 0.0, "data_base64": "eA=="}),
])
def test_integer_schema_rejects_integral_floats(name, values):
    with pytest.raises(ValueError, match="invalid_arguments"):
        arguments(CATALOG[name], values)


def test_strict_integer_schema_applies_recursively_through_arrays_and_unions():
    tool = {"operation": "synthetic", "inputSchema": {
        "type": "object", "properties": {"rows": {"type": "array", "items": {
            "type": "object", "properties": {"version": {"anyOf": [{"type": "integer"}, {"type": "null"}]}},
        }}},
    }}
    assert arguments(tool, {"rows": [{"version": 1}, {"version": None}]})["rows"][0]["version"] == 1
    for invalid in (1.0, True):
        with pytest.raises(ValueError, match="invalid_arguments"):
            arguments(tool, {"rows": [{"version": invalid}]})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_numbers_are_rejected_inside_untyped_operation_parameters(value):
    values = {"account": "account-1", "operation": "rename_group", "parameters": {"nested": [{"value": value}]}}
    with pytest.raises(ValueError, match="invalid_arguments"):
        arguments(CATALOG["messages_preview_messenger_operation"], values)


@pytest.mark.parametrize("name,values", [
    ("messages_send_message", {**SEND, "wait_s": float("nan")}),
    ("messages_wait_for_subscription_events", {"subscription_id": "subscription-1", "timeout": float("nan")}),
    ("messages_ack_subscription_events", {**ACK, "version": 1.0}),
])
def test_invalid_numbers_never_reach_backend_capabilities_or_dispatch_over_either_transport(tmp_path, name, values):
    requests = []

    def backend(request):
        requests.append(request.url.path)
        if request.url.path == "/v1/capabilities":
            return httpx.Response(200, json={"permissions": ["read", "send", "subscription-ack"],
                                            "tool_manifest": {"names": [name.removeprefix("messages_")]}})
        return httpx.Response(200, json={"synthetic": True})

    app, _, _ = configured(tmp_path, backend, module="messaging", tools=[name],
                           grants=["read", "send", "subscription-ack"])
    headers = {**HEADERS, "Content-Type": "application/json"}
    with TestClient(app) as client:
        # Raw JSON represents the incoming wire edge; HTTPX's json= encoder
        # correctly refuses non-finite Python floats before transmission.
        rest = client.post("/v1/" + name, content=json.dumps(values), headers=headers)
        mcp = client.post("/mcp", content=json.dumps({"jsonrpc": "2.0", "id": 8, "method": "tools/call",
                          "params": {"name": name, "arguments": values}}),
                          headers={**headers, "Accept": "application/json, text/event-stream"})
    assert rest.status_code == 422 and rest.json() == {"error": "invalid_arguments"}
    result = mcp.json()["result"]
    assert result["isError"] and result["structuredContent"] == {"error": "invalid_arguments"}
    assert requests == []


@pytest.mark.parametrize("value", [0, 0.0, 0.5, 25, 25.0])
def test_number_fields_accept_finite_integers_and_floats_without_coercion(value):
    assert arguments(CATALOG["messages_send_message"], {**SEND, "wait_s": value})["wait_s"] == value
    assert arguments(CATALOG["messages_wait_for_subscription_events"],
                     {"subscription_id": "subscription-1", "timeout": value})["timeout"] == value
