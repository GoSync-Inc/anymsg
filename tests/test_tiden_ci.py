"""Reporting tests use synthetic JUnit and never call Tiden."""
from collections import Counter
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from scripts.tiden_ci import CHECKS, normalize_junit, prepare, publish, resolve_branch


def report(tmp_path, xml):
    path = tmp_path / "report.xml"
    path.write_text(xml)
    return path


def test_real_failure_and_skipped_outcomes_survive_normalization(tmp_path):
    path = report(tmp_path, '<testsuite><testcase name="a[1]" classname="tests.test_sample" file="tests/test_sample.py"/>'
                  '<testcase name="a[2]" classname="tests.test_sample" file="tests/test_sample.py"><failure/></testcase>'
                  '<testcase name="optional" classname="tests.test_sample" file="tests/test_sample.py"><skipped/></testcase></testsuite>')
    xml, counts = normalize_junit(path, tmp_path, "python")
    cases = list(ET.fromstring(xml).iter("testcase"))
    assert counts == Counter(passed=1, failed=1, skipped=1)
    assert [c.get("name") for c in cases] == ["a[1]", "a[2]", "optional"]
    assert cases[1].find("failure") is not None and cases[2].find("skipped") is not None


def test_node_titles_from_different_files_keep_distinct_identities(tmp_path):
    path = report(tmp_path, f'<testsuites><testcase name="equal title" classname="test" file="{tmp_path}/whatsapp/tests/a.test.mjs"/>'
                  f'<testcase name="equal title" classname="test" file="{tmp_path}/whatsapp/tests/b.test.mjs"/></testsuites>')
    xml, _ = normalize_junit(path, tmp_path, "collector")
    cases = list(ET.fromstring(xml).iter("testcase"))
    assert cases[0].get("classname") != cases[1].get("classname")
    assert cases[0].get("file") == "whatsapp/tests/a.test.mjs"


@pytest.mark.parametrize("xml", ['<!DOCTYPE testsuite [<!ENTITY x "secret">]><testsuite/>', '<testsuite/>',
                                 '<testsuite><testcase name="x" file="../outside.py"/></testsuite>',
                                 '<testsuite><testcase name="x" file="/outside.py"/></testsuite>',
                                 '<testsuite><testcase name="x"/></testsuite>'])
def test_untrusted_or_incomplete_junit_is_rejected(tmp_path, xml):
    with pytest.raises(ValueError):
        normalize_junit(report(tmp_path, xml), tmp_path, "python")


def metadata():
    return {"sha": "a" * 40, "git_branch": "codex/feature", "ci_url": "https://github.com/example/repo/actions/runs/1",
            "checks": {check: "success" for check in CHECKS}}


def test_missing_reports_and_unrun_checks_cannot_make_ci_green(tmp_path):
    meta = metadata()
    meta["checks"]["smoke"] = "skipped"
    summary = prepare(tmp_path, tmp_path, meta)
    assert summary["test_counts"] == {}
    assert summary["check_counts"] == {"passed": 3, "blocked": 4}
    checks = json.loads((tmp_path / "ci-checks.json").read_text())
    assert next(x for x in checks if x["signature"] == "anymsg-ci::smoke")["execution"]["status"] == "blocked"


def test_failed_test_overrides_misreported_successful_step(tmp_path):
    (tmp_path / "python.xml").write_text('<testsuite><testcase name="x" file="tests/x.py"><failure/></testcase></testsuite>')
    result = prepare(tmp_path, tmp_path, metadata())
    assert result["check_counts"]["failed"] == 1 and result["test_counts"]["failed"] == 1


def test_only_skipped_suite_does_not_count_as_success(tmp_path):
    (tmp_path / "python.xml").write_text('<testsuite><testcase name="x" file="tests/x.py"><skipped/></testcase></testsuite>')
    result = prepare(tmp_path, tmp_path, metadata())
    assert result["check_counts"]["blocked"] == 3
    assert result["test_counts"] == {"skipped": 1}


def test_branch_mapping_is_explicit_and_never_falls_back_to_main():
    meta = metadata()
    with pytest.raises(ValueError, match="mapping"):
        resolve_branch(meta, {})
    assert resolve_branch(meta, {"codex/feature": "intent/feature"}) == "intent/feature"
    assert resolve_branch({**meta, "git_branch": "main"}, {}) == "main"
    assert resolve_branch({**meta, "tiden_branch": "intent/specific"}, {}) == "intent/specific"


def cli_fixture(monkeypatch, tmp_path, *, actual_branch="branch-id", status="passed", attempts="7"):
    import subprocess
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        command = args[1:3]
        if command == ["branch", "list"]:
            value = {"branches": [{"id": "branch-id", "name": "intent/feature"}]}
        elif command == ["run", "create"]:
            value = {"seqNum": 3, "branchName": "intent/feature", "buildSha": "a" * 40}
        elif command == ["run", "get"]:
            value = {"status": status, "branchId": actual_branch, "liveDocStatus": "succeeded", "stats": {"attempts": attempts}}
        elif command == ["run", "complete"]:
            value = {"status": status}
        else:
            value = {}
        return subprocess.CompletedProcess(args, 1 if command == ["run", "complete"] and status == "failed" else 0,
                                           stdout=json.dumps(value), stderr="")
    monkeypatch.setattr(subprocess, "run", run)
    (tmp_path / "ci-checks.json").write_text("[]")
    manifest = {**metadata(), "schema": 1, "reports": ["ci-checks.json"], "test_counts": {},
                "check_counts": {"passed": 7}}
    return calls, manifest


def test_publisher_checks_the_final_resolved_branch_not_only_run_creation(tmp_path, monkeypatch):
    calls, manifest = cli_fixture(monkeypatch, tmp_path, actual_branch="wrong-branch")
    with pytest.raises(RuntimeError, match="expected branch"):
        publish(tmp_path, manifest, "intent/feature", "product")
    creation = next(call for call in calls if call[1:3] == ["run", "create"])
    assert creation[creation.index("--intent-session") + 1] == "none"


def test_publisher_refuses_partial_results_even_when_tiden_returns_passed(tmp_path, monkeypatch):
    _, manifest = cli_fixture(monkeypatch, tmp_path, attempts="1")
    with pytest.raises(RuntimeError, match="count differs"):
        publish(tmp_path, manifest, "intent/feature", "product")


def test_failed_completion_still_gets_a_verified_failure_receipt(tmp_path, monkeypatch):
    _, manifest = cli_fixture(monkeypatch, tmp_path, status="failed")
    manifest["check_counts"] = {"passed": 6, "failed": 1}
    assert publish(tmp_path, manifest, "intent/feature", "product")["status"] == "failed"


def test_publisher_refuses_a_green_verdict_for_blocked_ci(tmp_path, monkeypatch):
    _, manifest = cli_fixture(monkeypatch, tmp_path)
    manifest["check_counts"] = {"passed": 6, "blocked": 1}
    with pytest.raises(RuntimeError, match="contradicts"):
        publish(tmp_path, manifest, "intent/feature", "product")
