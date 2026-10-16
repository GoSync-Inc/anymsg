"""Synthetic streaming restore boundaries; never connects a provider or live DB."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
from types import SimpleNamespace

import pytest

from messaging_service import backup_crypto as crypto


def bundle():
    dump=b'UNIT_DUMP_NOT_A_DATABASE'
    manifest={'format':1,'schema_version':2,'dump_sha256':hashlib.sha256(dump).hexdigest(),'tables':{},'blobs':[]}
    out=io.BytesIO()
    with tarfile.open(fileobj=out,mode='w') as archive:
        for name,data in [('manifest.json',json.dumps(manifest).encode()),('database.dump',dump)]:
            item=tarfile.TarInfo(name);item.size=len(data);archive.addfile(item,io.BytesIO(data))
    return out.getvalue(),manifest


@pytest.fixture
def decoder(tmp_path):
    path=tmp_path/'age-synthetic'
    path.write_text('#!'+sys.executable+'\nimport sys,shutil\nshutil.copyfileobj(sys.stdin.buffer,sys.stdout.buffer)\n')
    path.chmod(0o700)
    return str(path)


def test_stream_decryption_extracts_verified_bundle_without_ciphertext_file(tmp_path,decoder):
    data,manifest=bundle()
    result=crypto.decrypt_snapshot_stream(io.BytesIO(data),'synthetic-key',tmp_path/'restore',age=decoder,max_bytes=len(data))
    assert json.loads((result/'manifest.json').read_text())==manifest
    assert sorted(p.name for p in tmp_path.iterdir() if p.name!='processing-locks')==['age-synthetic','restore']


def test_stream_decryption_rejects_late_decoder_auth_failure(tmp_path):
    data,_=bundle();decoder=tmp_path/'age-fails'
    decoder.write_text('#!'+sys.executable+'\nimport sys,shutil\nshutil.copyfileobj(sys.stdin.buffer,sys.stdout.buffer)\nsys.exit(1)\n');decoder.chmod(0o700)
    with pytest.raises(RuntimeError,match='backup_decryption_failed'):
        crypto.decrypt_snapshot_stream(io.BytesIO(data),'key',tmp_path/'restore',age=str(decoder))
    assert not (tmp_path/'restore').exists() and not list(tmp_path.glob('.decrypt-*'))


def test_stream_decryption_propagates_source_failure_before_publication(tmp_path,decoder):
    data,_=bundle()
    class Broken(io.BytesIO):
        def read(self,size=-1):
            chunk=super().read(size)
            if not chunk:raise OSError('synthetic upstream disconnect')
            return chunk
    with pytest.raises(OSError):
        crypto.decrypt_snapshot_stream(Broken(data),'key',tmp_path/'restore',age=decoder)
    assert not (tmp_path/'restore').exists()


def test_worker_has_no_provider_or_store_initialization():
    from messaging_service import backup_restore_worker as worker
    assert callable(worker.run)
    assert worker.DATABASE.endswith('_restore_test')


def test_quarantine_assertion_uses_actual_text_array_policy_type():
    from messaging_service import backup_restore_worker as worker
    queries=[]
    class Connection:
        def execute(self,query):
            queries.append(query)
            if "operations_enabled" in query:
                assert "'{}'::text[]" in query
            return SimpleNamespace(fetchone=lambda:{'name':worker.DATABASE} if 'current_database' in query else None)
    class Lease:
        def __enter__(self):return Connection()
        def __exit__(self,*args):pass
    worker.assert_quarantined(SimpleNamespace(pool=SimpleNamespace(connection=Lease)),34)
    assert any('subscription_receipts' in q for q in queries)
    assert any('attachment_backup_fences' in q for q in queries)


@pytest.mark.parametrize('unsafe_table',['account_policies','api_keys','collector_leases','source_status','outgoing_messages',
 'messenger_commands','subscriptions','file_uploads','subscription_receivers','subscription_outbox','subscription_receipts','attachment_backup_fences'])
def test_each_unsafe_restored_authority_prevents_quarantine_acceptance(unsafe_table):
    from messaging_service import backup_restore_worker as worker
    class Connection:
        def execute(self,query):
            row={'name':worker.DATABASE} if 'current_database' in query else ({'unsafe':True} if 'FROM '+unsafe_table+' WHERE' in query else None)
            return SimpleNamespace(fetchone=lambda:row)
    class Lease:
        def __enter__(self):return Connection()
        def __exit__(self,*args):pass
    with pytest.raises(RuntimeError,match='restore_quarantine_failed'):
        worker.assert_quarantined(SimpleNamespace(pool=SimpleNamespace(connection=Lease)),34)


@pytest.mark.parametrize('code',['restore_decryption_failed','restore_database_failed','restore_snapshot_failed','restore_quarantine_failed'])
def test_worker_stage_failure_exposes_only_fixed_safe_code(code):
    from messaging_service import backup_restore_worker as worker
    def secret_failure():raise OSError('SYNTHETIC_PASSWORD_DO_NOT_PERSIST')
    with pytest.raises(worker.RestoreStageError) as caught:worker.stage(code,secret_failure)
    assert str(caught.value)==code
