"""Check resolved locks without importing or installing any service."""

import argparse
import json
from pathlib import Path
import tomllib


def check(root):
    policy = tomllib.loads((root / "config/stack.toml").read_text())
    if policy["version"] != 1:
        raise ValueError("unsupported stack policy")
    rows, errors = [], []
    for name, module in policy["modules"].items():
        lock = tomllib.loads((root / module["path"] / "uv.lock").read_text())
        versions = {p["name"]: p.get("version") for p in lock["package"]}
        if lock["requires-python"] != policy["python"]:
            errors.append(f"{name}: Python differs from the common stack")
        for package, expected in policy["common"].items():
            if versions.get(package) != expected:
                errors.append(f"{name}: {package} must resolve to {expected}")
        for package, expected in policy["optional_common"].items():
            if package in versions and versions[package] != expected:
                errors.append(
                    f"{name}: optional {package} differs from the common stack"
                )
        rows.append(
            {
                "module": name,
                "python": lock["requires-python"],
                "common": {key: versions.get(key) for key in policy["common"]},
            }
        )
    return {
        "version": 1,
        "status": "failed" if errors else "passed",
        "modules": rows,
        "errors": errors,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args()
    result = check(args.root)
    print(json.dumps(result, indent=2))
    raise SystemExit(bool(result["errors"]))
