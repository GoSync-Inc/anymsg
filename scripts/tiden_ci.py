"""Preserve real CI outcomes and explicitly associate evidence with a Tiden branch.

The preparation runner has no Tiden credentials. The publishing runner executes
only this tool and the pinned Tiden CLI, never tests or application code.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET

CHECKS = ("build", "release", "python", "collector", "npm", "smoke", "distributions")
REPORTS = {"python": "python.xml", "collector": "collector.xml", "npm": "npm.xml"}
MODULE_REPORTS = {
    "gateway": ("gateway.xml", "python", "services/gateway"),
    "calendar": ("calendar.xml", "python", "services/calendar"),
    "calls": ("calls.xml", "python", "services/calls"),
    "calls_frontend": ("calls-frontend.xml", "node", "services/calls/frontend"),
}
MAX_BYTES = 20 * 1024 * 1024


def source_path(value: str, root: Path) -> str:
    path = Path(value)
    if path.is_absolute():
        path = path.relative_to(root)
    value = path.as_posix()
    parts = PurePosixPath(value).parts
    if not parts or any(part in {"..", ".git"} for part in parts):
        raise ValueError("test source path is outside the checkout")
    return value


def normalize_junit(path: Path, root: Path, framework: str, source_prefix: str = "") -> tuple[bytes, Counter]:
    data = path.read_bytes()
    if len(data) > MAX_BYTES or b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("unsafe or oversized JUnit report")
    document = ET.fromstring(data)
    if document.tag not in {"testsuites", "testsuite"}:
        raise ValueError("not a JUnit report")
    cases = list(document.iter("testcase"))
    if not cases or len(cases) > 10000:
        raise ValueError("JUnit report must contain bounded real executions")
    counts = Counter()
    for case in cases:
        if not case.get("name"):
            raise ValueError("test identity is missing")
        file = case.get("file", "")
        if not file and framework == "python":
            # pytest xunit2 omits file; xunit1 in CI preserves the actual path.
            candidate = case.get("classname", "").replace(".", "/") + ".py"
            if (root / candidate).is_file():
                file = candidate
        if not file:
            raise ValueError("test source path is missing")
        if source_prefix and not Path(file).is_absolute() and not file.startswith(source_prefix + "/"):
            file = source_prefix + "/" + file
        file = source_path(file, root)
        case.set("file", file)
        if source_prefix and framework == "python":
            case.set("classname", source_prefix.replace("/", ".") + "." + case.get("classname", ""))
        if framework != "python":
            # Node's built-in reporter uses classname='test' for every file.
            # File qualification prevents unrelated equal titles from colliding.
            case.set("classname", "node:" + file)
        status = "failed" if case.find("failure") is not None or case.find("error") is not None else (
            "skipped" if case.find("skipped") is not None else "passed")
        counts[status] += 1
    return ET.tostring(document, encoding="utf-8", xml_declaration=True), counts


def prepare(root: Path, directory: Path, metadata: dict) -> dict:
    if not re.fullmatch(r"[a-f0-9]{40}", metadata.get("sha", "")):
        raise ValueError("actual tested commit SHA is required")
    checks = dict(metadata["checks"])
    module_checks = metadata.get("module_checks")
    if module_checks is not None:
        if set(module_checks) != set(MODULE_REPORTS):
            raise ValueError("explicit outcomes for every platform module are required")
        checks.update(module_checks)
    check_names = CHECKS + (tuple(MODULE_REPORTS) if module_checks is not None else ())
    rows = []
    reports = []
    counts = Counter()
    for check in check_names:
        outcome = checks.get(check, "skipped")
        if outcome not in {"success", "failure", "skipped", "cancelled"}:
            raise ValueError("unknown CI step outcome")
        reason = ""
        if check in REPORTS or check in MODULE_REPORTS:
            try:
                filename, framework, prefix = MODULE_REPORTS.get(check, (REPORTS.get(check), check, ""))
                normalized, totals = normalize_junit(directory / filename, root, framework, prefix)
                filename = f"normalized-{check}.xml"
                (directory / filename).write_bytes(normalized)
                reports.append(filename)
                counts.update(totals)
                if totals["failed"] and outcome == "success":
                    outcome = "failure"
                if not totals["passed"] and not totals["failed"]:
                    outcome, reason = "skipped", "No tests executed; only skipped tests"
            except (OSError, ValueError, ET.ParseError):
                outcome, reason = "skipped", "Missing or invalid test report; verification incomplete"
        status = "passed" if outcome == "success" else "failed" if outcome == "failure" else "blocked"
        rows.append({
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, metadata["ci_url"] + "/" + check)),
            "title": f"CI verification: {check}", "signature": ("anymsg-ci::" if check in CHECKS else "deedkit-ci::") + check,
            "execution": {"status": status, "duration": 0},
            "suitePath": [{"title": "CI verifications"}],
            "fields": {"file_path": ".github/workflows/checks.yml"},
            "message": reason or f"GitHub Actions step outcome: {outcome}",
        })
    (directory / "ci-checks.json").write_text(json.dumps(rows, indent=2))
    manifest = {**metadata, "schema": 1, "reports": reports + ["ci-checks.json"],
                "test_counts": dict(counts), "check_counts": dict(Counter(r["execution"]["status"] for r in rows))}
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def resolve_branch(manifest: dict, mapping: dict) -> str:
    branch = manifest.get("tiden_branch") or mapping.get(manifest["git_branch"])
    if not branch and manifest["git_branch"] == "main":
        branch = "main"
    if not isinstance(branch, str) or not branch:
        raise ValueError("No Tiden branch mapping; register this Git branch before publishing evidence")
    return branch


def publish(directory: Path, manifest: dict, branch: str, product: str, cli: str = "tiden") -> dict:
    if manifest.get("schema") != 1 or not re.fullmatch(r"[a-f0-9]{40}", manifest.get("sha", "")):
        raise ValueError("invalid evidence manifest")

    def call(arguments: list[str], label: str, allow_verdict: bool = False) -> dict:
        completed = subprocess.run([cli, *arguments, "--product-id", product, "--format", "json"],
                                   capture_output=True, text=True, timeout=120)
        token = os.environ.get("TIDEN_API_TOKEN", "")
        output = completed.stdout.replace(token, "[redacted]") if token else completed.stdout
        (directory / f"tiden-{label}.json").write_text(output)
        if completed.returncode and not allow_verdict:
            raise RuntimeError(f"Tiden {label} failed; evidence was not verified")
        value = json.loads(output)
        if completed.returncode and value.get("status") != "failed":
            raise RuntimeError(f"Tiden {label} failed without a test verdict")
        return value

    branches = call(["branch", "list"], "branches")["branches"]
    expected = next((b["id"] for b in branches if b["name"] == branch), None)
    if not expected:
        raise ValueError("Mapped Tiden branch does not exist; refusing unassociated results")
    run = call(["run", "create", "--branch", branch, "--intent-session", "none",
                "--build-sha", manifest["sha"], "--environment", "github-actions-synthetic",
                "--title", f"GitHub CI {manifest['sha'][:12]}", "--description", manifest["ci_url"],
                "--client-meta", "git_branch=" + manifest["git_branch"],
                "--client-meta", "ci_url=" + manifest["ci_url"]], "create")
    seq = str(run["seqNum"])
    completed = False
    try:
        if run["branchName"] != branch or run["buildSha"] != manifest["sha"]:
            raise RuntimeError("Tiden rewrote the explicit source association")
        paths = []
        for filename in manifest["reports"]:
            if Path(filename).name != filename or (directory / filename).is_symlink():
                raise ValueError("invalid evidence file path")
            if not (directory / filename).is_file() or (directory / filename).stat().st_size > MAX_BYTES:
                raise ValueError("missing or oversized evidence")
            paths.append(str((directory / filename).resolve()))
        if "ci-checks.json" not in manifest["reports"]:
            raise ValueError("CI step evidence is required")
        call(["run", "report", seq, *paths], "report")
        result = call(["run", "complete", seq], "complete", allow_verdict=True)
        completed = True
        for attempt in range(20):
            result = call(["run", "get", seq], "final")
            if result.get("liveDocStatus") in {"succeeded", "failed", "skipped"}:
                break
            time.sleep(3)
        if result.get("liveDocStatus") != "succeeded" or result.get("branchId") != expected:
            raise RuntimeError("Run recorded, but Tiden did not associate test evidence with the expected branch")
        if result.get("status") not in {"passed", "failed"}:
            raise RuntimeError("Tiden run has no terminal test verdict")
        check_count = len(CHECKS) + (len(MODULE_REPORTS) if "module_checks" in manifest else 0)
        expected_attempts = sum(manifest["test_counts"].values()) + check_count
        if int(result["stats"]["attempts"]) != expected_attempts:
            raise RuntimeError("Tiden result count differs from the authentic CI evidence")
        expected_failure = (manifest["test_counts"].get("failed", 0)
                            or manifest["check_counts"].get("failed", 0)
                            or manifest["check_counts"].get("blocked", 0))
        if expected_failure and result["status"] != "failed":
            raise RuntimeError("Tiden verdict contradicts failed or incomplete CI evidence")
        return result
    finally:
        if not completed:
            call(["run", "abort", seq], "abort")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "publish"])
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    directory = args.directory.resolve()
    if args.command == "prepare":
        result = prepare(args.source_root.resolve(), directory,
                         json.loads((directory / "metadata.json").read_text()))
        print(json.dumps({"test_counts": result["test_counts"], "check_counts": result["check_counts"]}))
        return 0
    manifest = json.loads((directory / "manifest.json").read_text())
    branch = resolve_branch(manifest, json.loads(os.environ.get("TIDEN_BRANCH_MAP", "{}")))
    result = publish(directory, manifest, branch, os.environ["TIDEN_PRODUCT_ID"])
    print(json.dumps({k: result[k] for k in ("seqNum", "status", "branchName", "branchId", "buildSha", "stats", "liveDocStatus")}))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
