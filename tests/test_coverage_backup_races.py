"""Post-verification member swaps; guards fail before any blocking FIFO open.

The local passthrough encoder proves source validation, never encryption.
No database, Docker or provider session is opened by these tests.
"""
import io
import os
from pathlib import Path
import stat

import pytest

from messaging_service import backup
from messaging_service.backup_crypto import encrypt_snapshot, encrypt_snapshot_stream
from test_backup_streaming import reference_fixture


@pytest.mark.parametrize('member', ['manifest.json', 'database.dump', 'original'])
def test_stream_rejects_post_verification_fifo_swap_before_any_blocking_open(tmp_path, monkeypatch, member):
    capture, originals, manifest, encoder = reference_fixture(tmp_path)
    target = (originals / manifest['blobs'][0]['digest'] if member == 'original'
              else capture / member)
    verify = backup.verify_bundle
    opened = os.open
    path_open = Path.open
    swapped = []

    def verified_then_replaced(*args, **kwargs):
        value = verify(*args, **kwargs)
        target.unlink()
        os.mkfifo(target, 0o600)
        swapped.append(True)
        return value

    def safe_open(path, flags, *args, **kwargs):
        if swapped and Path(path) == target:
            assert flags & os.O_NONBLOCK, 'post-verification member attempted a blocking FIFO open'
            assert flags & os.O_NOFOLLOW, 'post-verification member lost its no-follow boundary'
        return opened(path, flags, *args, **kwargs)

    def safe_path_open(path, *args, **kwargs):
        if swapped and path == target:
            pytest.fail('post-verification member attempted a blocking Path.open on FIFO')
        return path_open(path, *args, **kwargs)

    monkeypatch.setattr(backup, 'verify_bundle', verified_then_replaced)
    monkeypatch.setattr(os, 'open', safe_open)
    monkeypatch.setattr(Path, 'open', safe_path_open)
    output = io.BytesIO()
    with pytest.raises(ValueError, match='invalid_backup_(file|manifest)'):
        encrypt_snapshot_stream(capture, output, 'synthetic-recipient',
                                blob_root=originals, age=encoder)
    assert swapped == [True]
    assert stat.S_ISFIFO(target.lstat().st_mode)


@pytest.mark.parametrize('member', ['manifest.json', 'database.dump', 'original'])
def test_staged_rejects_post_verification_fifo_swap_without_publishing_output(tmp_path, monkeypatch, member):
    capture, originals, manifest, encoder = reference_fixture(tmp_path)
    digest = manifest['blobs'][0]['digest']
    original_bytes = (originals / digest).read_bytes()
    staged_original = capture / 'files' / digest
    staged_original.write_bytes(original_bytes)
    target = staged_original if member == 'original' else capture / member
    verify = backup.verify_bundle
    opened = os.open
    path_open = Path.open
    swapped = []

    def verified_then_replaced(*args, **kwargs):
        value = verify(*args, **kwargs)
        target.unlink()
        os.mkfifo(target, 0o600)
        swapped.append(True)
        return value

    def safe_open(path, flags, *args, **kwargs):
        if swapped and Path(path) == target:
            assert flags & os.O_NONBLOCK, 'staged member attempted a blocking FIFO open'
            assert flags & os.O_NOFOLLOW, 'staged member lost its no-follow boundary'
        return opened(path, flags, *args, **kwargs)

    def safe_path_open(path, *args, **kwargs):
        if swapped and path == target:
            pytest.fail('staged member attempted a blocking Path.open on FIFO')
        return path_open(path, *args, **kwargs)

    monkeypatch.setattr(backup, 'verify_bundle', verified_then_replaced)
    monkeypatch.setattr(os, 'open', safe_open)
    monkeypatch.setattr(Path, 'open', safe_path_open)
    output = tmp_path / 'snapshot.tar.age'
    with pytest.raises(ValueError, match='invalid_backup_file'):
        encrypt_snapshot(capture, output, 'synthetic-recipient', age=encoder)
    assert swapped == [True]
    assert stat.S_ISFIFO(target.lstat().st_mode)
    assert not output.exists()
    assert not list(tmp_path.glob('.encrypt-*'))
    assert (originals / digest).read_bytes() == original_bytes
