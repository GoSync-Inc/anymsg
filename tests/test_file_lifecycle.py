"""Synthetic original residency, crash replay and bounded rehydration contracts."""
import hashlib
from importlib import import_module

import pytest

from test_archive import archive, event


def lifecycle():
    try:
        return import_module('messaging_service.file_lifecycle')
    except ModuleNotFoundError:
        pytest.fail('safe original lifecycle is not implemented')


def stored(archive, *, mid='lifecycle-1', payload=b'Synthetic retained contract', mime='text/plain'):
    from messaging_service.blobs import BlobWriter
    ar,s,key,_=archive
    ar.ingest(event(provider_id=mid,media=[{'provider_id':'asset-'+mid,
        'filename':'Synthetic.txt','mime':mime,'bytes':len(payload)}]))
    job=ar.claim_media('fixture')
    writer=BlobWriter(s.files,reserve_bytes=0)
    blob=writer.write([payload]);ar.finish_media(job,blob,writer)
    return job['id'],blob


def test_new_original_starts_full_thirty_day_grace_period(archive):
    _,s,_,_=archive
    fid,blob=stored(archive)
    api=lifecycle()
    with s.pool.connection() as c:
        api.touch_original(c,blob.digest,blob.bytes)
        row=c.execute('''SELECT *,last_accessed_at>clock_timestamp()-interval '1 minute' AS recent
            FROM attachment_residency WHERE digest=%s''',(blob.digest,)).fetchone()
    assert row['state']=='resident' and row['recent'] and not row['pinned']
    assert api.LifecycleWorker(s).run_once()['evicted']==0
    assert (s.files/blob.digest).read_bytes()==b'Synthetic retained contract'


def test_backup_fence_remains_active_after_connection_closes(archive):
    _,s,_,_=archive;api=lifecycle()
    with s.pool.connection() as c:
        api.begin_backup_fence(c,'synthetic-backup-1')
    with s.pool.connection() as c:
        assert api.backup_fence_active(c)
        api.begin_backup_fence(c,'synthetic-backup-1')
        api.end_backup_fence(c,'synthetic-backup-1')
    with s.pool.connection() as c:
        assert not api.backup_fence_active(c)
        with pytest.raises(ValueError,match='backup_fence_closed'):
            api.begin_backup_fence(c,'synthetic-backup-1')


@pytest.fixture(autouse=True)
def clear_lifecycle_state(database):
    with database.pool.connection() as c:
        c.execute('UPDATE attachment_residency SET eviction_id=NULL,state=\'resident\',evicted_at=NULL')
        c.execute('DELETE FROM attachment_eviction_journal')
        c.execute('DELETE FROM attachment_backup_fences')
        c.execute('DELETE FROM attachment_residency')


def processed(archive,*,mid='lifecycle-1',payload=b'Synthetic retained contract',mime='text/plain'):
    from psycopg.types.json import Jsonb
    fid,blob=stored(archive,mid=mid,payload=payload,mime=mime)
    _,s,_,_=archive
    kind='video' if mime.startswith('video/') else 'audio' if mime.startswith('audio/') else 'image' if mime.startswith('image/') else 'document'
    mode={'video':'sampled_frames','image':'image_description','audio':'transcript_summary','document':'extracted_text_summary'}[kind]
    with s.pool.connection() as c:
        if kind=='document':
            c.execute("UPDATE attachment_text SET state='ready',text_content='Full synthetic extracted text',truncated=false,error_code=NULL WHERE attachment=%s",(fid,))
        if kind=='audio':
            c.execute("UPDATE attachment_enrichment SET state='ready',kind='audio',text_content='Full synthetic speech transcript',truncated=false,error_code=NULL WHERE attachment=%s",(fid,))
        coverage={'mode':mode,'complete':kind!='video'}
        if kind=='video':coverage.update(sampled_frames=2,sampled_timestamps_seconds=[0.,2.],duration_seconds=4.,audio_transcribed=False)
        from messaging_service.file_understanding import current_understanding_source,source_artifact_hash
        source=current_understanding_source(c,fid)
        c.execute("""UPDATE attachment_understanding SET state='ready',title='Synthetic contract',summary='Agreement description',
            source_artifact_hash=%s,coverage=%s,truncated=false,profile_hash=%s,published_profile_hash=%s,
            provider='ollama',model='synthetic-model',published_provider='ollama',published_model='synthetic-model',
            indexed_at=clock_timestamp() WHERE attachment=%s""",(source_artifact_hash(source),Jsonb(coverage),'b'*64,'b'*64,fid))
        c.execute("UPDATE attachment_residency SET last_accessed_at=clock_timestamp()-interval '31 days' WHERE digest=%s",(blob.digest,))
    return fid,blob


