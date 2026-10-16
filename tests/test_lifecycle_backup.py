"""Synthetic recovery inventory and durable original-fence contracts."""
import hashlib
import json
import sys
import uuid

import pytest

from messaging_service.backup import create_snapshot, verify_bundle, verify_snapshot


@pytest.fixture
def backup_seeded(seeded):
    store,*_=seeded
    with store.pool.connection() as c:
        c.execute("UPDATE attachment_residency SET state='resident',eviction_id=NULL,evicted_at=NULL")
        c.execute('DELETE FROM attachment_eviction_journal')
        c.execute('DELETE FROM attachment_backup_fences')
    return seeded


def absent_bundle(tmp_path, *, schema=34, **changes):
    directory=tmp_path/'bundle';directory.mkdir();(directory/'files').mkdir()
    dump=b'synthetic database dump';(directory/'database.dump').write_bytes(dump)
    manifest={'format':1,'schema_version':schema,'tables':{},'blobs':[],
        'dump_sha256':hashlib.sha256(dump).hexdigest(),
        'includes_provider_sessions':False,
        'absent_blobs':[{'digest':'a'*64,'bytes':17,'eviction_id':str(uuid.uuid4())}]}
    manifest.update(changes)
    (directory/'manifest.json').write_text(json.dumps(manifest))
    return directory,manifest


def test_managed_absent_inventory_is_a_restorable_explicit_schema34_bundle(tmp_path):
    directory,manifest=absent_bundle(tmp_path)
    assert verify_bundle(directory)==manifest


@pytest.mark.parametrize('changes',[
    {'schema_version':32},
    {'absent_blobs':[{'digest':'a'*64,'bytes':17}]},
    {'absent_blobs':[{'digest':'a'*64,'bytes':17,'eviction_id':'not-an-eviction'}]},
    {'absent_blobs':[{'digest':'a'*64,'bytes':-1,'eviction_id':str(uuid.uuid4())}]},
    {'absent_blobs':[{'digest':'a'*64,'bytes':17,'eviction_id':str(uuid.uuid4())}]*2},
    {'blobs':[{'digest':'a'*64,'bytes':17}]},
])
def test_absent_inventory_never_silently_omits_invalid_or_ambiguous_originals(tmp_path,changes):
    directory,_=absent_bundle(tmp_path,**changes)
    with pytest.raises((ValueError,RuntimeError)):
        verify_bundle(directory)


def test_reference_capture_retains_durable_fence_and_schema_inventory(backup_seeded,tmp_path):
    store,*_=backup_seeded
    create_snapshot(store,tmp_path/'capture',[sys.executable,'-c','print("synthetic dump")'],
                    reference_blobs=True,backup_fence='synthetic-reference-backup')
    manifest=verify_bundle(tmp_path/'capture',blob_root=store.files)
    assert manifest['schema_version']==34
    assert manifest['backup_fence']=='synthetic-reference-backup'
    assert {'attachment_residency','attachment_eviction_journal','attachment_backup_fences',
            'attachment_understanding','file_understanding_profiles'}<=set(manifest['tables'])
    with store.pool.connection() as c:
        assert c.execute('SELECT ended_at FROM attachment_backup_fences WHERE id=%s',
                         ('synthetic-reference-backup',)).fetchone()['ended_at'] is None


def mark_evicted(store):
    with store.pool.connection() as c:
        row=c.execute('SELECT digest,bytes FROM attachments ORDER BY digest LIMIT 1').fetchone()
        eviction=str(uuid.uuid4())
        c.execute("INSERT INTO attachment_eviction_journal(id,digest,bytes,root_device,root_inode,state,completed_at) VALUES(%s,%s,%s,%s,%s,'completed',clock_timestamp())",
                  (eviction,row['digest'],row['bytes'],store.files.stat().st_dev,store.files.stat().st_ino))
        c.execute("UPDATE attachment_residency SET state='evicted',eviction_id=%s,evicted_at=clock_timestamp() WHERE digest=%s",
                  (eviction,row['digest']))
    (store.files/row['digest']).unlink()
    return {**row,'eviction_id':eviction}


