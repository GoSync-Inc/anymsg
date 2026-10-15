"""The phone-only gateway uses the real calls ACL without archive/provider sessions."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests/platform"))
from runtime_helpers import call, gateway, port, server
from agent_api import ANNOTATIONS, DESCRIPTIONS, METHODS
from app import create_app
from test_agent_api import registry
from test_tasks import Archive, BRIEF


def test_gateway_catalog_mirrors_reviewed_calls_metadata():
    packaged = json.loads(
        (ROOT / "services/gateway/src/agent_gateway/catalog.json").read_text()
    )
    rows = {row["name"]: row for row in packaged if row["module"] == "calls"}
    assert set(rows) == {"calls_" + name for name in METHODS}
    for name, (model, scope) in METHODS.items():
        row = rows["calls_" + name]
        assert row["operation"] == name and row["scopes"] == ([scope] if scope else [])
        assert row["inputSchema"] == model.model_json_schema()
        assert row["description"] == DESCRIPTIONS[name]
        assert row["annotations"] == ANNOTATIONS[name]
        # Effect first: reads say so, everything else is never presented as a read.
        reads = scope in {"", "calls:read"}
        assert ANNOTATIONS[name]["readOnlyHint"] is reads
        assert DESCRIPTIONS[name].startswith("Read-only") is reads
    assert len(set(DESCRIPTIONS.values())) == len(METHODS)
    assert [n for n, a in ANNOTATIONS.items() if a["openWorldHint"]] == ["dial_call"]
    assert [n for n, a in ANNOTATIONS.items() if a["destructiveHint"]] == ["dial_call"]


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
            "list_briefs",
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
        listed = call(c, "calls_list_briefs", {"limit": 5})
        assert [row["id"] for row in listed["items"]] == [brief["id"]]
        assert listed["next_offset"] is None
        assert "+12025550123" not in json.dumps(listed)
        grok = call(c, "calls_prepare_external_call", {"brief": {**body,
            "dialog_engine":"grok_realtime", "grok_voice":"rex"}})
        assert grok["model"] == "grok-voice-3.0" and grok["grok_voice"] == "rex"
        readiness = call(c, "calls_capabilities", {})["calling_readiness"]
        assert readiness["channels"]["phone"]["provider_connection"] == "not_probed"
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