def test_processed_idle_original_is_only_candidate_until_apply(archive):
    _,s,_,_=archive;fid,blob=processed(archive);api=lifecycle()
    report=api.LifecycleWorker(s).run_once()
    assert report['dry_run'] is True and report['candidates']==1 and report['evicted']==0
    assert (s.files/blob.digest).exists()
    report=api.LifecycleWorker(s).run_once(apply=True)
    assert report['evicted']==1 and report['evicted_bytes']==blob.bytes
    assert not (s.files/blob.digest).exists()
    with s.pool.connection() as c:
        assert api.is_managed_evicted(c,blob.digest,blob.bytes)
        assert c.execute('SELECT text_content FROM attachment_text WHERE attachment=%s',(fid,)).fetchone()['text_content']=='Full synthetic extracted text'
        assert c.execute('SELECT title FROM attachment_understanding WHERE attachment=%s',(fid,)).fetchone()['title']=='Synthetic contract'
        assert c.execute('SELECT count(*) AS n FROM attachments WHERE id=%s',(fid,)).fetchone()['n']==1


@pytest.mark.parametrize('missing',['indexed_at','published_profile_hash','both'])
def test_ready_unpublished_description_does_not_authorize_eviction(archive,missing):
    _,s,key,_=archive;fid,blob=processed(archive);api=lifecycle()
    with s.pool.connection() as c:
        if missing in ('indexed_at','both'):
            c.execute('UPDATE attachment_understanding SET indexed_at=NULL WHERE attachment=%s',(fid,))
        if missing in ('published_profile_hash','both'):
            c.execute('UPDATE attachment_understanding SET published_profile_hash=NULL WHERE attachment=%s',(fid,))
    report=api.LifecycleWorker(s).run_once(apply=True)
    assert report['evicted']==0 and report['reasons']=={'description_incomplete':1}
    assert (s.files/blob.digest).exists()
    with s.pool.connection() as c:
        assert not api.is_managed_evicted(c,blob.digest,blob.bytes)


def test_worker_eviction_keeps_published_description_visible_to_api_and_search(archive):
    _,s,key,_=archive;fid,blob=processed(archive);api=lifecycle()
    # A successful older model publication remains useful after configuration
    # changes; deletion must not require equality with the active profile.
    with s.pool.connection() as c:
        c.execute("""INSERT INTO file_understanding_profiles(account,kind,profile_hash,provider,model,expires_at)
            VALUES('archive-test','document',%s,'ollama','new-synthetic-model',clock_timestamp()+interval '1 hour')""",('c'*64,))
    assert api.LifecycleWorker(s).run_once(apply=True)['evicted']==1
    assert not (s.files/blob.digest).exists()
    metadata=s.get_attachment(key,fid)
    assert metadata['reason']=='original_evicted'
    assert metadata['title']=='Synthetic contract' and metadata['summary']=='Agreement description'
    assert metadata['understanding']['state']=='ready'
    assert metadata['understanding']['profile_current'] is False
    hits=s.search_files(key,query='Agreement')['items']
    assert [row['attachment_id'] for row in hits]==[fid]
    assert hits[0]['summary']=='Agreement description' and 'understanding' in hits[0]['match_sources']
    assert s.get_file_text(key,fid)['text']=='Full synthetic extracted text'


def test_pin_protects_every_occurrence_of_shared_original(archive):
    _,s,_,_=archive;fid,blob=processed(archive)
    second,_=processed(archive,mid='lifecycle-2')
    api=lifecycle()
    with s.pool.connection() as c:
        api.pin_original(c,blob.digest,True)
    assert api.LifecycleWorker(s).run_once(apply=True)['evicted']==0
    assert (s.files/blob.digest).exists()
    with s.pool.connection() as c:
        api.pin_original(c,blob.digest,False)
    assert api.LifecycleWorker(s).run_once(apply=True)['evicted']==1


def test_touch_restarts_idle_window_and_never_unpins(archive):
    _,s,_,_=archive;_,blob=processed(archive);api=lifecycle()
    with s.pool.connection() as c:
        api.pin_original(c,blob.digest,True)
        api.touch_original(c,blob.digest,blob.bytes)
        assert c.execute('SELECT pinned FROM attachment_residency WHERE digest=%s',(blob.digest,)).fetchone()['pinned']
        api.pin_original(c,blob.digest,False)
    assert api.LifecycleWorker(s).run_once(apply=True)['evicted']==0


