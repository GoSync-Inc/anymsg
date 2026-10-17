"""Preserve real CI outcomes and explicitly associate evidence with a Tiden branch.

The preparation runner has no Tiden credentials. The publishing runner executes
only this tool and the pinned Tiden CLI, never tests or application code.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
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
    "load": ("load.xml", "python", ""),
    "gateway": ("gateway.xml", "python", "services/gateway"),
    "calendar": ("calendar.xml", "python", "services/calendar"),
    "calls": ("calls.xml", "python", "services/calls"),
    "calls_frontend": ("calls-frontend.xml", "node", "services/calls/frontend"),
    "backup_restore": ("backup-restore.xml", "python", ""),
}
MAX_BYTES = 20 * 1024 * 1024
# Tiden branch names: lowercase letters, digits, slashes, underscores and hyphens.
TIDEN_BRANCH_NAME = re.compile(r"[a-z0-9]+(?:[/_-][a-z0-9]+)*")
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
# The intent loop puts exactly this line, naming the session's Tiden branch, in the pull request description.
TIDEN_INTENT_LINE = re.compile(
    rf"^[ \t]*Tiden intent branch:[ \t]*\[(intent/[a-z0-9]+(?:[/_-][a-z0-9]+)*)\]"
    rf"\(https://app\.tiden\.ai/({_UUID})/products/({_UUID})/branches/({_UUID})\)", re.MULTILINE)
MAX_INTENT_LINKS = 5
MERGE_QUEUE_SKIP = "merge queue run of a temporary group commit"
# Tiden creates an environment for any unknown slug, so only these two may receive a release record.
RELEASE_ENVIRONMENTS = ("github-actions-synthetic", "production")
RUN_URL = re.compile(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/actions/runs/[0-9]+(?:/attempts/[0-9]+)?")
RELEASED_AT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")


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


def route_branch(manifest: dict, mapping: dict) -> tuple[str, bool]:
    """Return the Tiden branch and whether Tiden itself routes the run.

    An explicit mapping always wins. An unmapped feature branch is reported under
    its own Git branch name: Tiden attributes the run to the intent branch whose
    session recorded that Git branch, otherwise to a branch of the same name.
    """
    try:
        return resolve_branch(manifest, mapping), False
    except ValueError:
        git_branch = manifest["git_branch"]
        if git_branch == "main" or not TIDEN_BRANCH_NAME.fullmatch(git_branch):
            raise ValueError("No Tiden branch mapping and the Git branch is not a valid Tiden branch name; "
                             "register this Git branch before publishing evidence") from None
        return git_branch, True


def pull_request_body(event_path: str | None) -> str:
    """The pull request description from the GitHub event file, or empty outside a pull request."""
    try:
        event = json.loads(Path(event_path).read_text()) if event_path else {}
    except (OSError, ValueError):
        return ""
    body = (event.get("pull_request") or {}).get("body") if isinstance(event, dict) else None
    return body if isinstance(body, str) else ""


def intent_links(text: str) -> list[dict]:
    """`Tiden intent branch:` lines of a pull request description, as names and IDs only."""
    links = []
    for name, workspace, product, branch in TIDEN_INTENT_LINE.findall(text or ""):
        link = {"name": name, "workspace": workspace, "product": product, "branch": branch}
        if link not in links:
            links.append(link)
    return links[:MAX_INTENT_LINKS]


def linked_intent_branch(links: list, branches: list, product: str, workspace: str) -> str | None:
    """The one open intent branch of this product that the pull request links to, if unambiguous.

    The link text must name the same branch its ID resolves to.
    """
    wanted = {(link.get("branch"), link.get("name")) for link in links
              if link.get("product") == product and link.get("workspace") == workspace}
    names = {b["name"] for b in branches if (b.get("id"), b.get("name")) in wanted and b.get("status") == "open"
             and isinstance(b.get("name"), str) and b["name"].startswith("intent/")}
    return names.pop() if len(names) == 1 else None


def merge_queue_run(manifest: dict) -> bool:
    """A GitHub merge queue run tests a temporary `gh-readonly-queue/` group commit."""
    git_branch = manifest.get("git_branch")
    return manifest.get("event") == "merge_group" or (
        isinstance(git_branch, str) and git_branch.startswith("gh-readonly-queue/"))


def routed_target(name: object, git_branch: str) -> bool:
    return isinstance(name, str) and (name == git_branch or name.startswith("intent/"))


def tiden(cli: str, arguments: list[str], product: str, directory: Path, label: str) -> tuple[int, str]:
    """Run one Tiden CLI command and keep its redacted JSON output as a receipt."""
    completed = subprocess.run([cli, *arguments, "--product-id", product, "--format", "json"],
                               capture_output=True, text=True, timeout=120)
    token = os.environ.get("TIDEN_API_TOKEN", "")
    output = completed.stdout.replace(token, "[redacted]") if token else completed.stdout
    (directory / f"tiden-{label}.json").write_text(output)
    return completed.returncode, output


def publish(directory: Path, manifest: dict, branch: str | None, product: str, cli: str = "tiden",
            routed: bool = False, workspace: str = "") -> dict:
    if manifest.get("schema") != 1 or not re.fullmatch(r"[a-f0-9]{40}", manifest.get("sha", "")):
        raise ValueError("invalid evidence manifest")

    def call(arguments: list[str], label: str, allow_verdict: bool = False) -> dict:
        returncode, output = tiden(cli, arguments, product, directory, label)
        if returncode and not allow_verdict:
            raise RuntimeError(f"Tiden {label} failed; evidence was not verified")
        value = json.loads(output)
        if returncode and value.get("status") != "failed":
            raise RuntimeError(f"Tiden {label} failed without a test verdict")
        return value

    def skip(reason: str, level: str) -> dict:
        # A merge queue group, or a pull request without its intent branch, has no Tiden home;
        # main publishes the merged code.
        (directory / "tiden-route.json").write_text(json.dumps({"branch": None, "source": "skipped",
                                                                "reason": reason}))
        print(f"::{level} title=Tiden evidence not published::{reason}; main CI publishes it after merge")
        return {"seqNum": None, "status": "skipped", "branchName": None, "branchId": None,
                "buildSha": manifest["sha"], "stats": {}, "liveDocStatus": None}

    if merge_queue_run(manifest):
        # Before any Tiden call and whatever target was chosen: a queue run never publishes.
        return skip(MERGE_QUEUE_SKIP, "notice")
    pull_request = manifest.get("event") == "pull_request"
    if routed and pull_request and not manifest.get("pr_tiden_links"):
        return skip("pull request description has no Tiden intent branch link", "notice")
    branches = call(["branch", "list"], "branches")["branches"]
    source = "routed" if routed else "explicit"
    if routed and manifest.get("pr_tiden_links"):
        # An explicit target already won in route_branch; otherwise prefer the linked intent branch.
        linked = linked_intent_branch(manifest["pr_tiden_links"], branches, product, workspace)
        if linked:
            branch, routed, source = linked, False, "pull-request-link"
    if routed and pull_request:
        return skip("the pull request's Tiden intent branch link is not usable: its text must name the "
                    "branch its ID resolves to, and that must be the one open intent branch of this "
                    "workspace and product", "warning")
    if branch is None:
        raise ValueError("No Tiden branch mapping, no usable pull request intent link, and the Git branch "
                         "is not a valid Tiden branch name; register this Git branch before publishing evidence")
    (directory / "tiden-route.json").write_text(json.dumps({"branch": branch, "source": source}))
    expected = next((b["id"] for b in branches if b["name"] == branch), None)
    if not expected and routed:
        # Tiden skips live-doc sync for a branch name it does not know, so create it first.
        try:
            call(["branch", "create", "--name", branch,
                  "--description", "CI evidence for Git branch " + branch], "branch-create")
        except (RuntimeError, ValueError):
            pass  # A concurrent run may have created it; the listing below decides.
        branches = call(["branch", "list"], "branches-created")["branches"]
        expected = next((b["id"] for b in branches if b["name"] == branch), None)
    if not expected:
        raise ValueError("Tiden branch does not exist; refusing unassociated results")
    run = call(["run", "create", "--branch", branch, "--intent-session", "none",
                "--build-sha", manifest["sha"], "--environment", "github-actions-synthetic",
                "--title", f"GitHub CI {manifest['sha'][:12]}", "--description", manifest["ci_url"],
                "--client-meta", "git_branch=" + manifest["git_branch"],
                "--client-meta", "ci_url=" + manifest["ci_url"]], "create")
    seq = str(run["seqNum"])
    completed = False
    try:
        named = routed_target(run["branchName"], branch) if routed else run["branchName"] == branch
        if not named or run["buildSha"] != manifest["sha"]:
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
        if routed and result.get("liveDocStatus") == "succeeded" and result.get("branchId") != expected:
            # Tiden may attribute the run to an intent branch linked to this Git branch.
            final = call(["branch", "list"], "branches-final")["branches"]
            expected = next((b["id"] for b in final if b["id"] == result.get("branchId")
                             and routed_target(b["name"], branch)), None)
        if result.get("liveDocStatus") != "succeeded" or not expected or result.get("branchId") != expected:
            raise RuntimeError("Run recorded, but Tiden did not associate test evidence with the expected branch "
                               f"(live doc {result.get('liveDocStatus')}, branch {result.get('branchId')})")
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


class StaleRelease(RuntimeError):
    """The latest completed main run is not this commit's, so a new release would capture another gate."""


