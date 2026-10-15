"""The phone-only gateway uses the real calls ACL without archive/provider sessions."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tests/platform"))
from runtime_helpers import call, gateway, port, server
from app import create_app
from test_agent_api import registry
from test_tasks import Archive, BRIEF


def test_gateway_scoped_phone_preparation_needs_no_messaging(tmp_path):
    path, _, _ = registry(tmp_path)
    selected_port = port()
    base = f"http://127.0.0.1:{selected_port}"
    app = create_app(
        tmp_path / "data",
        Archive(),
        control_token="synthetic-owner",
        agent_registry=path,
    )
    tools = [
        "calls_" + name
        for name in [
            "capabilities",
            "prepare_external_call",
            "preflight_call",
            "get_call_status",
            "get_call_result",
        ]
    ]
    with (
        server(app, selected_port),
        gateway(
            tmp_path,
            {"calls": (base, "alice")},
            {"calls": ["calls:prepare", "calls:read"]},
            ["platform_capabilities", *tools],
        ) as (c, _, _),
    ):
        caps = call(c, "platform_capabilities", {})
        assert caps["modules"] == ["calls"] and caps["backend_states"] == {
            "calls": "ready"
        }
        body = {
            **BRIEF,
            "source_message_ids": [],
            "channel": "phone",
            "account": "twilio",
            "destination": "+12025550123",
        }
        brief = call(c, "calls_prepare_external_call", {"brief": body})
        assert (
            brief["owner_principal"] == "alice" and app.state.service.list_calls() == []
        )
        result = c.post("/v1/calls_preflight_call", json={"brief_id": brief["id"]})
        assert (
            result.status_code == 409 and result.json()["error"] == "calling_disabled"
        )
        assert (
            c.post(
                "/v1/calls_dial_call",
                json={"brief_id": brief["id"], "authorization_reference": "synthetic"},
            ).status_code
            == 403
        )
        requested = c.post(
            "/v1/calls_prepare_external_call",
            json={"brief": {**body, "source_message_ids": ["unavailable"]}},
        )
        assert (
            requested.status_code == 403
            and requested.json()["error"] == "scoped_archive_not_configured"
        )
        assert app.state.service.list_calls() == []
