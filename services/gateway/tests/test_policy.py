import hashlib
import json

import pytest

from agent_gateway.policy import Policy, Denied


def config():
    return {
        "version": 1,
        "revision": 1,
        "deployment": {
            "modules": ["calendar"],
            "backends": {"calendar": {"url": "http://calendar-api:13232"}},
        },
        "profiles": {
            "reader": {
                "tools": ["platform_capabilities", "calendar_calendars", "calendar_slots"],
                "actions": {},
            }
        },
        "principals": [
            {
                "id": "agent-a",
                "key_sha256": hashlib.sha256(b"gateway-a").hexdigest(),
                "profile": "reader",
                "enabled": True,
                "expires_at": "2099-01-01T00:00:00Z",
                "grants": {"calendar": ["agenda:read", "availability:read"]},
                "credentials": {
                    "calendar": {
                        "key_file": "/private/calendar-a.key",
                        "key_sha256": hashlib.sha256(b"backend-calendar-a").hexdigest(),
                    }
                },
                "actions": {},
            }
        ],
    }


@pytest.fixture
def policy(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(config()))
    path.chmod(0o600)
    return Policy(path)


def test_authentication_and_exact_tools(policy):
    snap, agent = policy.authenticate("gateway-a")
    assert agent["id"] == "agent-a"
    assert policy.visible(snap, agent) == ["platform_capabilities", "calendar_calendars", "calendar_slots"]
    for key in ("", "gateway-b", "Bearer gateway-a"):
        with pytest.raises(Denied):
            policy.authenticate(key)


def test_revocation_applies_without_reconnect(policy):
    policy.authenticate("gateway-a")
    value = config()
    value["revision"] = 2
    value["principals"][0]["enabled"] = False
    policy.path.write_text(json.dumps(value))
    with pytest.raises(Denied):
        policy.authenticate("gateway-a")


def test_partial_or_reused_revision_fails_closed(policy):
    policy.authenticate("gateway-a")
    value = config()
    value["profiles"]["reader"]["tools"] = ["platform_capabilities"]
    policy.path.write_text(json.dumps(value))
    with pytest.raises(Denied):
        policy.authenticate("gateway-a")
    policy.path.write_text('{"revision":2')
    with pytest.raises(Denied):
        policy.authenticate("gateway-a")


def test_module_scope_and_exposure_all_required(policy):
    value = config()
    value["revision"] = 2
    value["principals"][0]["grants"]["calendar"] = ["agenda:read"]
    policy.path.write_text(json.dumps(value))
    snap, agent = policy.authenticate("gateway-a")
    assert "calendar_slots" not in policy.visible(snap, agent)
    value["revision"] = 3
    value["deployment"]["modules"] = []
    policy.path.write_text(json.dumps(value))
    snap, agent = policy.authenticate("gateway-a")
    assert policy.visible(snap, agent) == ["platform_capabilities"]


@pytest.mark.parametrize("name", ["*", "calendar_*", "execute", "unknown"])
def test_unknown_and_wildcard_exposure_rejected(policy, name):
    value = config()
    value["profiles"]["reader"]["tools"] = [name]
    policy.path.write_text(json.dumps(value))
    with pytest.raises(Denied):
        policy.authenticate("gateway-a")


def test_public_policy_and_symlink_rejected(policy, tmp_path):
    policy.path.chmod(0o644)
    with pytest.raises(Denied):
        policy.authenticate("gateway-a")
    policy.path.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(policy.path)
    with pytest.raises(Denied):
        Policy(link).authenticate("gateway-a")