def record_release(directory: Path, sha: str, environment: str, url: str, released_at: str, product: str,
                   cli: str = "tiden") -> dict:
    """Record release deedkit@<sha> once this commit's passed run is the latest completed main run.

    Tiden captures a release's gate from the current main gate when the release is first
    created; a repeated create updates its name, URL and time but keeps that first gate.
    """
    if not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise ValueError("an exact lowercase 40-character commit SHA is required")
    if environment not in RELEASE_ENVIRONMENTS:
        raise ValueError("unknown release environment; Tiden would create a duplicate")
    if not RUN_URL.fullmatch(url):
        raise ValueError("the release URL must be a GitHub Actions run")
    if not RELEASED_AT.fullmatch(released_at):
        raise ValueError("the release time must be RFC3339 UTC, YYYY-MM-DDTHH:MM:SSZ")
    moment = datetime.strptime(released_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    if moment > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError("the release time is in the future")

    def call(arguments: list[str], label: str) -> dict:
        returncode, output = tiden(cli, arguments, product, directory, label)
        if returncode:
            raise RuntimeError(f"Tiden {label} failed; the release was not verified")
        return json.loads(output)

    def latest_main_run(label: str) -> dict:
        runs = call(["run", "list", "--branch", "main", "--environment", "github-actions-synthetic",
                     "--page-size", "20"], label)["runs"]
        completed = [run for run in runs if run.get("status") in {"passed", "failed"} and run.get("completedAt")]
        return max(completed, key=lambda run: run["completedAt"], default={})

    before = latest_main_run("release-main-before")
    if before.get("buildSha") != sha or before.get("status") != "passed":
        raise StaleRelease("this commit's passed run is not the latest completed main run in Tiden")
    version = "deedkit@" + sha
    release = call(["release", "create", "--version", version, "--environment", environment,
                    "--name", "DeedKit " + sha[:12], "--url", url, "--released-at", released_at], "release")
    if (release.get("version"), release.get("environmentSlug"), release.get("url")) != (version, environment, url):
        raise RuntimeError("Tiden rewrote the release identity")
    if latest_main_run("release-main-after").get("id") != before["id"]:
        raise RuntimeError("another main run completed while the release was recorded; its gate may belong to it")
    returncode, output = tiden(cli, ["gate", "check", "--release", release["id"]], product, directory, "release-gate")
    try:
        gate = json.loads(output)
    except json.JSONDecodeError:
        gate = {}
    if returncode and not gate:
        print("::warning title=Tiden release gate::no gate was captured for " + version)
    receipt = {"releaseId": release["id"], "version": version, "environment": environment, "url": url,
               "releasedAt": released_at, "mainRun": before["id"],
               "gate": gate.get("status"), "gateComputedAt": gate.get("computedAt")}
    (directory / "tiden-release-receipt.json").write_text(json.dumps(receipt, indent=2))
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "publish", "release"])
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, default=Path.cwd())
    parser.add_argument("--environment", choices=RELEASE_ENVIRONMENTS)
    parser.add_argument("--sha")
    parser.add_argument("--url")
    parser.add_argument("--released-at")
    args = parser.parse_args()
    directory = args.directory.resolve()
    if args.command == "release":
        return release(args, directory)
    if args.command == "prepare":
        metadata = json.loads((directory / "metadata.json").read_text())
        metadata["pr_tiden_links"] = intent_links(pull_request_body(os.environ.get("GITHUB_EVENT_PATH")))
        metadata["event"] = os.environ.get("GITHUB_EVENT_NAME", "")
        result = prepare(args.source_root.resolve(), directory, metadata)
        print(json.dumps({"test_counts": result["test_counts"], "check_counts": result["check_counts"]}))
        return 0
    manifest = json.loads((directory / "manifest.json").read_text())
    if merge_queue_run(manifest):
        # publish() skips a queue run before any Tiden call: no credentials, map or route needed.
        result = publish(directory, manifest, None, "")
    else:
        mapping = json.loads(os.environ.get("TIDEN_BRANCH_MAP") or "{}")  # a malformed map fails loudly
        try:
            branch, routed = route_branch(manifest, mapping)
        except ValueError:
            if not manifest.get("pr_tiden_links") and manifest.get("event") != "pull_request":
                raise
            branch, routed = None, True  # publish() resolves the pull request's intent link or skips
        result = publish(directory, manifest, branch, os.environ["TIDEN_PRODUCT_ID"], routed=routed,
                         workspace=os.environ.get("TIDEN_WORKSPACE_ID", ""))
    print(json.dumps({k: result[k] for k in ("seqNum", "status", "branchName", "branchId", "buildSha", "stats", "liveDocStatus")}))
    return 0 if result["status"] in {"passed", "skipped"} else 1


def release(args: argparse.Namespace, directory: Path) -> int:
    if args.environment == "github-actions-synthetic":
        # Only this main push run's own published evidence names the commit and its build URL.
        if args.sha or args.url or args.released_at:
            raise ValueError("a CI release takes its commit, URL and time from the published run")
        manifest = json.loads((directory / "manifest.json").read_text())
        if (manifest.get("event"), manifest.get("git_branch"), manifest.get("tiden_branch")) != ("push", "main", ""):
            raise ValueError("only a main push run records a CI release")
        sha, url = manifest["sha"], manifest["ci_url"]
        released_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    elif args.environment == "production":
        if not (args.sha and args.url and args.released_at):
            raise ValueError("a production release needs the promoted commit, its CI run URL and the apply time")
        sha, url, released_at = args.sha, args.url, args.released_at
    else:
        raise ValueError("select the release environment")
    try:
        receipt = record_release(directory, sha, args.environment, url, released_at, os.environ["TIDEN_PRODUCT_ID"])
    except StaleRelease as exc:
        if args.environment == "production":
            raise
        # A newer main commit finished first; its own run records its release.
        print(f"::warning title=Tiden release not recorded::{exc}")
        return 0
    print(json.dumps(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
