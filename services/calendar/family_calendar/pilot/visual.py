"""Keep owner declarations distinct from artifact-backed agent observations."""

import hashlib
import json
from pathlib import Path

from family_calendar.errors import CalendarError
from family_calendar.private_files import read_private

CORE = {"single_crud", "conditional_write", "single_cancel_series", "properties_tz_dst", "all_day"}
FUTURE = {"future_split_count", "future_split_until", "future_split_infinite"}
KNOWN = CORE | FUTURE | {"native_range_probe", "floating_roundtrip"}
IMAGES = {".png", ".jpg", ".jpeg"}


def _artifact(root, relative):
    path = Path(relative)
    if path.is_absolute() or not path.parts or any(p in {".", ".."} for p in path.parts):
        raise CalendarError("invalid_visual_artifact")
    full = Path(root) / path
    if any(p.is_symlink() for p in (full, *full.parents)):
        raise CalendarError("invalid_visual_artifact")
    data = read_private(full)
    if not data or len(data) > 10_485_760 or path.suffix not in IMAGES | {".txt"}:
        raise CalendarError("invalid_visual_artifact")
    if path.suffix == ".png" and not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise CalendarError("invalid_visual_artifact")
    if path.suffix in {".jpg", ".jpeg"} and not data.startswith(b"\xff\xd8\xff"):
        raise CalendarError("invalid_visual_artifact")
    if path.suffix == ".txt":
        data.decode("utf-8")
    return {"path": path.as_posix(), "sha256": hashlib.sha256(data).hexdigest()}


def agent_visual_evidence(root, path):
    declared = json.loads(read_private(path))["checks"]
    if not isinstance(declared, dict) or not declared or not set(declared) <= KNOWN:
        raise CalendarError("invalid_visual_evidence")
    checks = {}
    for name, item in declared.items():
        if item["result"] not in {"pass", "fail"} or not 2 <= len(item["artifacts"]) <= 32:
            raise CalendarError("invalid_visual_evidence")
        artifacts = [_artifact(root, relative) for relative in item["artifacts"]]
        suffixes = {Path(a["path"]).suffix for a in artifacts}
        if ".txt" not in suffixes or not suffixes & IMAGES:
            raise CalendarError("invalid_visual_evidence")
        checks[name] = {"result": item["result"], "artifacts": artifacts}
    return {"checks": checks}


def visual_verified(root, visual):
    if visual.get("source") == "owner_reported":
        return True
    if visual.get("source") != "agent_observed":
        return False
    try:
        checks = visual["checks"]
        if visual.get("result") not in {"pass", "fail"}:
            return False
        core_pass = all(checks[name]["result"] == "pass" for name in CORE)
        if core_pass != (visual["result"] == "pass"):
            return False
        future = visual.get("future_result", "not-checked")
        if future not in {"pass", "fail", "not-checked"}:
            return False
        if future != "not-checked":
            future_pass = all(checks[name]["result"] == "pass" for name in FUTURE)
            if future_pass != (future == "pass"):
                return False
        native = visual.get("native_range_result", "not-checked")
        if native != "not-checked" and checks["native_range_probe"]["result"] != native:
            return False
        if not set(checks) <= KNOWN:
            return False
        for item in checks.values():
            if item["result"] not in {"pass", "fail"}:
                return False
            if not 2 <= len(item["artifacts"]) <= 32:
                return False
            suffixes = {Path(a["path"]).suffix for a in item["artifacts"]}
            if ".txt" not in suffixes or not suffixes & IMAGES:
                return False
            for artifact in item["artifacts"]:
                if _artifact(root, artifact["path"]) != artifact:
                    return False
        return True
    except (CalendarError, OSError, ValueError, KeyError, TypeError):
        return False