@pytest.mark.parametrize('blocked', ['unprocessed','partial_text','partial_description','stale_description',
    'active_asr','active_download','pending_request','download_grant','fence'])
def test_incomplete_or_inflight_originals_cannot_be_evicted(archive,blocked):
    _,s,key,_=archive;fid,blob=processed(archive);api=lifecycle()
    with s.pool.connection() as c:
        if blocked=='unprocessed':c.execute("UPDATE attachment_text SET state='queued',text_content='' WHERE attachment=%s",(fid,))
        if blocked=='partial_text':c.execute('UPDATE attachment_text SET truncated=true WHERE attachment=%s',(fid,))
        if blocked=='partial_description':c.execute('UPDATE attachment_understanding SET truncated=true WHERE attachment=%s',(fid,))
        if blocked=='stale_description':c.execute('UPDATE attachment_understanding SET source_revision=source_revision+1 WHERE attachment=%s',(fid,))
        if blocked=='active_asr':c.execute("UPDATE attachment_enrichment SET state='leased',lease_until=now()+interval '1 hour' WHERE attachment=%s",(fid,))
        if blocked=='active_download':c.execute("UPDATE media_jobs SET state='leased',lease_until=now()+interval '1 hour' WHERE id=%s",(fid,))
        if blocked=='pending_request':c.execute("UPDATE media_jobs SET requested_until=now()+interval '1 hour' WHERE id=%s",(fid,))
        if blocked=='fence':api.begin_backup_fence(c,'synthetic-backup-active')
    if blocked=='download_grant':
        s.get_attachment(key,fid)
        with s.pool.connection() as c:c.execute("UPDATE attachment_residency SET last_accessed_at=now()-interval '31 days' WHERE digest=%s",(blob.digest,))
    assert api.LifecycleWorker(s).run_once(apply=True)['evicted']==0
    assert (s.files/blob.digest).exists()


@pytest.mark.parametrize('unsafe',['corrupt','symlink','missing'])
def test_unverified_original_is_not_declared_managed_evicted(archive,unsafe,tmp_path):
    _,s,_,_=archive;_,blob=processed(archive);api=lifecycle();path=s.files/blob.digest
    path.unlink()
    if unsafe=='corrupt':path.write_bytes(b'x'*blob.bytes)
    if unsafe=='symlink':
        target=tmp_path/'important-original';target.write_bytes(b'Synthetic retained contract');path.symlink_to(target)
    assert api.LifecycleWorker(s).run_once(apply=True)['evicted']==0
    with s.pool.connection() as c:assert not api.is_managed_evicted(c,blob.digest,blob.bytes)
    if unsafe=='corrupt':assert path.read_bytes()==b'x'*blob.bytes
    if unsafe=='symlink':assert target.read_bytes()==b'Synthetic retained contract'


def test_eviction_limits_are_real_bounds(archive):
    _,s,_,_=archive;_,one=processed(archive,payload=b'Synthetic one')
    _,two=processed(archive,mid='lifecycle-2',payload=b'Synthetic two')
    api=lifecycle()
    report=api.LifecycleWorker(s).run_once(apply=True,limit=1,max_bytes=one.bytes)
    assert report['evicted']==1 and report['evicted_bytes']<=one.bytes
    assert sum((s.files/b.digest).exists() for b in (one,two))==1
    with pytest.raises(ValueError,match='invalid_lifecycle_limit'):api.LifecycleWorker(s).run_once(limit=0)
    with pytest.raises(ValueError,match='invalid_lifecycle_limit'):api.LifecycleWorker(s).run_once(max_bytes=True)


