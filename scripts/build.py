"""Stable repository entrypoint for the independently packaged messaging service."""

from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
raise SystemExit(
    subprocess.call(
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
)