def test_snapshot_preserves_managed_absence_with_exact_restored_catalog(backup_seeded,tmp_path):
    store,*_=backup_seeded;expected=mark_evicted(store)
    create_snapshot(store,tmp_path/'capture',[sys.executable,'-c','print("synthetic dump")'])
    manifest=verify_bundle(tmp_path/'capture')
    assert manifest['blobs']==[] and manifest['absent_blobs']==[expected]
    assert verify_snapshot(store,tmp_path/'capture')['status']=='verified'
    manifest['absent_blobs']=[]
    (tmp_path/'capture/manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError,match='restored_blob_inventory_mismatch'):
        verify_snapshot(store,tmp_path/'capture')


def test_unexpected_missing_original_abandons_only_owned_capture_fence(backup_seeded,tmp_path):
    store,*_=backup_seeded
    with store.pool.connection() as c:
        row=c.execute('SELECT digest FROM attachments LIMIT 1').fetchone()
    (store.files/row['digest']).unlink()
    with pytest.raises(RuntimeError,match='blob_corrupt'):
        create_snapshot(store,tmp_path/'capture',[sys.executable,'-c','print("synthetic dump")'],
                        backup_fence='synthetic-failed-backup')
    with store.pool.connection() as c:
        assert c.execute('SELECT ended_at IS NOT NULL AS closed FROM attachment_backup_fences WHERE id=%s',
                         ('synthetic-failed-backup',)).fetchone()['closed']
    assert not (tmp_path/'capture').exists()


def test_reference_retry_rejects_unfenced_pre_lifecycle_capture(backup_seeded):
    from messaging_service import backup
    store,*_=backup_seeded
    validate=getattr(backup,'validate_reference_fence',None)
    assert validate is not None,'reference retry needs a durable-fence admission gate'
    with pytest.raises(ValueError,match='backup_capture_unfenced'):
        validate(store,{'schema_version':32},'synthetic-legacy-backup')


@pytest.mark.parametrize('mode',['staged','stream'])
def test_release_requires_exact_confirmed_receiver_receipt(backup_seeded,tmp_path,mode):
    from messaging_service import backup_worker
    from messaging_service.backup_runner import write_json
    store,*_=backup_seeded;backup_id='20261016T000000Z-0123456789abcdef'
    with store.pool.connection() as c:c.execute('SELECT public.begin_backup_fence(%s)',(backup_id,))
    work=tmp_path/'work';work.mkdir(mode=0o700)
    digest='b'*64
    result={'v':1,'id':backup_id,'status':'encrypted','bytes':100,'sha256':digest,'backup_fence':backup_id}
    if mode=='staged':write_json(work/'result.json',result)
    else:write_json(work/'stream.json',{'v':2,'id':backup_id,'snapshot_sha256':'c'*64,
        'max_bytes':1024,'captured_at':'2026-10-16T00:00:00+00:00','backup_fence':backup_id})
    release=getattr(backup_worker,'release_delivered_fence',None)
    assert release is not None,'encrypted completion must not release the original fence'
    with pytest.raises(FileNotFoundError):release(store,work,backup_id)
    with store.pool.connection() as c:
        assert c.execute('SELECT ended_at FROM attachment_backup_fences WHERE id=%s',(backup_id,)).fetchone()['ended_at'] is None
    receipt={**result,'v':1 if mode=='staged' else 2,'status':'stored','snapshot_sha256':'c'*64,'duplicate':True}
    write_json(work/'delivery.json',{**receipt,'id':'20261016T000000Z-1111111111111111'})
    with pytest.raises(ValueError,match='invalid_backup_receipt'):release(store,work,backup_id)
    write_json(work/'delivery.json',receipt)
    assert release(store,work,backup_id)['status']=='fence_released'
    assert release(store,work,backup_id)['status']=='fence_released'
    with store.pool.connection() as c:
        assert c.execute('SELECT ended_at FROM attachment_backup_fences WHERE id=%s',(backup_id,)).fetchone()['ended_at'] is not None


def test_worker_reuses_exact_reference_fence_without_releasing_on_duplicate_encryption_skip(backup_seeded,tmp_path):
    from messaging_service.backup_worker import run
    from messaging_service.backup_runner import write_json
    store,*_=backup_seeded;backup_id='20261016T000000Z-fedcba9876543210'
    work=tmp_path/'work';work.mkdir(mode=0o700)
    create_snapshot(store,work/'snapshot',[sys.executable,'-c','print("synthetic dump")'],
                    reference_blobs=True,backup_fence=backup_id)
    write_json(work/'stream-start.json',{'action':'skip'})
    write_json(work/'recovery.json',{})
    config={'id':backup_id,'dsn_file':__import__('os').environ['PROBE_TEST_DSN_FILE'],
            'files_directory':str(store.files),'recovery_config_file':str(work/'recovery.json'),
            'recipient_file':str(work/'unused-recipient'),'work_directory':str(work),'mode':'stream_v2'}
    assert run(config)['status']=='already_delivered'
    metadata=json.loads((work/'stream.json').read_text())
    assert metadata['backup_fence']==backup_id
    with store.pool.connection() as c:
        assert c.execute('SELECT ended_at FROM attachment_backup_fences WHERE id=%s',(backup_id,)).fetchone()['ended_at'] is None


@pytest.mark.parametrize('delivered',[False,True])
def test_host_releases_reference_fence_only_after_durable_matching_delivery(tmp_path,delivered):
    from messaging_service.backup_runner import Runner,read_json,write_json
    from test_backup_streaming import StreamingDocker,streaming_runner
    from test_backup_operations import FakeDocker
    class FencedDocker(StreamingDocker):
        def call(self,*args):
            if args[0]=='start' and args[1].startswith('pos-backup-release-'):
                info=self.containers[args[1]];work=self.root/info['Config']['Labels']['personal-os.backup.id']
                assert read_json(work/'delivery.json')['status']=='stored'
                assert read_json(work/'fence-release.json')['action']=='release_fence'
                write_json(work/'fence-released.json',{'id':work.name,'backup_fence':work.name,'status':'fence_released'})
                info['State'].update(Running=False,Status='exited',ExitCode=0)
                self.calls.append(args);return ''
            result=super().call(*args)
            if args[0]=='start' and args[1].startswith('pos-backup-'):
                info=self.containers[args[1]];work=self.root/info['Config']['Labels']['personal-os.backup.id']
                write_json(work/'stream.json',{**read_json(work/'stream.json'),'backup_fence':work.name})
            return result
        def inspect(self,cid):
            if cid.startswith('pos-backup-release-'):return FakeDocker.inspect(self,cid)
            return super().inspect(cid)
    async def send(path,metadata,transfer,timeout,start,completed,stopped=None):
        if not delivered:raise RuntimeError('receiver_quota')
        start(True);result=await completed()
        return {**result,'status':'stored','duplicate':False}
    baseline,_=streaming_runner(tmp_path,send);docker=FencedDocker(tmp_path)
    runner=Runner(baseline.cfg,docker=docker,sleep=lambda _:None,stream_send=send)
    if delivered:runner.run()
    else:
        with pytest.raises(RuntimeError,match='receiver_quota'):runner.run()
    releases=[call for call in docker.calls if call[0]=='create' and 'pos-backup-release-' in ' '.join(call)]
    assert bool(releases)==delivered
    if delivered:
        release=releases[0]
        assert not any('/sources/' in arg or ',dst=/data,' in arg or arg.endswith(',dst=/data') or '/recipient' in arg or 'identity' in arg for arg in release)
        assert 'no-new-privileges' in release
        assert not any(name.startswith('pos-backup-release-') for name in docker.containers)


def test_committed_release_marker_does_not_leave_its_owned_container_after_crash(tmp_path):
    from messaging_service.backup_runner import write_json
    from test_backup_operations import runner_fixture
    runner,docker=runner_fixture(tmp_path);runner.prepare()
    work=tmp_path/runner.journal['id'];name='pos-backup-release-'+work.name.lower()
    write_json(work/'stream.json',{'backup_fence':work.name})
    write_json(work/'fence-released.json',{'id':work.name,'backup_fence':work.name,'status':'fence_released'})
    docker.containers[name]={'Id':name,'Config':{'Labels':{'personal-os.backup.id':work.name,
        'personal-os.backup.project':'test-project','personal-os.backup.action':'release_fence'}},
        'State':{'Running':False,'Status':'exited','ExitCode':0}}
    runner.release_fence(work)
    assert name not in docker.containers


def test_reference_retry_keeps_captured_absence_when_source_is_later_rehydrated(backup_seeded,tmp_path):
    store,*_=backup_seeded
    with store.pool.connection() as c:row=c.execute('SELECT digest FROM attachments LIMIT 1').fetchone()
    original=(store.files/row['digest']).read_bytes();expected=mark_evicted(store)
    create_snapshot(store,tmp_path/'capture',[sys.executable,'-c','print("synthetic dump")'],reference_blobs=True)
    (store.files/row['digest']).write_bytes(original)
    with store.pool.connection() as c:
        c.execute("UPDATE attachment_residency SET state='resident',eviction_id=NULL,evicted_at=NULL WHERE digest=%s",(row['digest'],))
    assert verify_bundle(tmp_path/'capture',blob_root=store.files)['absent_blobs']==[expected]


def test_concurrent_schema_upgrade_cannot_create_unfenced_lifecycle_capture(backup_seeded,tmp_path):
    from contextlib import contextmanager
    from types import SimpleNamespace
    store,*_=backup_seeded
    with store.pool.connection() as c:c.execute("UPDATE probe_metadata SET value='32' WHERE name='schema_version'")
    class UpgradePool:
        calls=0
        @contextmanager
        def connection(self):
            with store.pool.connection() as c:yield c
            self.calls+=1
            if self.calls==1:
                with store.pool.connection() as c:
                    c.execute("UPDATE probe_metadata SET value='34' WHERE name='schema_version'")
    with pytest.raises(RuntimeError,match='backup_capture_unfenced'):
        create_snapshot(SimpleNamespace(pool=UpgradePool(),files=store.files),tmp_path/'capture',
                        [sys.executable,'-c','print("synthetic dump")'])


def test_quarantine_releases_restored_backup_fences_after_verification(backup_seeded,tmp_path):
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from messaging_service.backup import quarantine_restore
    from messaging_service.store import Store
    source,*_=backup_seeded;name='lifecycle_'+uuid.uuid4().hex+'_restore_test';restored=None
    with psycopg.connect(source.pool.conninfo,autocommit=True) as c:
        c.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    try:
        restored=Store(make_conninfo(source.pool.conninfo,dbname=name),tmp_path/'originals','http://testserver')
        restored.migrate();restored.seed()
        with restored.pool.connection() as c:c.execute("SELECT public.begin_backup_fence('synthetic-restored-fence')")
        quarantine_restore(restored)
        with restored.pool.connection() as c:
            assert c.execute("SELECT ended_at IS NOT NULL AS ended FROM attachment_backup_fences WHERE id='synthetic-restored-fence'").fetchone()['ended']
    finally:
        if restored:restored.close()
        with psycopg.connect(source.pool.conninfo,autocommit=True) as c:
            c.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))


def test_uncertain_fence_commit_keeps_originals_protected_for_reconciliation(backup_seeded,tmp_path):
    from contextlib import contextmanager
    from types import SimpleNamespace
    store,*_=backup_seeded;token='synthetic-uncertain-commit'
    class AmbiguousPool:
        calls=0
        @contextmanager
        def connection(self):
            with store.pool.connection() as c:yield c
            self.calls+=1
            if self.calls==1:raise RuntimeError('uncertain_commit')
    with pytest.raises(RuntimeError,match='uncertain_commit'):
        create_snapshot(SimpleNamespace(pool=AmbiguousPool(),files=store.files),tmp_path/'capture',
                        [sys.executable,'-c','print("synthetic dump")'],backup_fence=token)
    with store.pool.connection() as c:
        assert c.execute('SELECT ended_at IS NULL AS active FROM attachment_backup_fences WHERE id=%s',(token,)).fetchone()['active']


def test_invalid_capture_fence_is_rejected_without_cleanup_masking_the_error(backup_seeded,tmp_path):
    store,*_=backup_seeded
    with pytest.raises(ValueError,match='invalid_backup_fence'):
        create_snapshot(store,tmp_path/'capture',[sys.executable,'-c','print("synthetic dump")'],backup_fence='../invalid')