@pytest.mark.parametrize('blocker',['unprocessed','corrupt','oversized'])
def test_repeated_bounded_passes_visit_later_eligible_originals(archive,blocker):
    _,s,_,_=archive;api=lifecycle()
    fid,older=processed(archive,payload=b'Older synthetic original '*4)
    _,later=processed(archive,mid='lifecycle-later',payload=b'Later synthetic original')
    with s.pool.connection() as c:
        if blocker=='unprocessed':
            c.execute("UPDATE attachment_text SET state='queued',text_content='' WHERE attachment=%s",(fid,))
        c.execute("UPDATE attachment_residency SET last_accessed_at=clock_timestamp()-interval '32 days',updated_at=clock_timestamp()-interval '32 days' WHERE digest=%s",(older.digest,))
        c.execute("UPDATE attachment_residency SET updated_at=clock_timestamp()-interval '31 days' WHERE digest=%s",(later.digest,))
        original_access=c.execute('SELECT last_accessed_at FROM attachment_residency WHERE digest=%s',(older.digest,)).fetchone()['last_accessed_at']
    if blocker=='corrupt':(s.files/older.digest).write_bytes(b'x'*older.bytes)
    budget=later.bytes if blocker=='oversized' else older.bytes
    first=api.LifecycleWorker(s).run_once(apply=True,limit=1,max_bytes=budget)
    assert first['evicted']==0 and first['blocked']==1
    # Recreate the worker to prove traversal survives one-shot process restarts.
    second=api.LifecycleWorker(s).run_once(apply=True,limit=1,max_bytes=budget)
    assert second['evicted']==1
    assert (s.files/older.digest).exists() and not (s.files/later.digest).exists()
    with s.pool.connection() as c:
        row=c.execute('SELECT last_accessed_at,state FROM attachment_residency WHERE digest=%s',(older.digest,)).fetchone()
    assert row['last_accessed_at']==original_access and row['state']=='resident'


def test_preview_does_not_advance_inspection_order(archive):
    _,s,_,_=archive;_,blob=processed(archive);api=lifecycle()
    with s.pool.connection() as c:
        before=c.execute('SELECT updated_at FROM attachment_residency WHERE digest=%s',(blob.digest,)).fetchone()['updated_at']
    assert api.LifecycleWorker(s).run_once()['candidates']==1
    with s.pool.connection() as c:
        after=c.execute('SELECT updated_at FROM attachment_residency WHERE digest=%s',(blob.digest,)).fetchone()['updated_at']
    assert after==before


def test_backup_fence_stops_pass_before_source_traversal(archive,monkeypatch):
    _,s,_,_=archive;_,blob=processed(archive);api=lifecycle();worker=api.LifecycleWorker(s)
    with s.pool.connection() as c:
        api.begin_backup_fence(c,'synthetic-traversal-fence')
        before=c.execute('SELECT updated_at FROM attachment_residency WHERE digest=%s',(blob.digest,)).fetchone()['updated_at']
    def unexpected(*args):pytest.fail('active backup must stop source traversal')
    monkeypatch.setattr(worker,'_eligible',unexpected)
    report=worker.run_once(apply=True)
    assert report['evicted']==0 and report['reasons']=={'backup_active':1}
    with s.pool.connection() as c:
        after=c.execute('SELECT updated_at FROM attachment_residency WHERE digest=%s',(blob.digest,)).fetchone()['updated_at']
    assert before==after


def test_journal_replays_crash_after_unlink_without_losing_derivatives(archive,monkeypatch):
    _,s,_,_=archive;fid,blob=processed(archive);api=lifecycle();worker=api.LifecycleWorker(s)
    original=worker._mark_evicted
    def crash(*args):raise RuntimeError('synthetic_crash_after_unlink')
    monkeypatch.setattr(worker,'_mark_evicted',crash)
    with pytest.raises(RuntimeError,match='synthetic_crash'):worker.run_once(apply=True)
    assert not (s.files/blob.digest).exists()
    with s.pool.connection() as c:
        assert c.execute('SELECT state FROM attachment_residency WHERE digest=%s',(blob.digest,)).fetchone()['state']=='evicting'
        with pytest.raises(ValueError,match='backup_eviction_pending'):api.begin_backup_fence(c,'synthetic-racing-backup')
    monkeypatch.setattr(worker,'_mark_evicted',original)
    assert worker.run_once(apply=True)['evicted']==1
    with s.pool.connection() as c:
        assert api.is_managed_evicted(c,blob.digest,blob.bytes)
        assert c.execute('SELECT state FROM attachment_text WHERE attachment=%s',(fid,)).fetchone()['state']=='ready'
    assert worker.run_once(apply=True)['evicted']==0


def test_recent_touch_between_prepare_and_unlink_cancels_eviction(archive,monkeypatch):
    _,s,_,_=archive;_,blob=processed(archive);api=lifecycle();worker=api.LifecycleWorker(s)
    complete=worker._complete
    def touched(token):
        with s.pool.connection() as c:api.touch_original(c,blob.digest,blob.bytes)
        return complete(token)
    monkeypatch.setattr(worker,'_complete',touched)
    assert worker.run_once(apply=True)['evicted']==0
    assert (s.files/blob.digest).exists()
    with s.pool.connection() as c:
        assert c.execute('SELECT state FROM attachment_residency WHERE digest=%s',(blob.digest,)).fetchone()['state']=='resident'
        assert not api.is_managed_evicted(c,blob.digest,blob.bytes)


