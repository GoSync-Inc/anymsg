"""Verify saved evidence locally. No account credentials or network access."""

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

from family_calendar.errors import CalendarError
from family_calendar.pilot.journal import decode_intent
from family_calendar.private_files import read_private
from family_calendar.providers.caldav.codec import semantic_hash
from family_calendar.providers.caldav.transport import within_collection

CORE = {"single_crud", "conditional_write", "single_cancel_series", "properties_tz_dst", "all_day"}
FUTURE = {"future_split_count", "future_split_until", "future_split_infinite"}


def validate_evidence(root):
    root = Path(root).resolve()
    issues = []
    scope = json.loads(read_private(root / "scope.json"))
    manifest = json.loads(read_private(root / "manifest.json"))
    report = json.loads(read_private(root / "report.json"))
    path = root / "journal.sqlite"
    read_private(path)  # Owner, mode, regular-file and symlink validation before opening read-only.
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        steps = {row["id"]: dict(row) for row in db.execute("SELECT * FROM steps")}
    finally:
        db.close()
    if (
        scope["schema_version"] != 1
        or report["schema_version"] != 1
        or report["scope"] != {k: scope[k] for k in ("connection_id", "calendar_ref")}
        or report["run_id"] != manifest["run_id"]
        or report["environment"] != manifest["environment"]
    ):
        issues.append("scope_mismatch")
    reported = {x["step_id"]: x for x in report["operations"]}
    if not reported or len(reported) != len(report["operations"]):
        issues.append("invalid_operation_inventory")
    for step_id, item in reported.items():
        row = steps.get(step_id)
        if row is None:
            issues.append(f"missing_step:{step_id}")
            continue
        intent = decode_intent(row["intent"])
        if (
            item["state"] != row["state"]
            or item["request_id"] != row["request_id"]
            or item["kind"] != intent["kind"]
            or item["error_code"] != row["error_code"]
            or item["updated_at"] != row["updated_at"]
        ):
            issues.append(f"receipt_mismatch:{step_id}")
        if hashlib.sha256(row["intent"].encode()).hexdigest() != row["payload_hash"]:
            issues.append(f"intent_hash_mismatch:{step_id}")
        if not within_collection(intent["href"], manifest["collection"]):
            issues.append(f"step_outside_calendar:{step_id}")
        if row["state"] == "verified":
            try:
                if intent["kind"] == "delete":
                    matched = row["observed_data"] is None and row["observed_hash"] == "absent"
                else:
                    matched = (
                        semantic_hash(row["observed_data"])
                        == row["observed_hash"]
                        == semantic_hash(intent["desired"])
                    )
                if not matched:
                    issues.append(f"invalid_readback:{step_id}")
            except (CalendarError, TypeError):
                issues.append(f"invalid_readback:{step_id}")
    # Cleanup adds separate receipts. Other new steps require a refreshed report.
    if any(x not in reported and not row["request_id"].startswith("cleanup:") for x, row in steps.items()):
        issues.append("unreported_operations")
    complete_checks = set()
    for name, check in report["checks"].items():
        if check["status"] != "server_verified":
            continue
        ids = check["step_ids"]
        valid = bool(ids)
        for step_id in ids:
            row = steps.get(step_id)
            if row is None or step_id not in reported:
                valid = False
                continue
            expected_conflict = (
                name == "conditional_write"
                and row["request_id"] == "conditional:stale-version"
                and row["state"] == "conflict"
                and row["error_code"] == "version_conflict"
            )
            if row["state"] != "verified" and not expected_conflict:
                valid = False
        if not valid:
            issues.append(f"unconfirmed_check:{name}")
        else:
            complete_checks.add(name)
    unresolved = [
        i
        for i, row in steps.items()
        if i in reported and row["state"] in {"prepared", "sending", "unknown", "needs_attention"}
    ]
    if sorted(unresolved) != sorted(report["unresolved_steps"]):
        issues.append("unresolved_inventory_mismatch")
    gate = report["gate_a"]
    if gate not in {"not_accepted", "accepted_core", "accepted_core_and_future"}:
        issues.append("invalid_acceptance")
    elif gate != "not_accepted":
        visual_path = root / "visual-check.json"
        visual = json.loads(read_private(visual_path)) if visual_path.exists() else {}
        if (
            report["environment"] != "icloud_live"
            or report["client_visual_check"] != "pass"
            or visual.get("result") != "pass"
            or visual.get("source") != "owner_reported"
            or not CORE <= complete_checks
            or unresolved
            or (
                gate == "accepted_core_and_future"
                and (
                    not FUTURE <= complete_checks
                    or visual.get("future_result") != "pass"
                    or report.get("future_visual_check") != "pass"
                )
            )
        ):
            issues.append("invalid_acceptance")
    return {
        "valid": not issues,
        "accepted": not issues and gate != "not_accepted",
        "gate_a": gate,
        "environment": report["environment"],
        "verified_checks": sorted(complete_checks),
        "issues": issues,
    }


def main(argv=None):
    p = argparse.ArgumentParser(description="Check saved pilot receipts without network access")
    p.add_argument("--run-dir", type=Path, required=True)
    p.add_argument(
        "--allow-unaccepted", action="store_true", help="Validate structure before live UI acceptance"
    )
    args = p.parse_args(argv)
    try:
        result = validate_evidence(args.run_dir)
    except (CalendarError, OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        print(json.dumps({"valid": False, "accepted": False, "error": "evidence_unreadable"}))
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result["valid"] and (result["accepted"] or args.allow_unaccepted) else 2
