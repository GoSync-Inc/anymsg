import base64
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import random
import re
import shutil
import subprocess
import tarfile
import tomllib

import pytest
import yaml

from scripts import scan_image as si

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT / "config/gitleaks/gitleaks-8.30.1-default.toml"
UPSTREAM_SHA256 = "e163e53b9e7e8a8511e77271e2b323ed057759542a6d988258afe3a1fa329caf"
BINARY = os.environ.get("GITLEAKS") or shutil.which("gitleaks")
needs_gitleaks = pytest.mark.skipif(not BINARY, reason="the pinned gitleaks binary is not installed; images.yml always runs it")


def made_up_aws_pair(seed):
    """A made-up AWS key pair in the format gitleaks' aws-access-token rule matches; never a real key."""
    rng = random.Random(seed)
    key_id = "AKIA" + "".join(rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567") for _ in range(16))
    secret = "".join(rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789") for _ in range(40))
    return key_id, secret


def made_up_private_key(seed):
    """A made-up PEM block of random bytes: matches the private-key rule, is no usable key."""
    body = base64.encodebytes(random.Random(seed).randbytes(600)).decode()
    return "-----BEGIN " + "PRIVATE KEY-----\n" + body + "-----END " + "PRIVATE KEY-----\n"


def layer_tar(files):
    """An uncompressed layer tar: {path: text or bytes}; a None value is a whiteout or opaque marker."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name, data in files.items():
            data = b"" if data is None else data.encode() if isinstance(data, str) else data
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def image_archive(path, layers, *, env=(), history=(), gzip_layers=(), diff_ids=None, raw_layers=None):
    """A hand-made single-image `docker save` archive (Docker 25+ layout); no Docker needed."""
    raw = [layer_tar(files) for files in layers] if raw_layers is None else raw_layers
    config = {"architecture": "amd64", "os": "linux", "config": {"Env": list(env), "Labels": {}},
              "history": [{"created_by": entry} for entry in history],
              "rootfs": {"type": "layers", "diff_ids": diff_ids if diff_ids is not None
                         else ["sha256:" + hashlib.sha256(data).hexdigest() for data in raw]}}
    config_bytes = json.dumps(config).encode()
    blobs = {"sha256:" + hashlib.sha256(config_bytes).hexdigest(): config_bytes}
    names = []
    for number, data in enumerate(raw):
        stored = gzip.compress(data, mtime=0) if number in gzip_layers else data
        digest = "sha256:" + hashlib.sha256(stored).hexdigest()
        blobs[digest] = stored
        names.append("blobs/sha256/" + digest.removeprefix("sha256:"))
    config_name = "blobs/sha256/" + hashlib.sha256(config_bytes).hexdigest()
    manifest = json.dumps([{"Config": config_name, "RepoTags": ["deedkit-check-test:latest"], "Layers": names}]).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w") as tar:
        for name, data in [("manifest.json", manifest)] + [("blobs/sha256/" + d.removeprefix("sha256:"), b) for d, b in blobs.items()]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


def settings(seed=1):
    key_id, secret = made_up_aws_pair(seed)
    return f'aws_access_key_id = "{key_id}"\naws_secret_access_key = "{secret}"\n'


def findings(tmp_path, **kwargs):
    archive = image_archive(tmp_path / "image.tar", **kwargs)
    return si.scan(archive, binary=BINARY)[1]


# Helper: layers are validated and copied, never extracted.

def test_each_layer_becomes_a_tar_matching_its_diff_id_and_nothing_is_extracted(tmp_path):
    layers = [{"app/one.txt": "one"}, {"app/two.txt": "two", ".wh.app/one.txt": None}]
    archive = image_archive(tmp_path / "image.tar", layers=layers, gzip_layers={1})
    assert si.prepare(archive, tmp_path / "scan") == 2
    assert sorted(p.name for p in (tmp_path / "scan").iterdir()) == ["config.json", "layer-0.tar", "layer-1.tar"]
    for number, files in enumerate(layers):
        assert (tmp_path / "scan" / f"layer-{number}.tar").read_bytes() == layer_tar(files)  # gzip layer decompressed
    config = json.loads((tmp_path / "scan/config.json").read_text())
    assert config["rootfs"]["diff_ids"] == ["sha256:" + hashlib.sha256(layer_tar(f)).hexdigest() for f in layers]


@pytest.mark.parametrize("change, message", [
    (dict(diff_ids=["sha256:" + "0" * 64]), "2 layers but 1 diff_ids"),
    (dict(diff_ids=["sha256:" + "0" * 64, "sha256:" + "1" * 64]), "does not match its diff_id"),
    (dict(raw_layers=[layer_tar({"a": "a"}), layer_tar({"b": "b" * 4096})[:700]]), "unreadable|diff_id"),
    (dict(raw_layers=[layer_tar({"a": "a"}), si.ZSTD + b"\x00" * 64]), "zstd"),
], ids=["layer-count", "diff-id", "truncated", "zstd"])
def test_a_layer_that_cannot_be_validated_is_refused_before_any_scan(tmp_path, change, message):
    layers = [{"a": "a"}, {"b": "b"}]
    archive = image_archive(tmp_path / "image.tar", layers=layers, **change)
    with pytest.raises(si.Refused, match=message):
        si.scan(archive, binary="/nonexistent/gitleaks-must-not-run")


def test_a_truncated_layer_with_a_matching_digest_is_refused_by_the_tar_walk(tmp_path):
    truncated = layer_tar({"b": "b" * 4096})[:700]  # its diff_id is computed over the truncated bytes
    archive = image_archive(tmp_path / "image.tar", layers=None, raw_layers=[truncated])
    with pytest.raises(si.Refused, match="unreadable"):
        si.prepare(archive, tmp_path / "scan")


# Configuration: default rules without the default global path exemptions.

def test_image_config_keeps_the_pinned_default_rules_without_global_path_exemptions():
    assert hashlib.sha256(UPSTREAM.read_bytes()).hexdigest() == UPSTREAM_SHA256
    upstream = tomllib.loads(UPSTREAM.read_text())
    ours = tomllib.loads(si.CONFIG.read_text())
    assert ours["rules"] == upstream["rules"]
    assert "paths" not in ours["allowlist"] and upstream["allowlist"]["paths"]
    assert {k: v for k, v in ours["allowlist"].items() if k != "paths"} == \
        {k: v for k, v in upstream["allowlist"].items() if k != "paths"}
    assert "extend" not in ours and "disabledRules" not in json.dumps(ours)
    for entry in ours.get("allowlists", []):
        assert entry.get("description", "").strip(), entry
        assert entry.get("paths") or entry.get("regexes") or entry.get("commits"), entry
        assert not any(re.fullmatch(r"\(\?:\^\|/\)[^()]*\(\?:/\.\*\)\?\$", p) for p in entry.get("paths", [])), entry


# Real pinned gitleaks: what a public image would expose is found.

@needs_gitleaks
@pytest.mark.parametrize("path", ["app/settings.py", "usr/local/lib/python3.13/site-packages/pkg/settings.py",
                                  "app/node_modules/pkg/settings.js", "usr/lib/python3/dist-packages/x-1.0.dist-info/RECORD"])
def test_a_credential_in_any_layer_path_is_found(tmp_path, path):
    found = findings(tmp_path, layers=[{"etc/clean.txt": "nothing"}, {path: settings()}])
    assert ("aws-access-token", "layer-1", path, 1) in found


@needs_gitleaks
@pytest.mark.parametrize("hide", [{".wh.settings.py": None}, {"app/settings.py": "replaced\n"}, {"app/.wh..wh..opq": None}],
                         ids=["whiteout", "overwritten", "opaque"])
def test_a_credential_hidden_by_a_later_layer_is_still_found(tmp_path, hide):
    hide = {f"app/{k}" if not k.startswith("app/") else k: v for k, v in hide.items()}
    found = findings(tmp_path, layers=[{"app/settings.py": settings()}, hide])
    assert ("aws-access-token", "layer-0", "app/settings.py", 1) in found


@needs_gitleaks
def test_a_credential_in_a_gzip_layer_is_found(tmp_path):
    assert ("aws-access-token", "layer-0", "app/settings.py", 1) in \
        findings(tmp_path, layers=[{"app/settings.py": settings()}], gzip_layers={0})


@needs_gitleaks
@pytest.mark.parametrize("where", ["env", "history"])
def test_a_credential_only_in_the_image_config_is_found(tmp_path, where):
    key_id, _ = made_up_aws_pair(3)
    value = {"env": dict(env=[f"AWS_ACCESS_KEY_ID={key_id}"]), "history": dict(history=[f"RUN export AWS_KEY={key_id}"])}
    found = findings(tmp_path, layers=[{"app/clean.txt": "clean"}], **value[where])
    assert any(rule == "aws-access-token" and layer == "config.json" for rule, layer, _, _ in found)


@needs_gitleaks
@pytest.mark.parametrize("files", [
    {".gitleaks.toml": '[allowlist]\npaths = [".*"]\n'},
    {".gitleaksignore": "*\n"},
    {"app/allowed.py": None},
], ids=["config-in-image", "ignore-file-in-image", "inline-allow"])
def test_an_image_cannot_suppress_its_own_findings(tmp_path, files):
    key_id, secret = made_up_aws_pair(4)
    files = {name: data if data is not None else f'aws_access_key_id = "{key_id}"  # gitleaks:allow\n'
             for name, data in files.items()}
    found = findings(tmp_path, layers=[{**files, "app/settings.py": settings(4)}])
    assert any(rule == "aws-access-token" for rule, *_ in found)


@needs_gitleaks
def test_findings_never_print_the_credential(tmp_path, capsys, monkeypatch):
    archive = image_archive(tmp_path / "image.tar", layers=[{"app/settings.py": settings(5)}])
    monkeypatch.setenv("GITLEAKS", BINARY or "")
    with pytest.raises(SystemExit):
        si.main([str(archive)])
    out = capsys.readouterr()
    key_id, secret = made_up_aws_pair(5)
    assert "finding: aws-access-token in layer-0 at app/settings.py:1" in out.out
    assert key_id not in out.out + out.err and secret not in out.out + out.err
    assert "secrets found" in out.err


@needs_gitleaks
def test_an_archive_nested_in_a_layer_is_skipped_but_any_other_scanner_message_refuses(tmp_path, monkeypatch):
    nested = gzip.compress(settings(6).encode(), mtime=0)
    assert findings(tmp_path, layers=[{"usr/share/doc/pkg/changelog.gz": nested}]) == []
    unreadable = tmp_path / "unreadable"
    unreadable.mkdir()

    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, "", "2:00PM WRN skipping file: permission denied path=x\n")
    monkeypatch.setattr(si.subprocess, "run", run)
    with pytest.raises(si.Refused, match="permission denied"):
        si.scan(image_archive(unreadable / "image.tar", layers=[{"a": "a"}]), binary="gitleaks")


@needs_gitleaks
def test_a_clean_image_passes(tmp_path):
    assert findings(tmp_path, layers=[{"app/clean.txt": "nothing to see"}]) == []


# Canary: the committed archive must always be detected.

@needs_gitleaks
def test_the_committed_canary_is_detected_through_the_helper(capsys):
    si.main(["--canary"])
    assert "finding: private-key in layer-0 at usr/local/lib/python3.13/site-packages/deedkit_canary/key.pem" in \
        capsys.readouterr().out


def test_the_canary_keeps_its_key_out_of_plain_text():
    # The key layer is gzip-compressed, so the git history scan and push protection never see a key.
    assert b"PRIVATE KEY" not in si.CANARY.read_bytes()


def test_the_canary_is_a_valid_archive_whose_layer_carries_an_inline_allow(tmp_path):
    si.prepare(si.CANARY, tmp_path / "scan")
    with tarfile.open(tmp_path / "scan/layer-0.tar") as layer:
        handle = layer.extractfile("usr/local/lib/python3.13/site-packages/deedkit_canary/key.pem")
        text = handle.read().decode() if handle else ""
    assert "PRIVATE KEY" in text and "gitleaks:allow" in text


# Workflow: the scan gates the upload in every exporting job.

def test_every_exporting_build_job_scans_the_image_before_keeping_it():
    root = ROOT
    images = yaml.safe_load((root / ".github/workflows/images.yml").read_text())
    security = yaml.safe_load((root / ".github/workflows/security.yml").read_text())
    steps = images["jobs"]["build"]["steps"]
    names = [step.get("name") or step["uses"].split("@")[0] for step in steps]
    export = names.index("Export the tested image and publish it to a local registry as a dry run")
    install = names.index("Install the pinned gitleaks")
    scan = names.index("Scan every layer and the config of the tested image for secrets")
    upload = names.index("Keep the tested image for the publish job")
    assert export < install < scan < upload
    assert steps[install]["if"] == steps[scan]["if"] == steps[export]["if"]
    assert "continue-on-error" not in steps[install] and "continue-on-error" not in steps[scan]
    # A pipe must not hide the scan's exit code (GitHub's default shell has no pipefail).
    assert steps[scan]["run"].splitlines()[0] == "set -o pipefail" and "shell" not in steps[scan]
    assert "python3 scripts/scan_image.py --canary" in steps[scan]["run"]
    assert 'python3 scripts/scan_image.py "$RUNNER_TEMP/images/image-$TARGET/image.tar"' in steps[scan]["run"]
    # The same release URL and SHA-256 as security.yml's pinned download.
    url = re.compile(r"https://github\.com/gitleaks/gitleaks/releases/download/\S+\.tar\.gz")
    ours_url = url.search(steps[install]["run"]).group(0)
    ours_sha = re.search(r'echo "([0-9a-f]{64})  \$RUNNER_TEMP/gitleaks\.tar\.gz" \| sha256sum --check --strict',
                         steps[install]["run"]).group(1)
    theirs = re.search(re.escape(ours_url) + r"\s*\\\s*\n\s*([0-9a-f]{64})",
                       (root / ".github/workflows/security.yml").read_text())
    assert theirs and theirs.group(1) == ours_sha
    assert security  # parsed, so the security workflow stays valid YAML


@pytest.mark.parametrize("path", ["scripts/scan_image.py", ".gitleaks-images.toml", "tests/fixtures/gitleaks-canary/image.tar"])
def test_a_scan_only_change_still_builds_and_scans_every_image(tmp_path, path):
    from tests.test_tiden_ci import image_decision, pull_request_checkout

    def change(root, _git):
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text("changed\n")
    checkout, head = pull_request_checkout(tmp_path, change)
    assert image_decision(checkout, tmp_path, "pull_request", head)[0] == ["build=true"]