def test_managed_redownload_preserves_identity_detail_and_retry_history(archive):
    from messaging_service.blobs import BlobWriter
    ar,s,key,_=archive;fid,blob=processed(archive);api=lifecycle()
    with s.pool.connection() as c:c.execute('UPDATE media_jobs SET attempts=2 WHERE id=%s',(fid,))
    assert api.LifecycleWorker(s).run_once(apply=True)['evicted']==1
    result=s.request_file_download(key,fid)
    assert result['request_status']=='queued' and result['reason']=='managed_original_evicted'
    again=s.request_file_download(key,fid)
    assert again['request_status']=='coalesced' and again['requested_until']==result['requested_until']
    job=ar.claim_media('fixture');assert job['id']==fid and job['attempts']==3
    writer=BlobWriter(s.files,reserve_bytes=0);verified=writer.write([b'Synthetic retained contract'])
    ar.finish_media(job,verified,writer)
    with s.pool.connection() as c:
        assert not api.is_managed_evicted(c,blob.digest,blob.bytes)
        assert c.execute('SELECT attempts FROM media_jobs WHERE id=%s',(fid,)).fetchone()['attempts']==3
        assert c.execute('SELECT state FROM attachment_text WHERE attachment=%s',(fid,)).fetchone()['state']=='ready'
        assert c.execute('SELECT state FROM attachment_understanding WHERE attachment=%s',(fid,)).fetchone()['state']=='ready'


def test_managed_redownload_cannot_replace_retained_digest(archive):
    from messaging_service.blobs import BlobWriter
    ar,s,key,_=archive;fid,blob=processed(archive);api=lifecycle()
    api.LifecycleWorker(s).run_once(apply=True);s.request_file_download(key,fid)
    job=ar.claim_media('fixture');writer=BlobWriter(s.files,reserve_bytes=0)
    different=writer.write([b'x'*blob.bytes])
    with pytest.raises(ValueError,match='media_identity_mismatch'):ar.finish_media(job,different,writer)
    with s.pool.connection() as c:
        assert c.execute('SELECT digest FROM attachments WHERE id=%s',(fid,)).fetchone()['digest']==blob.digest
        assert api.is_managed_evicted(c,blob.digest,blob.bytes)


@pytest.mark.parametrize('mime',['audio/ogg','image/png','video/mp4'])
def test_appropriate_retained_detail_enables_processed_media_eviction(archive,mime):
    _,s,_,_=archive;_,blob=processed(archive,mime=mime)
    assert lifecycle().LifecycleWorker(s).run_once(apply=True)['evicted']==1
    assert not (s.files/blob.digest).exists()


def test_same_revision_detail_change_invalidates_old_description(archive):
    _,s,_,_=archive;fid,blob=processed(archive)
    with s.pool.connection() as c:c.execute("UPDATE attachment_text SET text_content='Replaced synthetic detail' WHERE attachment=%s",(fid,))
    assert lifecycle().LifecycleWorker(s).run_once(apply=True)['evicted']==0
    assert (s.files/blob.digest).exists()


def test_corrupt_description_artifact_hash_is_not_eligible(archive):
    _,s,_,_=archive;fid,blob=processed(archive)
    with s.pool.connection() as c:c.execute('UPDATE attachment_understanding SET source_artifact_hash=%s WHERE attachment=%s',('b'*64,fid))
    assert lifecycle().LifecycleWorker(s).run_once(apply=True)['evicted']==0


@pytest.mark.parametrize('case',['failed','exhausted','cooldown','unavailable'])
def test_managed_redownload_preserves_terminal_backoff_and_provider_absence(archive,case):
    ar,s,key,_=archive;fid,blob=processed(archive);api=lifecycle()
    api.LifecycleWorker(s).run_once(apply=True)
    with s.pool.connection() as c:
        if case=='failed':c.execute("UPDATE media_jobs SET state='failed',error_code='integrity_error',attempts=3 WHERE id=%s",(fid,))
        if case=='exhausted':c.execute('UPDATE media_jobs SET attempts=5 WHERE id=%s',(fid,))
        if case=='cooldown':c.execute("UPDATE media_jobs SET next_due=now()+interval '3 hours',error_code='rate_limited' WHERE id=%s",(fid,))
    result=s.request_file_download(key,fid)
    if case in ('failed','exhausted'):assert result['request_status']=='failed'
    elif case=='cooldown':assert result['request_status']=='deferred'
    else:
        assert result['request_status']=='queued'
        job=ar.claim_media('fixture');ar.fail_media(job,'unavailable',permanent=True)
        result=s.request_file_download(key,fid)
        assert result['request_status']=='failed' and result['reason']=='unavailable'
    with s.pool.connection() as c:assert api.is_managed_evicted(c,blob.digest,blob.bytes)


