from starlette.testclient import TestClient
from tests.service.test_policy import SCOPES


def client(household, visibility="details"):
    from family_calendar.service.api import create_app

    household.grant("family", "adult-a", "cal-a", SCOPES, "details")
    key = household.agent("agent", "family", "adult-a", SCOPES, ["cal-a"], visibility)
    return TestClient(create_app(household, port=13232), base_url="http://127.0.0.1:13232"), key


def test_rest_and_mcp_return_identical_calendar_policy(household):
    c, key = client(household, "busy_only")
    with c:
        headers = {"Authorization": "Bearer " + key}
        rest = c.post("/v1/calendars", json={}, headers=headers).json()
        r = c.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "family_calendars", "arguments": {}},
            },
            headers={
                **headers,
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2025-06-18",
            },
        )
        assert r.status_code == 200
        assert r.json()["result"]["structuredContent"] == rest
        assert rest["calendars"][0]["visibility"] == "busy_only"


def test_agent_cannot_change_roles_or_create_credentials(household):
    c, key = client(household)
    with c:
        for route in ("/v1/admin", "/v1/agent-key", "/v1/connect"):
            assert c.post(route, json={}, headers={"Authorization": "Bearer " + key}).status_code == 404


def test_missing_auth_and_foreign_origin_are_rejected(household):
    c, key = client(household)
    with c:
        assert c.post("/v1/calendars", json={}).status_code == 401
        assert (
            c.post(
                "/v1/calendars",
                json={},
                headers={"Authorization": "Bearer " + key, "Origin": "https://evil.example"},
            ).status_code
            == 403
        )


def test_unknown_arguments_are_rejected_in_both_transports(household):
    c, key = client(household)
    with c:
        h = {"Authorization": "Bearer " + key}
        assert c.post("/v1/calendars", json={"extra": "secret"}, headers=h).status_code == 422
        r = c.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "family_calendars", "arguments": {"extra": "secret"}},
            },
            headers={
                **h,
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2025-06-18",
            },
        )
        assert r.json()["result"]["isError"] is True


def test_notification_stream_is_disabled(household):
    c, key = client(household)
    with c:
        assert c.get("/mcp", headers={"Authorization": "Bearer " + key}).status_code == 405
