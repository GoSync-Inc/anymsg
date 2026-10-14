import json
import sys

import pytest

from messaging_service.backup import create_snapshot,verify_snapshot,quarantine_restore


def test_snapshot_manifest_missing_blob_and_dump_failure(seeded,tmp_path):
    s,a,b=seeded
    with pytest.raises(RuntimeError,match='pg_dump_failed'):
        create_snapshot(s,tmp_path/'failed',[sys.executable,'-c','raise SystemExit(2)'])
    assert not (tmp_path/'failed').exists()
    assert list(tmp_path.glob('.backup-*'))==[]
    # Unit stub only; real pg_dump/pg_restore exercised by the separate deployment restore drill.
    result=create_snapshot(s,tmp_path/'snapshot',[sys.executable,'-c','print("unit-dump-stub")'])
    assert result['blobs']==1
    assert verify_snapshot(s,tmp_path/'snapshot')['status']=='verified'
    manifest=json.loads((tmp_path/'snapshot/manifest.json').read_text())
    (tmp_path/'snapshot/files'/manifest['blobs'][0]['digest']).write_bytes(b'bad')
    with pytest.raises(RuntimeError,match='blob_corrupt'): verify_snapshot(s,tmp_path/'snapshot')


def test_snapshot_rejects_changed_database_and_source_quarantine(seeded,tmp_path):
    s,a,b=seeded
    create_snapshot(s,tmp_path/'snapshot',[sys.executable,'-c','print("unit-dump-stub")'])
    s.get_updates(a,'changed-after-snapshot')
    with pytest.raises(RuntimeError,match='database_mismatch'): verify_snapshot(s,tmp_path/'snapshot')
    with pytest.raises(ValueError,match='not_restore_database'): quarantine_restore(s)
    with pytest.raises(ValueError,match='destination_exists'):
        create_snapshot(s,tmp_path/'snapshot',[sys.executable])