def test_root_replacement_during_pending_replay_fails_closed(archive,monkeypatch,tmp_path):
    _,s,_,_=archive;_,blob=processed(archive);api=lifecycle();worker=api.LifecycleWorker(s)
    def crash(*args):raise RuntimeError('synthetic_crash')
    monkeypatch.setattr(worker,'_mark_evicted',crash)
    with pytest.raises(RuntimeError):worker.run_once(apply=True)
    monkeypatch.undo()
    original_root=s.files
    moved=original_root.with_name(original_root.name+'-old');original_root.rename(moved)
    original_root.mkdir()
    try:
        assert worker.run_once(apply=True)['evicted']==0
        with s.pool.connection() as c:assert not api.is_managed_evicted(c,blob.digest,blob.bytes)
    finally:
        original_root.rmdir();moved.rename(original_root)


def test_concurrent_pin_during_hash_cancels_before_unlink(archive,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    _,s,_,_=archive;_,blob=processed(archive);api=lifecycle();worker=api.LifecycleWorker(s)
    original=worker._verified;prepared=Event();resume=Event()
    calls=0
    def paused(*args):
        nonlocal calls
        calls+=1
        value=original(*args)
        if calls==2:
            prepared.set();assert resume.wait(10)
        return value
    monkeypatch.setattr(worker,'_verified',paused)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future=executor.submit(worker.run_once,apply=True)
        assert prepared.wait(10)
        with s.pool.connection() as c:api.pin_original(c,blob.digest,True)
        resume.set();assert future.result(timeout=10)['evicted']==0
    assert (s.files/blob.digest).exists()


def test_size_bound_skips_large_original_and_processes_small_one(archive):
    _,s,_,_=archive;_,large=processed(archive,payload=b'First synthetic large original')
    _,small=processed(archive,mid='lifecycle-small',payload=b'Small')
    report=lifecycle().LifecycleWorker(s).run_once(apply=True,max_bytes=small.bytes)
    assert report['evicted']==1 and report['evicted_bytes']==small.bytes
    assert (s.files/large.digest).exists() and not (s.files/small.digest).exists()


def test_legacy_managed_original_without_provider_job_reports_unavailable(archive):
    _,s,key,_=archive;fid,blob=processed(archive)
    with s.pool.connection() as c:c.execute('DELETE FROM media_jobs WHERE id=%s',(fid,))
    assert lifecycle().LifecycleWorker(s).run_once(apply=True)['evicted']==1
    result=s.request_file_download(key,fid)
    assert result['request_status']=='unavailable' and result['reason']=='provider_source_unavailable'
    with s.pool.connection() as c:assert c.execute('SELECT count(*) AS n FROM media_jobs WHERE id=%s',(fid,)).fetchone()['n']==0


def test_partial_audio_metadata_never_authorizes_original_eviction(archive):
    from psycopg.types.json import Jsonb
    from messaging_service.file_understanding import current_understanding_source,source_artifact_hash
    _,s,_,_=archive;fid,blob=processed(archive,mime='audio/ogg')
    with s.pool.connection() as c:
        c.execute('UPDATE attachment_enrichment SET metadata=%s WHERE attachment=%s',(Jsonb({'audio_truncated':True}),fid))
        source=current_understanding_source(c,fid)
        c.execute("""UPDATE attachment_understanding SET state='ready',title='Partial speech',summary='Partial audio only',
            source_artifact_hash=%s,coverage=%s,published_profile_hash=%s,published_provider='ollama',
            published_model='synthetic-model',indexed_at=clock_timestamp() WHERE attachment=%s""",
            (source_artifact_hash(source),Jsonb({'mode':'transcript_summary','complete':True}),'b'*64,fid))
    assert lifecycle().LifecycleWorker(s).run_once(apply=True)['evicted']==0
    assert (s.files/blob.digest).exists()


def test_cli_preview_and_apply_require_explicit_operator_arguments(archive,capsys):
    _,s,_,_=archive;_,blob=processed(archive)
    api=lifecycle()
    assert api.main(['--dsn-file',__import__('os').environ['PROBE_TEST_DSN_FILE'],'--files',str(s.files)])==0
    assert '"dry_run": true' in capsys.readouterr().out
    assert (s.files/blob.digest).exists()
    assert api.main(['--dsn-file',__import__('os').environ['PROBE_TEST_DSN_FILE'],'--files',str(s.files),
        '--apply','--limit','1','--max-bytes',str(blob.bytes)])==0
    assert '"evicted": 1' in capsys.readouterr().out
    assert not (s.files/blob.digest).exists()


def test_operator_role_can_evict_but_cannot_modify_archive_or_derived_content(archive):
    from contextlib import contextmanager
    from copy import copy
    from pathlib import Path
    from uuid import uuid4
    import psycopg
    from psycopg import sql
    _,s,_,_=archive;_,blob=processed(archive)
    role='lifecycle_'+uuid4().hex
    path=Path('services/messaging/messaging_service/file_lifecycle_role.sql')
    assert path.exists(),'dedicated lifecycle DB role is required'
    ddl=path.read_text().replace('messaging_file_lifecycle',role)
    class RestrictedPool:
        @contextmanager
        def connection(self):
            with s.pool.connection() as c:
                c.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(role)))
                yield c
    restricted=copy(s);restricted.pool=RestrictedPool()
    with s.pool.connection() as c:c.execute(ddl)
    try:
        assert lifecycle().LifecycleWorker(restricted).run_once(apply=True)['evicted']==1
        for statement in ("UPDATE attachments SET active=false","UPDATE media_jobs SET id=id||'-renamed'","UPDATE attachment_text SET attachment=attachment",'DELETE FROM attachment_text',
                "UPDATE attachment_understanding SET title='Forged'",'DELETE FROM messages'):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with restricted.pool.connection() as c:c.execute(statement)
    finally:
        with s.pool.connection() as c:
            c.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
            c.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


