"""Stable repository entrypoint: the messaging wheel/sdist and both npm tarballs into dist/."""

from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
status = subprocess.call(
    [
        "uv",
        "build",
        "--project",
        str(root / "services/messaging"),
        "--out-dir",
        str(root / "dist"),
        *sys.argv[1:],
    ],
    cwd=root,
)
# anymsg (messaging HTTP client) and deedkit (gateway client) share the release version.
for package in ("packages/npm", "packages/deedkit"):
    if status == 0:
        status = subprocess.call(
            ["npm", "pack", "--ignore-scripts", "--offline", "--loglevel", "warn", "--pack-destination", str(root / "dist")],
            cwd=root / package,
        )
raise SystemExit(status)
