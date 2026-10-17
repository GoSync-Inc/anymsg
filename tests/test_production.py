"""Promotion guards use synthetic GitHub/Compose responses; no live runtime access."""
import copy
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from types import SimpleNamespace

import pytest
import yaml

from scripts import production as p

SHA = "a" * 40
IMAGE = "sha256:" + "b" * 64
OLD_IMAGE = "sha256:" + "d" * 64
ROOT = Path(__file__).resolve().parents[1]


def github(*, conclusion="success", event="push", branch="main", sha=SHA, jobs=None):
    run = {"id": 12, "run_attempt": 2, "head_sha": sha, "head_branch": branch,
           "event": event, "status": "completed", "conclusion": conclusion,
           "head_repository": {"full_name": p.REPOSITORY}, "html_url": "https://example.invalid/run/12"}
    required = [{"name": name, "status": "completed", "conclusion": "success"} for name in p.REQUIRED_JOBS]
    def get(path):
        if path == "git/ref/heads/main":
            return {"object": {"sha": SHA}}
        if "/runs?" in path:
            return {"workflow_runs": [run]}
        assert "runs/12/attempts/2/jobs?" in path
        return {"jobs": required if jobs is None else jobs}
    return get, run, required


def test_exact_successful_main_push_and_all_required_jobs_allow_promotion():
    get, _, _ = github()
    assert p.verify(SHA, get)["run_attempt"] == 2


@pytest.mark.parametrize("candidate", ["main", "a" * 7, "A" * 40, "b" * 40, "refs/heads/feature", ""], ids=["branch-name", "short-sha", "uppercase", "outside-main", "feature-ref", "empty"])
def test_non_main_or_ambiguous_revision_cannot_reach_production(candidate):
    get, _, _ = github()
    with pytest.raises(p.Refused):
        p.verify(candidate, get)


@pytest.mark.parametrize("field,value", [("conclusion", "failure"), ("conclusion", "cancelled"),
    ("conclusion", "skipped"), ("status", "in_progress"), ("event", "workflow_dispatch"),
    ("event", "pull_request"), ("head_branch", "feature"), ("head_sha", "b" * 40),
    ("head_repository", {"full_name": "other/repo"})])
def test_failed_pending_or_unrelated_ci_never_authorizes_promotion(field, value):
    get, run, _ = github()
    run[field] = value
    with pytest.raises(p.Refused):
        p.verify(SHA, get)


def test_newer_failed_run_supersedes_an_old_green_run():
    get, latest, _ = github(conclusion="failure")
    old = {**latest, "id": 11, "conclusion": "success"}
    def response(path):
        return {"workflow_runs": [old, latest]} if "/runs?" in path else get(path)
    with pytest.raises(p.Refused):
        p.verify(SHA, response)


@pytest.mark.parametrize("outcome", ["failure", "skipped", "cancelled", None, "missing", "duplicate"])
def test_required_native_check_cannot_be_skipped_or_hidden_by_a_green_workflow(outcome):
    get, _, jobs = github()
    native = next(job for job in jobs if job["name"] == "native / synthetic")
    if outcome == "missing":
        jobs.remove(native)
    elif outcome == "duplicate":
        jobs.append(copy.deepcopy(native))
    else:
        native["conclusion"] = outcome
    with pytest.raises(p.Refused, match="required job"):
        p.verify(SHA, get)


def test_no_ci_run_fails_closed():
    get, _, _ = github()
    def response(path):
        return {"workflow_runs": []} if "/runs?" in path else get(path)
    with pytest.raises(p.Refused, match="no CI"):
        p.verify(SHA, response)


