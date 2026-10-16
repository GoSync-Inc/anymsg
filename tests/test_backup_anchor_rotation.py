"""Synthetic receiver maintenance checks; no decryption keys or provider data."""
from datetime import datetime, timezone, timedelta
import hashlib
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from messaging_service import backup_receiver as receiver

OLD = '20261013T091703Z-0123456789abcdef'
NEW = '20261016T091703Z-fedcba9876543210'
PENDING = '20261016T101703Z-1123456789abcdef'
SNAPSHOT = 'a' * 64


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, separators=(',', ':')) + '\n')
    path.chmod(0o600)


@pytest.fixture
def rotation(tmp_path, monkeypatch):
    # macOS test runners are not root. Mock only OS identity, retaining real
    # files, modes, descriptors, hashes, locks and atomic filesystem operations.
    original_fstat = os.fstat
    original_stat = Path.stat
    # The real system-temp ancestor chain is writable/user-owned. Explicitly
    # virtualize only these known fixture ancestor inodes as trusted; all newly
    # created ancestors retain their actual permissions for negative cases.
    fixture_ancestors = {(original_stat(p).st_dev, original_stat(p).st_ino)
                         for p in (tmp_path, *tmp_path.parents)}
    def root_info(info):
        values = list(info)
        values[4] = 0
        if (info.st_dev, info.st_ino) in fixture_ancestors:
            values[0] &= ~0o022
        return os.stat_result(values)
    monkeypatch.setattr(os, 'fstat', lambda fd: root_info(original_fstat(fd)))
    monkeypatch.setattr(Path, 'stat', lambda path, *a, **kw: root_info(original_stat(path, *a, **kw)))
    monkeypatch.setattr(os, 'geteuid', lambda: 0)
    archive = tmp_path / 'archive'
    control = tmp_path / 'control'
    archive.mkdir(mode=0o700)
    control.mkdir(mode=0o700)
    old_bytes = receiver.AGE_HEADER + b'old verified ciphertext' * 5
    new_bytes = receiver.AGE_HEADER + b'new verified ciphertext' * 5
    old = {'id': OLD, 'bytes': len(old_bytes), 'sha256': digest(old_bytes)}
    new = {'id': NEW, 'bytes': len(new_bytes), 'sha256': digest(new_bytes)}
    (archive / (OLD + '.tar.age')).write_bytes(old_bytes)
    (archive / (NEW + '.tar.age')).write_bytes(new_bytes)
    receipt = {'v': 2, 'status': 'stored', **new, 'snapshot_sha256': SNAPSHOT,
               'duplicate': False, 'received_at': (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()}
    write_json(archive / (NEW + '.receipt.json'), receipt)
    marker = control / 'protected.json'
    write_json(marker, [old])
    expected = digest(marker.read_bytes())
    proof = control / 'independent-restore.json'
    report = {'v': 1, 'type': 'independent_restore_verification', 'status': 'verified',
              'backup': {**new, 'snapshot_sha256': SNAPSHOT},
              'verification': {'id': '1' * 32, 'verified_at': datetime.now(timezone.utc).isoformat(),
                               'schema_version': 32, 'manifest_sha256': SNAPSHOT,
                               'checks': {name: True for name in (
                                   'decryption', 'bundle_integrity', 'database_restore',
                                   'table_inventory', 'original_hashes', 'recovery_objects',
                                   'collectors_quarantined')}}}
    write_json(proof, report)
    return archive, marker, proof, expected, old, new, report


def rotate(rotation):
    archive, marker, proof, expected, *_ = rotation
    return receiver.rotate_anchor(archive, marker, proof, expected_protected_sha256=expected)


def test_rotation_promotes_verified_candidate_before_removing_previous_anchor(rotation):
    archive, marker, _, _, _, new, _ = rotation
    # An unrelated pending verification candidate is never a deletion target.
    pending = archive / (PENDING + '.tar.age')
    pending.write_bytes(receiver.AGE_HEADER + b'not yet independently restored')
    result = rotate(rotation)
    assert result['status'] == 'rotated'
    assert result['removed'] == 1 and result['protected'] == 1
    assert json.loads(marker.read_text()) == [new]
    assert not (archive / (OLD + '.tar.age')).exists()
    assert (archive / (NEW + '.tar.age')).is_file() and pending.is_file()


@pytest.mark.parametrize('attack', [
    'upload_receipt_only', 'failed_restore', 'missing_check', 'false_check',
    'wrong_manifest', 'wrong_snapshot', 'wrong_candidate', 'bool_size',
    'invalid_schema', 'future_verification', 'before_delivery',
])
def test_rotation_rejects_nonmatching_or_incomplete_restore_proof(rotation, attack):
    archive, marker, proof, _, _, _, report = rotation
    before = marker.read_bytes()
    if attack == 'upload_receipt_only':
        report = json.loads((archive / (NEW + '.receipt.json')).read_text())
    elif attack == 'failed_restore': report['status'] = 'failed'
    elif attack == 'missing_check': del report['verification']['checks']['database_restore']
    elif attack == 'false_check': report['verification']['checks']['original_hashes'] = False
    elif attack == 'wrong_manifest': report['verification']['manifest_sha256'] = 'b' * 64
    elif attack == 'wrong_snapshot': report['backup']['snapshot_sha256'] = 'b' * 64
    elif attack == 'wrong_candidate': report['backup']['id'] = PENDING
    elif attack == 'bool_size': report['backup']['bytes'] = True
    elif attack == 'invalid_schema': report['verification']['schema_version'] = True
    elif attack == 'future_verification': report['verification']['verified_at'] = '2999-01-01T00:00:00+00:00'
    elif attack == 'before_delivery': report['verification']['verified_at'] = '2026-10-12T00:00:00+00:00'
    write_json(proof, report)
    with pytest.raises((ValueError, FileNotFoundError)):
        rotate(rotation)
    assert marker.read_bytes() == before and (archive / (OLD + '.tar.age')).is_file()
    assert (archive / (NEW + '.tar.age')).is_file()


@pytest.mark.parametrize('attack', ['old_corrupt', 'candidate_corrupt', 'receipt_missing', 'receipt_snapshot', 'receipt_size', 'stale_marker'])
def test_rotation_preserves_every_copy_on_stale_or_corrupt_inputs(rotation, attack):
    archive, marker, _, _, _, _, _ = rotation
    before = marker.read_bytes()
    if attack == 'old_corrupt': (archive / (OLD + '.tar.age')).write_bytes(b'corrupted old copy')
    elif attack == 'candidate_corrupt': (archive / (NEW + '.tar.age')).write_bytes(b'corrupted new copy')
    elif attack == 'receipt_missing': (archive / (NEW + '.receipt.json')).unlink()
    elif attack in ('receipt_snapshot', 'receipt_size'):
        path = archive / (NEW + '.receipt.json')
        receipt = json.loads(path.read_text())
        receipt['snapshot_sha256' if attack == 'receipt_snapshot' else 'bytes'] = 'b' * 64 if attack == 'receipt_snapshot' else 123
        write_json(path, receipt)
    else:
        rotation = (*rotation[:3], '0' * 64, *rotation[4:])
    with pytest.raises((ValueError, FileNotFoundError)):
        rotate(rotation)
    assert marker.read_bytes() == before
    assert (archive / (OLD + '.tar.age')).exists() and (archive / (NEW + '.tar.age')).exists()


@pytest.mark.parametrize('attack', ['nonroot', 'public_proof', 'symlink_proof', 'hardlink_proof', 'oversized_proof', 'writable_control', 'proof_in_archive', 'unowned_proof'])
def test_rotation_requires_separate_private_root_operator_authority(rotation, monkeypatch, attack):
    archive, marker, proof, _, _, _, _ = rotation
    before = marker.read_bytes()
    if attack == 'nonroot': monkeypatch.setattr(os, 'geteuid', lambda: 1000)
    elif attack == 'public_proof': proof.chmod(0o644)
    elif attack == 'symlink_proof':
        real = proof.with_name('real-proof.json');proof.rename(real);proof.symlink_to(real)
    elif attack == 'hardlink_proof': os.link(proof, proof.with_name('hardlink.json'))
    elif attack == 'oversized_proof': proof.write_bytes(b' ' * 65537)
    elif attack == 'writable_control': marker.parent.chmod(0o777)
    elif attack == 'proof_in_archive':
        copied = archive / 'proof.json';copied.write_bytes(proof.read_bytes());copied.chmod(0o600)
        rotation = (archive, marker, copied, *rotation[3:])
    else:
        original = os.fstat
        def unowned(fd):
            values = list(original(fd));values[4] = 123;return os.stat_result(values)
        monkeypatch.setattr(os, 'fstat', unowned)
    with pytest.raises((ValueError, OSError)):
        rotate(rotation)
    assert marker.read_bytes() == before and (archive / (OLD + '.tar.age')).exists()


def test_rotation_uses_receiver_lock_and_never_deletes_busy_uploads(rotation):
    archive, marker, *_ = rotation
    before = marker.read_bytes()
    with receiver.locked(archive):
        with pytest.raises(RuntimeError, match='receiver_busy'):
            rotate(rotation)
    assert marker.read_bytes() == before and (archive / (OLD + '.tar.age')).exists()


def test_rotation_same_proof_retry_is_idempotent(rotation):
    archive, marker, *_ = rotation
    first = rotate(rotation)
    marker_bytes = marker.read_bytes()
    second = rotate(rotation)
    assert second['status'] == 'rotated' and second['duplicate'] is True
    assert first['duplicate'] is False and second['removed'] == 0
    assert marker.read_bytes() == marker_bytes and (archive / (NEW + '.tar.age')).exists()


def test_rotation_retry_after_marker_commit_finishes_exact_previous_cleanup(rotation, monkeypatch):
    archive, marker, _, _, _, new, _ = rotation
    original = os.unlink
    failed = []
    def interrupted(path, *a, **kw):
        if Path(path).name == OLD + '.tar.age' and kw.get('dir_fd') is not None and not failed:
            failed.append(True);raise OSError('interrupted old object cleanup')
        return original(path, *a, **kw)
    monkeypatch.setattr(os, 'unlink', interrupted)
    with pytest.raises(OSError, match='interrupted'):
        rotate(rotation)
    assert json.loads(marker.read_text()) == [new]
    assert (archive / (OLD + '.tar.age')).exists() and (archive / (NEW + '.tar.age')).exists()
    assert rotate(rotation)['removed'] == 1
    assert not (archive / (OLD + '.tar.age')).exists()


def test_rotation_failure_before_marker_commit_preserves_current_anchor(rotation, monkeypatch):
    archive, marker, *_ = rotation
    before = marker.read_bytes()
    original = os.replace
    failed = []
    def interrupted(source, target, *a, **kw):
        if Path(target).name == marker.name and not failed:
            failed.append(True);raise OSError('interrupted marker replacement')
        return original(source, target, *a, **kw)
    monkeypatch.setattr(os, 'replace', interrupted)
    with pytest.raises(OSError, match='interrupted'):
        rotate(rotation)
    assert marker.read_bytes() == before
    assert (archive / (OLD + '.tar.age')).exists() and (archive / (NEW + '.tar.age')).exists()
    assert rotate(rotation)['removed'] == 1


def test_rotation_fsync_failure_never_deletes_current_good_copy(rotation, monkeypatch):
    archive, marker, _, _, _, new, _ = rotation
    original = receiver.fsync_dir
    failed = []
    def interrupted(directory):
        same_parent = (os.fstat(directory).st_ino == marker.parent.stat().st_ino if isinstance(directory, int)
                       else Path(directory) == marker.parent)
        if same_parent and json.loads(marker.read_text()) == [new] and not failed:
            failed.append(True);raise OSError('interrupted marker durability')
        return original(directory)
    monkeypatch.setattr(receiver, 'fsync_dir', interrupted)
    with pytest.raises(OSError, match='interrupted'):
        rotate(rotation)
    assert (archive / (OLD + '.tar.age')).exists() and (archive / (NEW + '.tar.age')).exists()
    assert rotate(rotation)['removed'] == 1


def test_rotation_does_not_accept_changed_proof_as_same_completed_operation(rotation):
    archive, marker, proof, _, _, _, report = rotation
    rotate(rotation)
    before = marker.read_bytes()
    report['verification']['id'] = '2' * 32
    write_json(proof, report)
    with pytest.raises(ValueError, match='protected_marker_changed'):
        rotate(rotation)
    assert marker.read_bytes() == before and (archive / (NEW + '.tar.age')).exists()


def test_rotation_cli_is_local_and_upload_command_cannot_select_it(rotation, monkeypatch, capsys):
    archive, marker, proof, expected, *_ = rotation
    monkeypatch.setenv('SSH_ORIGINAL_COMMAND', '--rotate-anchor --restore-proof /sender/chosen')
    monkeypatch.setattr('sys.argv', ['backup_receiver.py', '--directory', str(archive),
                                   '--rotate-anchor', '--protected-file', str(marker),
                                   '--restore-proof', str(proof), '--expected-protected-sha256', expected])
    receiver.main()
    assert json.loads(capsys.readouterr().out)['status'] == 'rotated'
    assert not (archive / (OLD + '.tar.age')).exists()


def test_rotation_rejects_duplicate_proof_keys_without_touching_anchors(rotation):
    archive, marker, proof, *_ = rotation
    before = marker.read_bytes()
    raw = proof.read_text().replace('"status":"verified"', '"status":"failed","status":"verified"')
    proof.write_text(raw)
    with pytest.raises(ValueError, match='duplicate_evidence_field'):
        rotate(rotation)
    assert marker.read_bytes() == before and (archive / (OLD + '.tar.age')).exists()


def test_rotation_cannot_start_another_operation_until_interrupted_cleanup_finishes(rotation, monkeypatch):
    archive, marker, proof, _, _, new, report = rotation
    original = os.unlink
    def interrupted(path, *a, **kw):
        if Path(path).name == OLD + '.tar.age' and kw.get('dir_fd') is not None:raise OSError('cleanup interrupted')
        return original(path, *a, **kw)
    monkeypatch.setattr(os, 'unlink', interrupted)
    with pytest.raises(OSError, match='cleanup interrupted'):rotate(rotation)
    # A second fully verified candidate does not authorize forgetting the first
    # rotation's unfinished prior-copy cleanup journal.
    third = archive / (PENDING + '.tar.age')
    third.write_bytes(receiver.AGE_HEADER + b'next ciphertext' * 10)
    candidate = {'id': PENDING, 'bytes': third.stat().st_size, 'sha256': digest(third.read_bytes())}
    receipt = json.loads((archive / (NEW + '.receipt.json')).read_text())
    receipt.update(candidate)
    write_json(archive / (PENDING + '.receipt.json'), receipt)
    report['backup'] = {**candidate, 'snapshot_sha256': SNAPSHOT}
    report['verification']['id'] = '2' * 32
    write_json(proof, report)
    with pytest.raises(ValueError, match='anchor_rotation_incomplete'):
        receiver.rotate_anchor(archive, marker, proof, expected_protected_sha256=digest(marker.read_bytes()))
    assert json.loads(marker.read_text()) == [new]
    assert (archive / (OLD + '.tar.age')).exists() and third.exists()


def test_maintenance_journal_writer_rejects_unbounded_records_before_replacement(rotation):
    _, marker, *_ = rotation
    before = marker.read_bytes()
    with pytest.raises(ValueError, match='operator_evidence_too_large'):
        receiver.write_maintenance_json(marker, {'unbounded': 'x' * 65536})
    assert marker.read_bytes() == before


@pytest.mark.parametrize('field', ['proof', 'marker', 'directory'])
def test_rotation_rejects_parent_traversal_before_evidence_separation(rotation, field):
    archive, marker, proof, expected, *_ = rotation
    if field == 'proof':
        copied = archive / 'restore.json'
        copied.write_bytes(proof.read_bytes());copied.chmod(0o600)
        proof = marker.parent / '..' / 'archive' / copied.name
    elif field == 'marker':
        copied = archive / 'protected.json'
        copied.write_bytes(marker.read_bytes());copied.chmod(0o600)
        marker = marker.parent / '..' / 'archive' / copied.name
    else:
        archive = marker.parent / '..' / 'archive'
    before = rotation[1].read_bytes()
    with pytest.raises(ValueError, match='invalid_operator_path'):
        receiver.rotate_anchor(archive, marker, proof, expected_protected_sha256=expected)
    assert rotation[1].read_bytes() == before
    assert (rotation[0] / (OLD + '.tar.age')).exists()


def test_upload_forced_command_ignores_sender_requested_anchor_rotation(rotation, monkeypatch, capsys):
    archive, marker, proof, *_ = rotation
    before = marker.read_bytes()
    data = receiver.AGE_HEADER + b'synthetic upload ciphertext' * 5
    header = {'v': 1, 'id': PENDING, 'bytes': len(data), 'sha256': digest(data)}
    monkeypatch.setattr('sys.argv', ['backup_receiver.py', '--directory', str(archive)])
    monkeypatch.setattr('sys.stdin', SimpleNamespace(buffer=io.BytesIO(json.dumps(header).encode() + b'\n' + data)))
    monkeypatch.setenv('SSH_ORIGINAL_COMMAND', '--rotate-anchor --protected-file ' + str(marker) + ' --restore-proof ' + str(proof))
    receiver.main()
    assert json.loads(capsys.readouterr().out)['status'] == 'stored'
    assert marker.read_bytes() == before and (archive / (OLD + '.tar.age')).exists()


@pytest.mark.parametrize('target', ['control', 'archive', 'proof'])
def test_rotation_rejects_writable_ancestors_even_when_immediate_parent_is_private(rotation, target):
    archive, marker, proof, expected, *_ = rotation
    unsafe = archive.parent / 'untrusted-parent'
    unsafe.mkdir(mode=0o777);unsafe.chmod(0o777)
    if target == 'control':
        moved = unsafe / 'control'
        marker.parent.rename(moved)
        marker = moved / marker.name;proof = moved / proof.name
    elif target == 'archive':
        moved = unsafe / 'archive';archive.rename(moved);archive = moved
    else:
        moved = unsafe / 'proofs';moved.mkdir(mode=0o700)
        copied = moved / proof.name;copied.write_bytes(proof.read_bytes());copied.chmod(0o600);proof = copied
    before = marker.read_bytes()
    with pytest.raises(ValueError, match='untrusted_operator_ancestor'):
        receiver.rotate_anchor(archive, marker, proof, expected_protected_sha256=expected)
    assert marker.read_bytes() == before and (archive / (OLD + '.tar.age')).exists()


def test_rotation_marker_commit_is_anchored_when_root_path_is_interposed(rotation, monkeypatch):
    archive, marker, proof, expected, old, new, _ = rotation
    real_control = marker.parent
    parked = real_control.with_name('parked-control')
    original = receiver.write_maintenance_json
    interposed = []
    def write(path, value, *a, **kw):
        if Path(path).name == 'protected.json' and not interposed:
            interposed.append(True)
            real_control.rename(parked)
            real_control.mkdir(mode=0o700)
            write_json(real_control / marker.name, [old])
        return original(path, value, *a, **kw)
    monkeypatch.setattr(receiver, 'write_maintenance_json', write)
    with pytest.raises(ValueError, match='operator_directory_changed'):
        receiver.rotate_anchor(archive, marker, proof, expected_protected_sha256=expected)
    assert interposed
    assert json.loads((parked / marker.name).read_text()) == [new]
    assert json.loads(marker.read_text()) == [old]
    assert (archive / (OLD + '.tar.age')).exists()
