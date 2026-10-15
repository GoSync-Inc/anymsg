#!/usr/bin/env python3
"""Compare exact objects/links. Changes require reviewed before/after digests."""
import argparse
import hashlib
import json
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def compare(before, after, allowed):
    diff = []
    for key in ("product_id", "workspace_id", "branch"):
        if before.get(key) != after.get(key):
            diff.append({"kind": "identity_changed", "field": key})
    for group in sorted(set(before["inventory"]) | set(after["inventory"])):
        old = {x["id"]: x for x in before["inventory"].get(group, [])}
        new = {x["id"]: x for x in after["inventory"].get(group, [])}
        if len(old) != len(before["inventory"].get(group, [])) or len(new) != len(after["inventory"].get(group, [])):
            diff.append({"kind": "duplicate_id", "collection": group})
        for identity in sorted(old.keys() - new.keys()):
            diff.append({"kind": "removed", "collection": group, "id": identity})
        for identity in sorted(new.keys() - old.keys()):
            if identity not in allowed.get("additions", {}).get(group, []):
                diff.append({"kind": "unreviewed_addition", "collection": group, "id": identity})
        for identity in sorted(old.keys() & new.keys()):
            if old[identity] == new[identity]:
                continue
            permit = allowed.get("changes", {}).get(group, {})
            permit = permit.get(identity) if isinstance(permit, dict) else None
            expected = {"before_sha256": digest(old[identity]), "after_sha256": digest(new[identity])}
            if permit != expected:
                diff.append({"kind": "changed", "collection": group, "id": identity, **expected})
    return diff


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--allowed", type=Path)
    args = parser.parse_args()
    before, after = (json.loads(p.read_text()) for p in (args.before, args.after))
    for snapshot in (before, after):
        if snapshot.get("complete") is not True or snapshot.get("stable_reads") != 2:
            raise SystemExit("refusing incomplete/unverified snapshot")
        # Do not accept a mutated artifact under a stale signed digest.
        import importlib.util
        spec = importlib.util.spec_from_file_location("snapshot", Path(__file__).with_name("tiden_snapshot.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if module.fingerprint(snapshot["inventory"]) != snapshot.get("sha256"):
            raise SystemExit("snapshot digest mismatch")
    changes = compare(before, after, json.loads(args.allowed.read_text()) if args.allowed else {})
    print(json.dumps({"preserved": not changes, "diff": changes}, indent=2))
    raise SystemExit(bool(changes))


if __name__ == "__main__":
    main()
