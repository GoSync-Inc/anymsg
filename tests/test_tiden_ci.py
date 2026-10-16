"""Reporting tests use synthetic JUnit and never call Tiden."""
from collections import Counter
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from scripts.tiden_ci import (CHECKS, intent_links, linked_intent_branch, normalize_junit, prepare, publish,
                              resolve_branch, route_branch)


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


def test_publisher_counts_additive_module_checks_without_changing_legacy_checks(tmp_path, monkeypatch):
    _, manifest = cli_fixture(monkeypatch, tmp_path, attempts="12")
    manifest["module_checks"] = {name: "success" for name in ("gateway", "calendar", "calls", "calls_frontend", "load")}
    manifest["check_counts"] = {"passed": 12}
    assert publish(tmp_path, manifest, "intent/feature", "product")["status"] == "passed"


def test_failed_completion_still_gets_a_verified_failure_receipt(tmp_path, monkeypatch):
    _, manifest = cli_fixture(monkeypatch, tmp_path, status="failed")
    manifest["check_counts"] = {"passed": 6, "failed": 1}
    assert publish(tmp_path, manifest, "intent/feature", "product")["status"] == "failed"


def test_publisher_refuses_a_green_verdict_for_blocked_ci(tmp_path, monkeypatch):
    _, manifest = cli_fixture(monkeypatch, tmp_path)
    manifest["check_counts"] = {"passed": 6, "blocked": 1}
    with pytest.raises(RuntimeError, match="contradicts"):
        publish(tmp_path, manifest, "intent/feature", "product")


def test_unmapped_feature_branch_is_routed_under_its_own_git_branch_name():
    meta = metadata()
    assert route_branch(meta, {}) == ("codex/feature", True)
    assert route_branch(meta, {"codex/feature": "intent/feature"}) == ("intent/feature", False)
    assert route_branch({**meta, "git_branch": "main"}, {}) == ("main", False)
    assert route_branch({**meta, "tiden_branch": "intent/specific"}, {}) == ("intent/specific", False)
    for name in ("Codex/Feature", "release/1.2", "codex//feature", "codex/feature/", "-feature"):
        with pytest.raises(ValueError, match="mapping"):
            route_branch({**meta, "git_branch": name}, {})


def routed_cli(monkeypatch, tmp_path, *, created="codex/feature", final_id="new-id",
               final_branches=({"id": "new-id", "name": "codex/feature"},), live_doc="succeeded",
               existing=(), creation="created"):
    import subprocess
    calls = []
    branches = [{"id": "main-id", "name": "main"}, *existing]
    def run(args, **kwargs):
        calls.append(args)
        command = args[1:3]
        if command == ["branch", "list"]:
            # Branches created while Tiden processes the run appear once the run exists.
            ran = any(call[1:3] == ["run", "create"] for call in calls)
            value = {"branches": [*branches, *(final_branches if ran else ())]}
        elif command == ["branch", "create"]:
            if creation != "refused":
                # "raced": a concurrent CI run for the same Git branch created it first.
                branches.append({"id": "new-id", "name": args[args.index("--name") + 1]})
            if creation != "created":
                return subprocess.CompletedProcess(args, 1, stdout="", stderr="")
            value = branches[-1]
        elif command == ["run", "create"]:
            value = {"seqNum": 4, "branchName": created, "buildSha": "a" * 40}
        elif command == ["run", "get"]:
            value = {"status": "passed", "branchId": final_id, "liveDocStatus": live_doc, "stats": {"attempts": "7"}}
        elif command == ["run", "complete"]:
            value = {"status": "passed"}
        else:
            value = {}
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(value), stderr="")
    monkeypatch.setattr(subprocess, "run", run)
    (tmp_path / "ci-checks.json").write_text("[]")
    manifest = {**metadata(), "schema": 1, "reports": ["ci-checks.json"], "test_counts": {},
                "check_counts": {"passed": 7}}
    return calls, manifest


def test_routed_run_lands_on_the_same_named_branch_tiden_creates(tmp_path, monkeypatch):
    calls, manifest = routed_cli(monkeypatch, tmp_path)
    assert publish(tmp_path, manifest, "codex/feature", "product", routed=True)["branchId"] == "new-id"
    branch = next(call for call in calls if call[1:3] == ["branch", "create"])
    assert branch[branch.index("--name") + 1] == "codex/feature"
    creation = next(call for call in calls if call[1:3] == ["run", "create"])
    assert calls.index(branch) < calls.index(creation)
    assert creation[creation.index("--branch") + 1] == "codex/feature"
    assert creation[creation.index("--intent-session") + 1] == "none"


