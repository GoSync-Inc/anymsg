"""Staging-space regression tests; transport stubs make no crypto assertion."""
import hashlib
import os
import sys

import pytest

from messaging_service.backup import create_snapshot, verify_bundle
from messaging_service.backup_crypto import decrypt_snapshot, encrypt_snapshot

DUMP = [sys.executable, '-c', 'print("UNIT_DUMP_NOT_A_DATABASE")']


@pytest.fixture
def age_stub(tmp_path):
    path = tmp_path / 'age-stub'
    path.write_text('#!' + sys.executable + '\nimport sys,shutil\n'
        'source=open(sys.argv[-1],"rb") if "-d" in sys.argv else sys.stdin.buffer\n'
        'shutil.copyfileobj(source,sys.stdout.buffer)\n')
    path.chmod(0o700)
    return str(path)


@pytest.mark.parametrize('consume', [False, True])
def test_encryption_roundtrip_can_consume_only_owned_staged_blobs(seeded, tmp_path, age_stub, consume):
    store, *_ = seeded
    snapshot = tmp_path / 'snapshot'
    create_snapshot(store, snapshot, DUMP)
    manifest = verify_bundle(snapshot)
    originals = {row['digest']: (store.files / row['digest']).read_bytes() for row in manifest['blobs']}
    assert originals
    kwargs = {'consume_staged_blobs': True} if consume else {}
    encrypt_snapshot(snapshot, tmp_path / 'bundle', 'fake', age=age_stub, **kwargs)
    assert {p.name for p in (snapshot / 'files').iterdir()} == (set() if consume else set(originals))
    assert (snapshot / 'manifest.json').exists() and (snapshot / 'database.dump').exists()
    assert all((store.files / name).read_bytes() == data for name, data in originals.items())
    decrypt_snapshot(tmp_path / 'bundle', 'fake', tmp_path / 'restored', age=age_stub)
    assert verify_bundle(tmp_path / 'restored') == manifest
    assert all((tmp_path / 'restored/files' / name).read_bytes() == data for name, data in originals.items())


@pytest.mark.parametrize('attack', ['corrupt', 'hardlink', 'invalid_opt_in'])
def test_consumption_rejects_invalid_snapshot_before_removing_any_file(seeded, tmp_path, age_stub, attack):
    store, *_ = seeded
    snapshot = tmp_path / 'snapshot'
    create_snapshot(store, snapshot, DUMP)
    blob = next((snapshot / 'files').iterdir())
    option = True
    if attack == 'corrupt':
        blob.write_bytes(b'corrupt')
    elif attack == 'hardlink':
        os.link(blob, tmp_path / 'shared-copy')
    else:
        option = 'true'
    before = {p.name: p.read_bytes() for p in (snapshot / 'files').iterdir()}
    with pytest.raises((ValueError, RuntimeError)):
        encrypt_snapshot(snapshot, tmp_path / 'bundle', 'fake', age=age_stub, consume_staged_blobs=option)
    assert {p.name: p.read_bytes() for p in (snapshot / 'files').iterdir()} == before
    assert not (tmp_path / 'bundle').exists() and not list(tmp_path.glob('.encrypt-*'))


def test_failed_encoder_never_publishes_or_changes_originals_or_old_backup(seeded, tmp_path):
    store, *_ = seeded
    snapshot = tmp_path / 'snapshot'
    create_snapshot(store, snapshot, DUMP)
    manifest = verify_bundle(snapshot)
    original_hashes = {row['digest']: hashlib.sha256((store.files / row['digest']).read_bytes()).hexdigest()
                       for row in manifest['blobs']}
    old = tmp_path / 'old-bundle'
    old.write_bytes(b'PREVIOUS_VERIFIED_BACKUP_FIXTURE')
    encoder = tmp_path / 'age-fails'
    encoder.write_text('#!' + sys.executable + '\nimport sys\nsys.stdin.buffer.read()\nsys.exit(1)\n')
    encoder.chmod(0o700)
    with pytest.raises(RuntimeError, match='backup_encryption_failed'):
        encrypt_snapshot(snapshot, tmp_path / 'bundle', 'fake', age=str(encoder), consume_staged_blobs=True)
    assert not (tmp_path / 'bundle').exists() and not list(tmp_path.glob('.encrypt-*'))
    assert old.read_bytes() == b'PREVIOUS_VERIFIED_BACKUP_FIXTURE'
    assert all(hashlib.sha256((store.files / name).read_bytes()).hexdigest() == digest
               for name, digest in original_hashes.items())
    # The runner discards this job's consumed staging on failure and captures anew.
    assert (snapshot / 'manifest.json').exists()