def test_new_shared_occurrence_cannot_publish_ready_after_preflight_eviction(archive,monkeypatch):
    from messaging_service.blobs import BlobWriter
    ar,s,_,_=archive;fid,blob=processed(archive)
    ar.ingest(event(provider_id='new-during-eviction',media=[{'provider_id':'new-asset',
        'filename':'Other occurrence.txt','mime':'text/plain','bytes':blob.bytes}]))
    job=ar.claim_media('fixture');writer=BlobWriter(s.files,reserve_bytes=0)
    same=writer.write([b'Synthetic retained contract'])
    verify=writer.verify
    def verified_then_evicted(value):
        path=verify(value)
        assert lifecycle().LifecycleWorker(s).run_once(apply=True)['evicted']==1
        return path
    monkeypatch.setattr(writer,'verify',verified_then_evicted)
    with pytest.raises(RuntimeError,match='blob_corrupt'):ar.finish_media(job,same,writer)
    with s.pool.connection() as c:
        assert c.execute('SELECT state FROM media_jobs WHERE id=%s',(job['id'],)).fetchone()['state']=='leased'
        assert c.execute('SELECT count(*) AS n FROM attachments WHERE id=%s',(job['id'],)).fetchone()['n']==0


def test_corrupt_original_hashes_still_consume_pass_byte_budget(archive,monkeypatch):
    _,s,_,_=archive;_,one=processed(archive,payload=b'First synthetic payload')
    _,two=processed(archive,mid='lifecycle-corrupt-two',payload=b'Other synthetic payload')
    for blob in (one,two):(s.files/blob.digest).write_bytes(b'x'*blob.bytes)
    api=lifecycle();worker=api.LifecycleWorker(s);hashed=[];verify=worker._verified
    def counted(digest,size):
        hashed.append(size);return verify(digest,size)
    monkeypatch.setattr(worker,'_verified',counted)
    assert worker.run_once(apply=True,max_bytes=max(one.bytes,two.bytes))['evicted']==0
    assert sum(hashed)<=max(one.bytes,two.bytes)