def fixture(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    project = {"name": "synthetic", "directory": str(tmp_path), "files": [str(tmp_path / "original.json")],
               "services": {"api": "runtime"}}
    settings = {"version": 1, "state_directory": str(state), "projects": [project]}
    document = {"name": "synthetic", "services": {"api": {"image": "old", "build": {"context": "/unused"},
        "environment": {"SYNTHETIC": "literal$password"}, "volumes": [{"source": "/existing", "target": "/data"}]},
        "db": {"image": "postgres:pinned"}}}
    (tmp_path / "original.json").write_text(json.dumps(document))
    calls = []
    def command(argv, **kwargs):
        calls.append(argv)
        if "FETCH_HEAD" in argv:
            return SHA
        if argv[:3] == ["docker", "image", "inspect"]:
            if "--format" in argv:
                return IMAGE
            return json.dumps([{"Id": IMAGE, "Config": {"Labels": {"org.opencontainers.image.revision": SHA},
                                                        "Env": ["PATH=/usr/local/bin", "DEEDKIT_SOURCE_SHA=" + SHA]}}])
        if argv[:2] == ["docker", "compose"] and "config" in argv:
            value = json.loads(Path(argv[argv.index("-f") + 1]).read_text())
            if "--profile" not in argv:
                value["services"] = {key: unit for key, unit in value["services"].items() if not unit.get("profiles")}
            # Compose unescapes $$ without consulting a real environment.
            return json.dumps(value).replace("$$", "$")
        if argv[:2] == ["docker", "ps"]:
            return "existing123\n"
        if argv[:2] == ["docker", "inspect"]:
            return json.dumps([{"Image": OLD_IMAGE if argv[2] == "existing123" else IMAGE, "State": {"Running": True}}])
        if "ps" in argv and "-q" in argv:
            return "replacement123\n"
        return ""
    monkeypatch.setattr(p, "run", command)
    monkeypatch.setattr(p, "verify", lambda sha: {"sha": sha, "run_id": 12})
    monkeypatch.setattr(p, "check_space", lambda directory: None)
    original = p.run
    monkeypatch.setattr(p, "run", lambda argv, **kw: str(tmp_path) if argv[:2] == ["docker", "info"] else original(argv, **kw))
    return settings, calls, document


def test_preparation_builds_only_exact_main_source_and_does_not_restart_services(tmp_path, monkeypatch):
    settings, calls, before = fixture(tmp_path, monkeypatch)
    receipt = p.prepare(settings, SHA)
    after = p.read(receipt["projects"][0]["file"])
    assert after["services"]["api"]["image"] == IMAGE
    assert after["services"]["api"]["volumes"] == before["services"]["api"]["volumes"]
    assert after["services"]["api"]["environment"]["SYNTHETIC"] == "literal$$password"
    assert after["services"]["db"] == before["services"]["db"]
    assert "build" not in after["services"]["api"]
    previous = p.read(Path(settings["state_directory"]) / SHA / "synthetic.before.json")
    assert previous["services"]["api"]["image"] == OLD_IMAGE
    assert "build" not in previous["services"]["api"]
    assert receipt["projects"][0]["running"]["api"] == {"container": "existing123", "image": OLD_IMAGE}
    assert any("refs/heads/main" in call for call in calls)
    assert not any("stop" in call or "up" in call for call in calls)
    smoke = next(call for call in calls if call[:2] == ["docker", "run"])
    assert smoke[smoke.index("--network") + 1] == "none"
    assert not any("--volume" in call or "-v" in call for call in calls)


def test_private_preparation_checks_out_source_readable_by_image_users(tmp_path, monkeypatch):
    real_run = p.run
    upstream = tmp_path / "public-upstream"
    real_run(["git", "init", "--quiet", "--initial-branch=main", str(upstream)])
    (upstream / "package").mkdir()
    (upstream / "package" / "module.py").write_text("VALUE = 'synthetic'\n")
    (upstream / "entrypoint").write_text("#!/bin/sh\nexit 0\n")
    (upstream / "entrypoint").chmod(0o755)
    real_run(["git", "-C", str(upstream), "add", "."])
    real_run(["git", "-C", str(upstream), "-c", "user.name=Synthetic", "-c",
              "user.email=synthetic@example.invalid", "commit", "--quiet", "-m", "fixture"])
    sha = real_run(["git", "-C", str(upstream), "rev-parse", "HEAD"]).strip()
    settings, calls, _ = fixture(tmp_path, monkeypatch)
    simulated_runtime = p.run

    def command(argv, **kwargs):
        if argv[0] == "git":
            if "remote" in argv:
                argv = [*argv[:-1], str(upstream)]
            return real_run(argv, **kwargs)
        return simulated_runtime(argv, **kwargs)

    def inspect_build_source(source, revision, target):
        assert revision == sha
        # Docker COPY preserves these modes; root-owned 0600/0700 code is
        # unreadable to the production image's UID 10001.
        assert stat.S_IMODE((source / "package" / "module.py").stat().st_mode) == 0o644
        assert stat.S_IMODE((source / "package").stat().st_mode) == 0o755
        assert stat.S_IMODE((source / "entrypoint").stat().st_mode) == 0o755
        return IMAGE

    monkeypatch.setattr(p, "run", command)
    monkeypatch.setattr(p, "build_image", inspect_build_source)
    previous = os.umask(0o077)
    try:
        receipt = p.prepare(settings, sha)
        state = Path(settings["state_directory"])
        assert stat.S_IMODE(state.stat().st_mode) == 0o700
        assert stat.S_IMODE((state / sha).stat().st_mode) == 0o700
        assert stat.S_IMODE((state / sha / "prepared.json").stat().st_mode) == 0o600
        assert stat.S_IMODE(Path(receipt["projects"][0]["file"]).stat().st_mode) == 0o600
        assert stat.S_IMODE((state / sha / "synthetic.before.json").stat().st_mode) == 0o600
        assert os.umask(0o077) == 0o077
    finally:
        os.umask(previous)
    assert not any("stop" in call or "up" in call for call in calls)


def test_profiled_live_services_remain_in_the_candidate_without_starting_other_profiles(tmp_path, monkeypatch):
    settings, calls, document = fixture(tmp_path, monkeypatch)
    document["services"]["api"]["profiles"] = ["collectors"]
    document["services"]["optional"] = {"profiles": ["other"], "image": "unrelated:pinned"}
    Path(settings["projects"][0]["files"][0]).write_text(json.dumps(document))
    receipt = p.prepare(settings, SHA)
    candidate = p.read(receipt["projects"][0]["file"])
    assert candidate["services"]["api"]["image"] == IMAGE
    assert candidate["services"]["optional"] == document["services"]["optional"]
    calls.clear()
    p.apply(settings, SHA)
    mutations = [call for call in calls if "stop" in call or "up" in call]
    assert len(mutations) == 2
    assert all(call[-1] == "api" and "optional" not in call and "db" not in call for call in mutations)
    assert "--no-deps" in mutations[-1]


def test_main_moving_during_source_fetch_prevents_any_build(tmp_path, monkeypatch):
    settings, calls, _ = fixture(tmp_path, monkeypatch)
    original = p.run
    monkeypatch.setattr(p, "run", lambda argv, **kw: "c" * 40 if "FETCH_HEAD" in argv else original(argv, **kw))
    with pytest.raises(p.Refused, match="main moved"):
        p.prepare(settings, SHA)
    assert not any(call[:2] == ["docker", "build"] for call in calls)


@pytest.mark.parametrize("mount", [
    {"type": "bind", "source": "/host/telegram_session_owner.py", "target": "/voice-bridge/telegram_session_owner.py"},
    {"type": "bind", "source": "/host/source", "target": "/app"},
    {"type": "volume", "source": "unchecked-code", "target": "/usr/local/lib/python3.13/site-packages"},
    {"type": "bind", "source": "/host/extension.py", "target": "/data/extension.py"},
    {"type": "bind", "source": "/host/source", "target": "/data/../app"},
])
def test_host_mounted_code_prevents_preparation_before_any_build_or_restart(tmp_path, monkeypatch, mount):
    settings, calls, document = fixture(tmp_path, monkeypatch)
    document["services"]["api"]["volumes"].append(mount)
    Path(settings["projects"][0]["files"][0]).write_text(json.dumps(document))
    with pytest.raises(p.Refused, match="host-mounted code"):
        p.prepare(settings, SHA)
    assert not any(call[:2] == ["docker", "build"] or "stop" in call or "up" in call for call in calls)


@pytest.mark.parametrize("name", ["PYTHONPATH", "NODE_PATH", "LD_PRELOAD", "LD_LIBRARY_PATH"])
def test_external_import_path_cannot_bypass_the_verified_image(tmp_path, monkeypatch, name):
    settings, calls, document = fixture(tmp_path, monkeypatch)
    document["services"]["api"]["environment"][name] = "/data/unchecked"
    Path(settings["projects"][0]["files"][0]).write_text(json.dumps(document))
    with pytest.raises(p.Refused, match="import path"):
        p.prepare(settings, SHA)
    assert not any(call[:2] == ["docker", "build"] or "stop" in call or "up" in call for call in calls)


BROKER = {"type": "bind", "source": "/opt/legacy/bridge", "target": "/broker"}
BYPASSES = {
    # Synthetic reproductions from the 2026-10-15 audit, then close variants.
    "broker-script": ({"volumes": [BROKER], "command": ["python", "/broker/telegram_session_owner.py"]}, "host-mounted code"),
    "broker-relative-script": ({"volumes": [BROKER], "command": ["python", "../broker/owner"]}, "host-mounted code"),
    "broker-module": ({"volumes": [BROKER], "command": ["python", "-m", "broker.telegram_session_owner"]}, "host-mounted code"),
    "attached-module": ({"volumes": [BROKER], "command": ["python3", "-Imbroker.owner"]}, "host-mounted code"),
    "working-dir": ({"working_dir": "/data/patch", "command": ["python", "-m", "messaging_service", "serve"]}, "host-mounted code"),
    "pythonuserbase": ({"environment": {"PYTHONUSERBASE": "/data/u"}}, "import path"),
    "pythonhome": ({"environment": {"PYTHONHOME": "/data/python"}}, "import path"),
    "pythonstartup": ({"environment": {"PYTHONSTARTUP": "/data/startup.py"}}, "import path"),
    "pythonsafepath": ({"environment": {"PYTHONSAFEPATH": ""}}, "import path"),
    "pycache-prefix": ({"environment": {"PYTHONPYCACHEPREFIX": "/data/pyc"}}, "import path"),
    "empty-pythonpath": ({"environment": {"PYTHONPATH": ""}}, "import path"),
    "listed-pythonpath": ({"environment": ["PYTHONPATH=/data"]}, "import path"),
    "user-site-home": ({"environment": {"HOME": "/data/home"}}, "import path"),
    "path-prefix": ({"environment": {"PATH": "/broker:/usr/local/bin:/usr/bin"}}, "import path"),
    "node-options": ({"environment": {"NODE_OPTIONS": "--require /data/x.js"}}, "import path"),
    "ld-audit": ({"environment": {"LD_AUDIT": "/data/audit.so"}}, "import path"),
    "gconv": ({"environment": {"GCONV_PATH": "/data/gconv"}}, "import path"),
    "entrypoint-sh-c": ({"entrypoint": ["sh", "-c", "python - <<<'print(1)'"]}, "inline"),
    "entrypoint-string": ({"entrypoint": "/bin/bash -lc 'exec python app.py'"}, "inline"),
    "python-c": ({"command": ["python", "-W", "ignore", "-c", "import runpy"]}, "inline"),
    "python-stdin": ({"command": ["python", "-"]}, "inline"),
    "wrapped-shell": ({"command": ["nice", "-n", "5", "busybox", "sh", "-ec", "true"]}, "inline"),
    "env-assignment": ({"command": ["env", "PYTHONPATH=/srv/extension", "python", "-m", "messaging_service"]}, "import path"),
    "env-split": ({"command": ["env", "-S", "python app.py"]}, "inline"),
    "node-require": ({"command": ["node", "--require=/srv/hook.js", "app.js"]}, "inline"),
    "embedded-mount": ({"command": ["perl", "-e", "do '/broker/bridge.pl'"]}, "host-mounted code"),
    "option-value-mount": ({"command": ["deedkit-gateway", "--plugin=/broker/x"]}, "host-mounted code"),
    "healthcheck-shell": ({"healthcheck": {"test": ["CMD-SHELL", "true"]}}, "inline"),
    "healthcheck-script": ({"healthcheck": {"test": ["CMD", "python", "/broker/probe.py"]}}, "host-mounted code"),
    "healthcheck-inline-mount": ({"healthcheck": {"test": ["CMD", "python", "-c", "exec(open('/broker/x').read())"]}}, "host-mounted code"),
    "secret-site-packages": ({"secrets": [{"source": "bridge", "target": "/usr/local/lib/python3.13/site-packages/bridge.pth"}]}, "host-mounted code"),
    "config-default-root": ({"configs": ["bridge"]}, "host-mounted code"),
    "lifecycle-hook": ({"post_start": [{"command": ["true"], "user": "root"}]}, "lifecycle"),
    "privileged": ({"privileged": True}, "privileged"),
    "cap-add": ({"cap_add": ["SYS_PTRACE"]}, "privileged"),
    "devices": ({"devices": ["/dev/fuse:/dev/fuse"]}, "privileged"),
    "volumes-from": ({"volumes_from": ["legacy-bridge"]}, "privileged"),
    "seccomp-unconfined": ({"security_opt": ["no-new-privileges:true", "seccomp=unconfined"]}, "privileged"),
    "network-host": ({"network_mode": "host"}, "namespaces"),
    "pid-host": ({"pid": "host"}, "namespaces"),
    "ipc-host": ({"ipc": "host"}, "namespaces"),
    "userns-host": ({"userns_mode": "host"}, "namespaces"),
    "pid-shared": ({"pid": "container:legacy-bridge"}, "namespaces"),
}


@pytest.mark.parametrize("case", BYPASSES, ids=list(BYPASSES))
def test_runtime_bypasses_prevent_preparation_before_any_build_or_restart(tmp_path, monkeypatch, case):
    settings, calls, document = fixture(tmp_path, monkeypatch)
    change, message = BYPASSES[case]
    unit = document["services"]["api"]
    for key, value in change.items():
        if key == "volumes":
            unit["volumes"] = unit["volumes"] + value
        elif key == "environment" and isinstance(value, dict):
            unit["environment"] = {**unit["environment"], **value}
        else:
            unit[key] = value
    Path(settings["projects"][0]["files"][0]).write_text(json.dumps(document))
    with pytest.raises(p.Refused, match=message):
        p.prepare(settings, SHA)
    assert not any(call[:2] == ["docker", "build"] or "stop" in call or "up" in call for call in calls)


@pytest.mark.parametrize("unit", [
    {"command": ["--policy", "/run/deedkit/policy.json", "--public-url", "https://gateway.example.invalid/deedkit",
                 "--host", "0.0.0.0", "--port", "13240"],
     "volumes": [{"source": "/srv/policy.json", "target": "/run/deedkit/policy.json"},
                 {"source": "/srv/keys", "target": "/run/deedkit/keys"}]},
    {"command": ["python", "-m", "telegram_session_owner", "--config", "/run/secrets/telegram_config"],
     "volumes": [{"source": "/srv/broker", "target": "/broker"}, {"source": "/srv/session", "target": "/sessions"}]},
    {"command": ["python", "-X", "dev", "-W", "default", "-m", "messaging_service", "serve"]},
    {"command": ["family-calendar", "--home=/data", "service", "serve"], "working_dir": "/app"},
    {"environment": {"PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1", "HOME": "/tmp",
                     "PATH": "/app/.venv/bin:/usr/local/bin:/usr/bin:/bin", "PROBE_FILES": "/data/files"}},
    {"healthcheck": {"test": ["CMD", "python", "-c", "import urllib.request", "https://gateway.example.invalid"]}},
    {"healthcheck": {"test": ["NONE"]}},
    {"secrets": ["database_url", {"source": "config", "target": "/run/secrets/telegram_config"}]},
    {"security_opt": ["no-new-privileges:true", "seccomp=/srv/chromium-seccomp.json"], "cap_drop": ["ALL"],
     "network_mode": "bridge", "ipc": "private"},
], ids=["gateway", "broker-owner", "interpreter-flags", "data-option", "environment", "healthcheck",
        "healthcheck-disabled", "secrets", "sandbox"])
def test_reviewed_runtime_settings_remain_promotable(unit):
    p.validate_runtime(unit)


class ComposeLoader(yaml.SafeLoader):
    """Read Compose override tags without constructing arbitrary Python objects."""


ComposeLoader.add_constructor("!override", lambda loader, node: loader.construct_sequence(node)
                              if isinstance(node, yaml.SequenceNode) else loader.construct_mapping(node))


def resolved(value):
    # Approximate `compose config` with synthetic values; no Compose or Docker runs.
    if isinstance(value, str):
        return re.sub(r"\$\{[^}]*\}", "synthetic", value)
    if isinstance(value, list):
        return [resolved(item) for item in value]
    if isinstance(value, dict):
        return {key: resolved(item) for key, item in value.items()}
    return value


def test_every_repository_application_service_passes_the_runtime_check():
    checked = []
    for path in sorted((ROOT / "deploy").glob("compose*.yaml")):
        for name, unit in yaml.load(path.read_text(), Loader=ComposeLoader)["services"].items():
            if name in {"db", "calendar-db", "operator"}:
                continue  # Never promotion targets: refused by name before any runtime check.
            unit = resolved(unit)
            unit["volumes"] = [volume if isinstance(volume, dict)
                               else dict(zip(("source", "target"), volume.split(":")[:2] if ":" in volume else ("", volume)))
                               for volume in unit.get("volumes", [])]
            if path.name == "compose.calls-telegram.yaml":
                # Existing limitation: /run/telegram-broker is not an approved mount root.
                with pytest.raises(p.Refused, match="host-mounted code"):
                    p.validate_runtime(unit)
                continue
            p.validate_runtime(unit)
            checked.append(name)
    assert {"api", "gateway", "calendar-api", "calendar-worker", "calls", "telegram", "whatsapp1", "whatsapp2",
            "indexer", "enricher", "enricher-local", "subscriptions"} <= set(checked)


def test_ci_becoming_red_after_build_prevents_a_prepared_release(tmp_path, monkeypatch):
    settings, calls, _ = fixture(tmp_path, monkeypatch)
    attempts = []
    def gate(sha):
        attempts.append(sha)
        if len(attempts) == 2:
            raise p.Refused("latest CI failed")
        return {"sha": sha}
    monkeypatch.setattr(p, "verify", gate)
    with pytest.raises(p.Refused):
        p.prepare(settings, SHA)
    assert not (Path(settings["state_directory"]) / SHA / "prepared.json").exists()
    assert not any("stop" in call or "up" in call for call in calls)


def test_apply_uses_prepared_image_without_build_pull_migration_or_database_restart(tmp_path, monkeypatch):
    settings, calls, _ = fixture(tmp_path, monkeypatch)
    p.prepare(settings, SHA)
    calls.clear()
    result = p.apply(settings, SHA)
    assert result["status"] == "applied"
    stop = next(i for i, call in enumerate(calls) if "stop" in call)
    up = next(i for i, call in enumerate(calls) if "up" in call)
    assert stop < up
    assert calls[stop][-1] == calls[up][-1] == "api"
    assert "--no-build" in calls[up] and "--no-deps" in calls[up]
    assert calls[up][calls[up].index("--pull") + 1] == "never"
    assert not any("build" in call or "migrate" in call or "db" in call for call in calls)
    assert p.read(Path(settings["state_directory"]) / "current.json")["sha"] == SHA


@pytest.mark.parametrize("change", ["config", "container", "snapshot", "image", "ci"])
def test_changed_candidate_or_live_runtime_is_refused_before_first_stop(tmp_path, monkeypatch, change):
    settings, calls, _ = fixture(tmp_path, monkeypatch)
    receipt = p.prepare(settings, SHA)
    original = p.run
    if change == "config":
        settings["new_setting"] = True
    elif change == "snapshot":
        Path(receipt["projects"][0]["file"]).write_text("{}")
    elif change == "ci":
        monkeypatch.setattr(p, "verify", lambda sha: (_ for _ in ()).throw(p.Refused("red CI")))
    else:
        def changed(argv, **kw):
            if change == "container" and argv[:2] == ["docker", "ps"]:
                return "other123\n"
            if change == "image" and argv[:3] == ["docker", "image", "inspect"]:
                return json.dumps([{"Id": IMAGE, "Config": {"Labels": {"org.opencontainers.image.revision": "c" * 40}}}])
            return original(argv, **kw)
        monkeypatch.setattr(p, "run", changed)
    calls.clear()
    with pytest.raises(p.Refused):
        p.apply(settings, SHA)
    assert not any("stop" in call or "up" in call for call in calls)


def test_failed_apply_is_not_blindly_retried_or_marked_current(tmp_path, monkeypatch):
    settings, calls, _ = fixture(tmp_path, monkeypatch)
    p.prepare(settings, SHA)
    original = p.run
    def failing(argv, **kw):
        if "up" in argv:
            raise p.Refused("synthetic start failure")
        return original(argv, **kw)
    monkeypatch.setattr(p, "run", failing)
    with pytest.raises(p.Refused):
        p.apply(settings, SHA)
    assert not (Path(settings["state_directory"]) / "current.json").exists()
    calls.clear()
    with pytest.raises(p.Refused, match="already attempted"):
        p.apply(settings, SHA)
    assert not any("stop" in call or "up" in call for call in calls)


def test_host_lock_rejects_concurrent_promotion(tmp_path):
    state = tmp_path / "private"
    with p.lock(state):
        with pytest.raises(p.Refused, match="lock"):
            with p.lock(state):
                pytest.fail("two promoters acquired the same lock")


def test_ci_gate_fails_for_failed_cancelled_or_skipped_dependencies():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    gate = workflow["jobs"]["gate"]
    assert gate["needs"] == ["checks", "native", "images"] and gate["if"] == "${{ always() }}"
    assert workflow["jobs"]["native"]["uses"] == "./.github/workflows/whatsmeow.yml"
    script = gate["steps"][0]["run"]
    for checks, native, images in [("success", "success", "success"), ("failure", "success", "success"),
                                  ("success", "skipped", "success"), ("cancelled", "success", "success"),
                                  ("success", "success", "failure")]:
        result = subprocess.run(["bash", "-e", "-c", script], env={"CHECKS_RESULT": checks, "NATIVE_RESULT": native, "IMAGES_RESULT": images}, capture_output=True)
        assert (result.returncode == 0) == (checks == native == images == "success")


def test_ci_gate_refuses_a_manual_baseline_of_another_revision():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    step = workflow["jobs"]["gate"]["steps"][0]
    assert step["env"]["SOURCE_REF"] == "${{ inputs.source_ref || '' }}"
    assert step["env"]["EXPECTED_SHA"] == "${{ github.sha }}"
    green = {"CHECKS_RESULT": "success", "NATIVE_RESULT": "success", "IMAGES_RESULT": "success",
             "EXPECTED_SHA": SHA}
    for source_ref, passes in [("", True), (SHA, True), ("b" * 40, False), ("main", False),
                               ("refs/tags/v1.2.3", False)]:
        result = subprocess.run(["bash", "-e", "-c", step["run"]], env={**green, "SOURCE_REF": source_ref},
                                capture_output=True)
        assert (result.returncode == 0) == passes, source_ref


def test_disk_pressure_prevents_source_fetch_and_build(tmp_path, monkeypatch):
    settings, calls, _ = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(p, "check_space", lambda directory: (_ for _ in ()).throw(p.Refused("insufficient disk")))
    with pytest.raises(p.Refused, match="disk"):
        p.prepare(settings, SHA)
    assert calls == []


def test_disk_floor_is_enforced_on_the_real_preflight_function(tmp_path, monkeypatch):
    monkeypatch.setattr(p.shutil, "disk_usage", lambda _: SimpleNamespace(free=p.MIN_FREE_BYTES - 1))
    with pytest.raises(p.Refused, match="15 GiB"):
        p.check_space(tmp_path)
    monkeypatch.setattr(p.shutil, "disk_usage", lambda _: SimpleNamespace(free=p.MIN_FREE_BYTES))
    p.check_space(tmp_path)


def test_config_rejects_a_database_as_an_application_target(tmp_path, monkeypatch):
    settings, _, _ = fixture(tmp_path, monkeypatch)
    settings["projects"][0]["services"] = {"db": "runtime"}
    filename = tmp_path / "production.json"
    filename.write_text(json.dumps(settings))
    filename.chmod(0o600)
    with pytest.raises(p.Refused, match="application"):
        p.config(filename)


MESSAGING_STAGES = ("runtime", "indexer", "enricher-local", "whatsapp-native", "backup")


def test_production_messaging_stages_end_with_the_source_revision():
    # Parse instructions after folding continuations; the variable comes last so earlier layers stay cached.
    lines = (ROOT / "services/messaging/Dockerfile").read_text().replace("\\\n", " ").splitlines()
    stages, current = {}, None
    for line in (value.strip() for value in lines):
        if not line or line.startswith("#"):
            continue
        if line.upper().startswith("FROM "):
            current = line.split()[-1] if " AS " in line.upper() else None
            stages[current] = []
        elif current:
            stages[current].append(line)
    for stage in MESSAGING_STAGES:
        assert stages[stage][-2:] == ["ARG DEEDKIT_SOURCE_SHA", "ENV DEEDKIT_SOURCE_SHA=$DEEDKIT_SOURCE_SHA"], stage
        assert sum("DEEDKIT_SOURCE_SHA" in line for line in stages[stage]) == 2, stage
    assert {target for target, build in p.BUILDS.items() if build[1] == "services/messaging/Dockerfile"} == set(MESSAGING_STAGES)


def image_builder(monkeypatch, *, labels=None, env=None):
    calls = []
    def command(argv, **kwargs):
        calls.append(argv)
        if argv[:3] == ["docker", "image", "inspect"]:
            if "--format" in argv:
                return IMAGE
            return json.dumps([{"Id": IMAGE, "Config": {
                "Labels": {"org.opencontainers.image.revision": SHA} if labels is None else labels,
                "Env": ["PATH=/usr/local/bin", "DEEDKIT_SOURCE_SHA=" + SHA] if env is None else env}}])
        return ""
    monkeypatch.setattr(p, "run", command)
    return calls


@pytest.mark.parametrize("target", sorted(p.BUILDS))
def test_every_production_build_receives_the_candidate_source_revision(tmp_path, monkeypatch, target):
    calls = image_builder(monkeypatch)
    assert p.build_image(tmp_path, SHA, target) == IMAGE
    build = next(call for call in calls if call[:2] == ["docker", "build"])
    assert build[build.index("--build-arg") + 1] == "DEEDKIT_SOURCE_SHA=" + SHA
    assert "org.opencontainers.image.revision=" + SHA in build
    checked = ["docker", "image", "inspect", IMAGE] in calls
    assert checked == (target in MESSAGING_STAGES)
    assert any(call[:2] == ["docker", "run"] for call in calls)


@pytest.mark.parametrize("labels,env", [
    (None, ["PATH=/usr/local/bin"]),
    (None, ["DEEDKIT_SOURCE_SHA="]),
    (None, ["DEEDKIT_SOURCE_SHA=" + "c" * 40]),
    (None, []),
    ({"org.opencontainers.image.revision": "c" * 40}, None),
    ({}, None),
])
def test_new_messaging_image_without_the_candidate_revision_is_refused_before_smoke(tmp_path, monkeypatch, labels, env):
    calls = image_builder(monkeypatch, labels=labels, env=env)
    with pytest.raises(p.Refused, match="source revision"):
        p.build_image(tmp_path, SHA, "runtime")
    assert not any(call[:2] == ["docker", "run"] for call in calls)


def test_apply_still_accepts_prepared_images_built_before_the_source_revision(tmp_path, monkeypatch):
    settings, calls, _ = fixture(tmp_path, monkeypatch)
    p.prepare(settings, SHA)
    original = p.run
    def older(argv, **kw):
        # An image prepared before this change has the revision label but no DEEDKIT_SOURCE_SHA.
        if argv[:3] == ["docker", "image", "inspect"] and "--format" not in argv:
            return json.dumps([{"Id": IMAGE, "Config": {"Labels": {"org.opencontainers.image.revision": SHA},
                                                        "Env": ["PATH=/usr/local/bin"]}}])
        return original(argv, **kw)
    monkeypatch.setattr(p, "run", older)
    assert p.apply(settings, SHA)["status"] == "applied"
