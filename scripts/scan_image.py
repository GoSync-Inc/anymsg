#!/usr/bin/env python3
"""Scan every layer and the config of an exported image for secrets before it can be published."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / ".gitleaks-images.toml"
CANARY = ROOT / "tests/fixtures/gitleaks-canary/image.tar"
GZIP, ZSTD = b"\x1f\x8b", b"\x28\xb5\x2f\xfd"
FINDINGS = 2  # gitleaks --exit-code for findings; every other non-zero exit is a scanner error
CHUNK = 1024 * 1024


class Refused(RuntimeError):
    pass


def member(archive, name):
    handle = archive.extractfile(name)
    if handle is None:
        raise Refused(f"exported archive has no file {name}")
    return handle


def read_to_end(path):
    """Walk every member header and payload, so a truncated or malformed layer is refused here."""
    with tarfile.open(path, "r:") as layer:
        for entry in layer:
            if entry.isfile():
                payload = layer.extractfile(entry)
                while payload is not None and payload.read(CHUNK):
                    pass


def prepare(archive_path, directory):
    """Write each layer as a validated uncompressed layer-<n>.tar and the config as config.json.

    Nothing inside a layer is extracted: the scan sees deleted, overwritten and hidden files of
    lower layers too, because a published image ships every layer blob.
    """
    directory.mkdir(parents=True, exist_ok=False)
    try:
        with tarfile.open(archive_path, "r:") as archive:
            manifest = json.load(member(archive, "manifest.json"))
            if len(manifest) != 1:
                raise Refused("an exported archive must hold exactly one image")
            config_bytes = member(archive, manifest[0]["Config"]).read()
            diff_ids = json.loads(config_bytes)["rootfs"]["diff_ids"]
            layers = manifest[0]["Layers"]
            if len(layers) != len(diff_ids):
                raise Refused(f"{len(layers)} layers but {len(diff_ids)} diff_ids in the image config")
            for number, (name, diff_id) in enumerate(zip(layers, diff_ids, strict=True)):
                blob = member(archive, name)
                magic = blob.read(4)
                blob.seek(0)
                if magic.startswith(ZSTD):
                    raise Refused(f"layer {number} is zstd-compressed, which the scan does not support")
                source = gzip.GzipFile(fileobj=blob) if magic.startswith(GZIP) else blob
                target = directory / f"layer-{number}.tar"
                digest = hashlib.sha256()
                with target.open("wb") as out:
                    while chunk := source.read(CHUNK):
                        digest.update(chunk)
                        out.write(chunk)
                if "sha256:" + digest.hexdigest() != diff_id:
                    raise Refused(f"layer {number} does not match its diff_id")
                read_to_end(target)
    except (OSError, EOFError, tarfile.TarError, KeyError, TypeError, ValueError) as exc:
        raise Refused(f"exported archive {Path(archive_path).name} is unreadable ({type(exc).__name__})") from exc
    (directory / "config.json").write_bytes(config_bytes)
    return len(diff_ids)


def gitleaks():
    binary = os.environ.get("GITLEAKS") or shutil.which("gitleaks")
    if not binary:
        raise Refused("gitleaks is not installed")
    return binary


def scan(archive_path, *, config=CONFIG, binary=None):
    """Findings as (RuleID, layer, path inside the layer, StartLine); never the secret itself."""
    with tempfile.TemporaryDirectory() as work:
        work = Path(work)
        layers = prepare(archive_path, work / "scan")
        (work / "ignore").mkdir()
        report = work / "report.json"
        # The image cannot steer the scan: the config comes from this repository, the ignore path is
        # empty and inline gitleaks:allow comments are ignored.
        result = subprocess.run(
            [binary or gitleaks(), "dir", "--config", str(config), "--gitleaks-ignore-path", str(work / "ignore"),
             "--ignore-gitleaks-allow", "--max-archive-depth", "1", "--redact", "--no-banner", "--no-color",
             "--log-level", "warn", "--exit-code", str(FINDINGS), "--report-format", "json",
             "--report-path", str(report), str(work / "scan")],
            capture_output=True, text=True, timeout=1800)
        if result.returncode not in (0, FINDINGS):
            raise Refused(f"gitleaks failed (exit {result.returncode})")
        # Skipped or unreadable files are only logged, so any log line other than the findings
        # summary is a scanner problem.
        lines = [line for line in result.stderr.splitlines() if line.strip()]
        if (result.returncode == 0 and lines) or any("leaks found" not in line for line in lines):
            raise Refused("gitleaks reported a problem: " + " | ".join(lines)[-400:])
        findings = []
        for finding in json.loads(report.read_text() or "[]"):
            outer, _, inner = finding["File"].partition("!")
            findings.append((finding["RuleID"], Path(outer).name.removesuffix(".tar"), inner, finding["StartLine"]))
        if (result.returncode == FINDINGS) != bool(findings):
            raise Refused("gitleaks exit code and report disagree")
        return layers, sorted(findings)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, nargs="?", default=None)
    parser.add_argument("--canary", action="store_true", help="scan the committed canary and require a finding")
    args = parser.parse_args(argv)
    try:
        archive = CANARY if args.canary else args.archive
        if archive is None:
            raise Refused("name an archive or pass --canary")
        started = time.monotonic()
        layers, findings = scan(archive)
        for rule, layer, path, line in findings:
            print(f"finding: {rule} in {layer} at {path}:{line}")
        print(f"scanned {layers} layers and the config of {archive.name} in {time.monotonic() - started:.1f}s: "
              f"{len(findings)} findings")
        if args.canary and not findings:
            raise Refused("the canary was not detected; the scanner, its config or the helper is broken")
        if findings and not args.canary:
            raise Refused("secrets found in the image; fix the image or add a reviewed allowlist entry")
    except Refused as exc:
        parser.exit(1, "image scan refused: " + str(exc) + "\n")


if __name__ == "__main__":
    sys.exit(main())