def test_missing_replay_rechecks_root_after_verification_before_commit(archive,monkeypatch):
    _,s,_,_=archive;_,blob=processed(archive);api=lifecycle();worker=api.LifecycleWorker(s)
    mark=worker._mark_evicted
    def crash(*args):raise RuntimeError('synthetic_crash')
    monkeypatch.setattr(worker,'_mark_evicted',crash)
    with pytest.raises(RuntimeError):worker.run_once(apply=True)
    monkeypatch.setattr(worker,'_mark_evicted',mark)
    verify=worker._verified;original_root=s.files;moved=original_root.with_name(original_root.name+'-moved')
    def root_changed_after_missing(*args):
        try:return verify(*args)
        except api.OriginalMissing:
            original_root.rename(moved);original_root.mkdir()
            raise
    monkeypatch.setattr(worker,'_verified',root_changed_after_missing)
    try:
        assert worker.run_once(apply=True)['evicted']==0
        with s.pool.connection() as c:assert not api.is_managed_evicted(c,blob.digest,blob.bytes)
    finally:
        original_root.rmdir();moved.rename(original_root)


def test_least_privilege_collector_publishes_and_gateway_requeues_managed_original(archive):
    from contextlib import contextmanager
    from copy import copy
    from pathlib import Path
    from uuid import uuid4
    from psycopg import sql
    from messaging_service.archive import Archive
    from messaging_service.blobs import BlobWriter
    ar,s,key,_=archive
    roles={kind:kind+'_'+uuid4().hex for kind in ('messaging_collector','messaging_gateway')}
    class RestrictedPool:
        def __init__(self,role):self.role=role
        @contextmanager
        def connection(self):
            with s.pool.connection() as c:
                c.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(self.role)))
                yield c
    for kind,role in roles.items():
        path=Path('services/messaging/messaging_service')/(kind.removeprefix('messaging_')+'_role.sql')
        with s.pool.connection() as c:c.execute(path.read_text().replace(kind,role))
    collector=copy(s);collector.pool=RestrictedPool(roles['messaging_collector'])
    gateway=copy(s);gateway.pool=RestrictedPool(roles['messaging_gateway'])
    restricted_archive=Archive(collector)
    try:
        payload=b'Synthetic role publication'
        ar.ingest(event(provider_id='role-original',media=[{'provider_id':'role-asset',
            'filename':'Synthetic roles.txt','mime':'text/plain','bytes':len(payload)}]))
        job=restricted_archive.claim_media('fixture')
        writer=BlobWriter(s.files,reserve_bytes=0);blob=writer.write([payload])
        restricted_archive.finish_media(job,blob,writer)
        fid=job['id']
        from psycopg.types.json import Jsonb
        from messaging_service.file_understanding import current_understanding_source,source_artifact_hash
        with s.pool.connection() as c:
            c.execute("UPDATE attachment_text SET state='ready',text_content='Full role fixture detail' WHERE attachment=%s",(fid,))
            source=current_understanding_source(c,fid)
            c.execute("""UPDATE attachment_understanding SET state='ready',source_artifact_hash=%s,title='Roles',
                summary='Role fixture description',coverage=%s,profile_hash=%s,published_profile_hash=%s,
                provider='ollama',model='synthetic-model',published_provider='ollama',published_model='synthetic-model',
                indexed_at=clock_timestamp() WHERE attachment=%s""",
                (source_artifact_hash(source),Jsonb({'mode':'extracted_text_summary','complete':True}),'b'*64,'b'*64,fid))
            c.execute("UPDATE attachment_residency SET last_accessed_at=now()-interval '31 days' WHERE digest=%s",(blob.digest,))
        assert lifecycle().LifecycleWorker(s).run_once(apply=True)['evicted']==1
        assert gateway.request_file_download(key,fid)['request_status']=='queued'
        again=restricted_archive.claim_media('fixture')
        verified=writer.write([payload]);restricted_archive.finish_media(again,verified,writer)
        with s.pool.connection() as c:
            assert c.execute('SELECT state FROM attachment_understanding WHERE attachment=%s',(fid,)).fetchone()['state']=='ready'
            assert not lifecycle().is_managed_evicted(c,blob.digest,blob.bytes)
    finally:
        for role in roles.values():
            with s.pool.connection() as c:
                c.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
                c.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


def test_replay_syncs_missing_directory_before_marking_evicted(archive,monkeypatch):
    _,s,_,_=archive;_,blob=processed(archive);api=lifecycle();worker=api.LifecycleWorker(s)
    sync=api.os.fsync;calls=[]
    def interrupted(fd):
        calls.append(fd)
        if len(calls)==1:raise OSError('synthetic directory sync interruption')
        return sync(fd)
    monkeypatch.setattr(api.os,'fsync',interrupted)
    with pytest.raises(OSError,match='synthetic'):worker.run_once(apply=True)
    assert not (s.files/blob.digest).exists()
    assert worker.run_once(apply=True)['evicted']==1
    assert len(calls)==2