def test_routed_run_reuses_an_existing_same_named_branch(tmp_path, monkeypatch):
    calls, manifest = routed_cli(monkeypatch, tmp_path, final_id="old-id", final_branches=(),
                                 existing=({"id": "old-id", "name": "codex/feature"},))
    assert publish(tmp_path, manifest, "codex/feature", "product", routed=True)["branchId"] == "old-id"
    assert not any(call[1:3] == ["branch", "create"] for call in calls)


def test_routed_run_tolerates_a_concurrent_branch_creation(tmp_path, monkeypatch):
    _, manifest = routed_cli(monkeypatch, tmp_path, creation="raced")
    assert publish(tmp_path, manifest, "codex/feature", "product", routed=True)["branchId"] == "new-id"


def test_routed_run_refuses_when_no_tiden_branch_can_be_created(tmp_path, monkeypatch):
    calls, manifest = routed_cli(monkeypatch, tmp_path, creation="refused")
    with pytest.raises(ValueError, match="does not exist"):
        publish(tmp_path, manifest, "codex/feature", "product", routed=True)
    assert not any(call[1:3] == ["run", "create"] for call in calls)


def test_routed_run_follows_the_intent_branch_tiden_attributes_it_to(tmp_path, monkeypatch):
    _, manifest = routed_cli(monkeypatch, tmp_path, created="intent/2026-10-15-agents-feature", final_id="intent-id",
                             final_branches=({"id": "intent-id", "name": "intent/2026-10-15-agents-feature"},))
    assert publish(tmp_path, manifest, "codex/feature", "product", routed=True)["branchId"] == "intent-id"


@pytest.mark.parametrize("final_id, final_branches, live_doc", [
    ("main-id", (), "succeeded"),
    ("other-id", ({"id": "other-id", "name": "codex/other"},), "succeeded"),
    ("missing-id", (), "succeeded"),
    ("new-id", ({"id": "new-id", "name": "codex/feature"},), "failed"),
])
def test_routed_run_never_lands_on_main_or_an_unrelated_branch(tmp_path, monkeypatch, final_id, final_branches, live_doc):
    calls, manifest = routed_cli(monkeypatch, tmp_path, final_id=final_id, final_branches=final_branches, live_doc=live_doc)
    with pytest.raises(RuntimeError, match="expected branch"):
        publish(tmp_path, manifest, "codex/feature", "product", routed=True)
    assert not any(call[1:3] == ["run", "abort"] for call in calls)


def test_routed_run_refuses_a_rewritten_source_association(tmp_path, monkeypatch):
    calls, manifest = routed_cli(monkeypatch, tmp_path, created="main")
    with pytest.raises(RuntimeError, match="rewrote"):
        publish(tmp_path, manifest, "codex/feature", "product", routed=True)
    assert any(call[1:3] == ["run", "abort"] for call in calls)


# Synthetic Tiden identities; none of these belong to a real workspace or product.
WORKSPACE = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
PRODUCT = "11111111-2222-4333-8444-555555555555"
INTENT_ID = "99999999-8888-4777-8666-555555555555"
INTENT = {"id": INTENT_ID, "name": "intent/2026-10-16-feature", "status": "open"}


def tiden_link(branch=INTENT_ID, product=PRODUCT, workspace=WORKSPACE):
    return f"https://app.tiden.ai/{workspace}/products/{product}/branches/{branch}"


def linked_manifest(manifest, *links):
    return {**manifest, "pr_tiden_links": intent_links(" ".join(links))}


def test_pull_request_description_yields_only_tiden_branch_ids():
    body = (f"Fix.\n\nTiden intent branch: [intent/2026-10-16-feature]({tiden_link()})\n"
            f"Again: {tiden_link()} and https://example.com/branches/{INTENT_ID}")
    assert intent_links(body) == [{"workspace": WORKSPACE, "product": PRODUCT, "branch": INTENT_ID}]
    assert intent_links("") == [] and intent_links(None) == []
    many = " ".join(tiden_link(f"{i:08d}-0000-4000-8000-000000000000") for i in range(9))
    assert len(intent_links(many)) == 5
    assert intent_links(tiden_link(branch="not-a-uuid")) == []


