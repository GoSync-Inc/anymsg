import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "deedkit_launcher", ROOT / "scripts/deedkit.py"
)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def profile(modules, connectors=(), external=()):
    return {
        "version": 1,
        "id": "synthetic",
        "deployment": {
            "modules": list(modules),
            "connectors": list(connectors),
            "external": list(external),
        },
        "exposure": {"tools": ["platform_capabilities"]},
    }


def names(plan):
    return {m["module"] for m in plan["modules"]}


def test_calendar_and_phone_do_not_require_messaging():
    assert names(launcher.resolve(profile(["calendar"]))) == {"gateway", "calendar"}
    assert names(launcher.resolve(profile(["calls"], ["calls-phone"]))) == {
        "gateway",
        "calls",
    }
    assert names(launcher.resolve(profile(["calls"], ["calls-telegram"]))) == {
        "gateway",
        "calls",
        "messaging",
    }
    assert names(launcher.resolve(profile(["calls"], ["calls-archive"]))) == {
        "gateway",
        "calls",
        "messaging",
    }


def test_external_backend_never_starts_or_demands_provider_credentials():
    plan = launcher.resolve(
        profile(["calendar", "messaging"], external=["calendar", "messaging"])
    )
    argv = launcher.commands(plan, "up")
    assert len(argv) == 1
    assert argv[0][-3:] == ["-d", "--wait", "gateway"]
    assert all(
        not m["required_files"] for m in plan["modules"] if m["mode"] == "external"
    )
    stop = launcher.commands(plan, "stop")[0]
    assert stop[-2:] == ["stop", "gateway"]
    assert "down" not in stop and "-v" not in stop


@pytest.mark.parametrize(
    "value",
    [
        profile(["unknown"]),
        profile(["calendar"], ["calls-phone"]),
        profile(["calendar"], external=["calls"]),
        profile(["calendar", "calendar"]),
    ],
)
def test_unknown_invalid_and_duplicate_selections_fail(value):
    with pytest.raises(ValueError):
        launcher.resolve(value)


def test_dependency_cycles_fail_before_process_creation(tmp_path):
    target = tmp_path / "config/modules"
    target.mkdir(parents=True)
    for name, dependency in [("gateway", "calendar"), ("calendar", "gateway")]:
        (target / (name + ".toml")).write_text(
            f'version=1\nid="{name}"\nrequires=["{dependency}"]\n'
        )
    with pytest.raises(ValueError, match="cycle"):
        launcher.resolve(profile(["calendar"]), tmp_path)


def test_missing_or_public_secret_fails_only_for_selected_managed_module(tmp_path):
    plan = launcher.resolve(profile(["calendar"], external=["calendar"]))
    with pytest.raises(ValueError, match="gateway"):
        launcher.validate_files(plan, tmp_path)
    for relative in next(
        m["required_files"] for m in plan["modules"] if m["module"] == "gateway"
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic")
        path.chmod(0o600)
    launcher.validate_files(plan, tmp_path)
    path.chmod(0o644)
    with pytest.raises(ValueError):
        launcher.validate_files(plan, tmp_path)