def test_linked_open_intent_branch_receives_pull_request_evidence(tmp_path, monkeypatch):
    calls, manifest = routed_cli(monkeypatch, tmp_path, created=INTENT["name"], final_id=INTENT_ID,
                                 final_branches=(), existing=(INTENT,))
    result = publish(tmp_path, linked_manifest(manifest, tiden_link()), "codex/feature", PRODUCT,
                     routed=True, workspace=WORKSPACE)
    assert result["branchId"] == INTENT_ID
    assert not any(call[1:3] == ["branch", "create"] for call in calls)
    creation = next(call for call in calls if call[1:3] == ["run", "create"])
    assert creation[creation.index("--branch") + 1] == INTENT["name"]
    assert json.loads((tmp_path / "tiden-route.json").read_text()) == {
        "branch": INTENT["name"], "source": "pull-request-link"}


@pytest.mark.parametrize("existing, links", [
    ((INTENT,), (tiden_link(product="22222222-2222-4333-8444-555555555555"),)),
    ((INTENT,), (tiden_link(workspace="bbbbbbbb-bbbb-4ccc-8ddd-eeeeeeeeeeee"),)),
    (({**INTENT, "status": "merged"},), (tiden_link(),)),
    (({**INTENT, "name": "codex/other"},), (tiden_link(),)),
    ((INTENT, {"id": "77777777-8888-4777-8666-555555555555", "name": "intent/2026-10-16-other", "status": "open"}),
     (tiden_link(), tiden_link(branch="77777777-8888-4777-8666-555555555555"))),
    ((), (tiden_link(),)),
])
def test_unusable_pull_request_link_keeps_git_branch_routing(tmp_path, monkeypatch, existing, links):
    calls, manifest = routed_cli(monkeypatch, tmp_path, existing=existing)
    result = publish(tmp_path, linked_manifest(manifest, *links), "codex/feature", PRODUCT,
                     routed=True, workspace=WORKSPACE)
    assert result["branchId"] == "new-id"
    creation = next(call for call in calls if call[1:3] == ["run", "create"])
    assert creation[creation.index("--branch") + 1] == "codex/feature"
    assert json.loads((tmp_path / "tiden-route.json").read_text())["source"] == "routed"


def test_explicit_target_wins_over_a_pull_request_link(tmp_path, monkeypatch):
    explicit = {"id": "explicit-id", "name": "intent/explicit", "status": "open"}
    calls, manifest = routed_cli(monkeypatch, tmp_path, created=explicit["name"], final_id="explicit-id",
                                 final_branches=(), existing=(explicit, INTENT))
    publish(tmp_path, linked_manifest(manifest, tiden_link()), "intent/explicit", PRODUCT, workspace=WORKSPACE)
    creation = next(call for call in calls if call[1:3] == ["run", "create"])
    assert creation[creation.index("--branch") + 1] == "intent/explicit"
    meta = {**metadata(), "pr_tiden_links": intent_links(tiden_link())}
    assert route_branch({**meta, "tiden_branch": "intent/explicit"}, {}) == ("intent/explicit", False)
    assert route_branch(meta, {"codex/feature": "intent/mapped"}) == ("intent/mapped", False)
    assert linked_intent_branch(meta["pr_tiden_links"], [INTENT], PRODUCT, WORKSPACE) == INTENT["name"]


def test_unroutable_git_branch_name_needs_a_usable_pull_request_link(tmp_path, monkeypatch):
    calls, manifest = routed_cli(monkeypatch, tmp_path, created=INTENT["name"], final_id=INTENT_ID,
                                 final_branches=(), existing=(INTENT,))
    manifest = {**linked_manifest(manifest, tiden_link()), "git_branch": "Release/1.2"}
    assert publish(tmp_path, manifest, None, PRODUCT, routed=True, workspace=WORKSPACE)["branchId"] == INTENT_ID
    with pytest.raises(ValueError, match="intent link"):
        publish(tmp_path, {**manifest, "pr_tiden_links": []}, None, PRODUCT, routed=True, workspace=WORKSPACE)


def test_prepare_keeps_pull_request_link_ids_but_not_the_description(tmp_path, monkeypatch):
    import sys
    from scripts import tiden_ci
    (tmp_path / "metadata.json").write_text(json.dumps(metadata()))
    monkeypatch.setenv("TIDEN_PR_BODY", f"Private note. Tiden intent branch: {tiden_link()}")
    monkeypatch.setattr(sys, "argv", ["tiden_ci.py", "prepare", "--directory", str(tmp_path),
                                      "--source-root", str(tmp_path)])
    assert tiden_ci.main() == 0
    manifest = (tmp_path / "manifest.json").read_text()
    assert json.loads(manifest)["pr_tiden_links"] == [{"workspace": WORKSPACE, "product": PRODUCT, "branch": INTENT_ID}]
    assert "Private note" not in manifest
