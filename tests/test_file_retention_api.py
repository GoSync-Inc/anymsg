"""Synthetic API contracts for retained understanding and managed absence."""
import hashlib
from uuid import uuid4

import pytest

from messaging_service.attachment_metadata import attachment_fields
from messaging_service.file_index import FileIndexer
from messaging_service.store import APIError


def file_with_detail(store, attachment='retained-doc', message='demo-tg-1'):
    content=b'Synthetic detailed invoice line items and totals.'
    sha=hashlib.sha256(content).hexdigest()
    (store.files/sha).write_bytes(content)
    with store.pool.connection() as c:
        c.execute('INSERT INTO attachments(id,message,digest,filename,mime,bytes) VALUES(%s,%s,%s,%s,%s,%s)',
            (attachment,message,sha,'invoice.txt','text/plain',len(content)))
    worker=FileIndexer(store.pool.conninfo,store.files)
    for _ in range(10):
        if not worker.run_once()['claimed']:break
    with store.pool.connection() as c:
        c.execute("""UPDATE attachment_understanding SET state='ready',source_artifact_hash=%s,
            profile_hash=%s,published_profile_hash=%s,published_provider='ollama',published_model='synthetic-model',
            title='Bluebird invoice',summary='A synthetic invoice for Bluebird bicycles.',
            provider='ollama',model='synthetic-model',coverage='{"basis":"extracted_text","complete":true}',
            indexed_at=clock_timestamp() WHERE attachment=%s""",('a'*64,'b'*64,'b'*64,attachment))
    return sha,len(content)


def managed_absence(store,sha,size):
    identity=uuid4()
    root=store.files.stat()
    with store.pool.connection() as c:
        c.execute("INSERT INTO attachment_eviction_journal(id,digest,bytes,root_device,root_inode,state,completed_at) VALUES(%s,%s,%s,%s,%s,'completed',clock_timestamp())",
            (identity,sha,size,root.st_dev,root.st_ino))
        c.execute("UPDATE attachment_residency SET state='evicted',eviction_id=%s,evicted_at=clock_timestamp() WHERE digest=%s",
            (identity,sha))
    (store.files/sha).unlink()


def test_missing_managed_original_metadata_preserves_description():
    fields=attachment_fields({'id':'synthetic','digest':'a'*64,'filename':'x.txt','mime':'text/plain','bytes':3,
        'original_residency':'evicted','understanding_state':'ready','understanding_title':'Budget',
        'understanding_summary':'A synthetic annual budget.','understanding_coverage':{'basis':'extracted_text'},
        'understanding_provider':'ollama','understanding_model':'synthetic'},original_present=False)
    assert fields['reason']=='original_evicted'
    assert fields['next_action']=='request_file_download'
    assert fields['title']=='Budget'
    assert fields['summary']=='A synthetic annual budget.'
    assert fields['understanding']['state']=='ready'
    assert fields['original_available'] is False


@pytest.mark.parametrize('catalog,present,expected',[
    ('resident',False,'missing'),('evicted',True,'resident'),
    ('evicted',False,'evicted'),('missing',None,'missing'),
])
def test_metadata_residency_agrees_with_verified_original_presence(catalog,present,expected):
    result=attachment_fields({'id':'synthetic','digest':'a'*64,'filename':'x.txt',
        'mime':'text/plain','bytes':3,'original_residency':catalog},original_present=present)
    assert result['original_residency']==expected


def test_opt_in_worker_commands_pass_production_runtime_guard():
    from pathlib import Path
    import yaml
    from scripts.production import validate_argv
    root=Path(__file__).resolve().parents[1]
    for name in ('file-lifecycle','file-understanding'):
        document=yaml.safe_load((root/'deploy'/('compose.'+name+'.yaml')).read_text())
        service=document['services'][name]
        validate_argv(service['command'])
    assert '--apply' not in yaml.safe_load((root/'deploy/compose.file-lifecycle.yaml').read_text())['services']['file-lifecycle']['command']


def test_understanding_is_searchable_and_does_not_replace_detail(seeded):
    store,key,_=seeded
    file_with_detail(store)
    for mode in ('all','phrase','exact'):
        result=store.search_files(key,query='Bluebird',query_mode=mode)
        assert [row['attachment_id'] for row in result['items']]==['retained-doc']
        row=result['items'][0]
        assert row['title']=='Bluebird invoice'
        assert 'understanding' in row['match_sources']
        assert row['understanding']['coverage']['basis']=='extracted_text'
    detail=store.get_file_text(key,'retained-doc')
    assert detail['text']=='Synthetic detailed invoice line items and totals.'


def test_text_and_search_survive_only_journaled_managed_eviction(seeded):
    store,key,_=seeded
    sha,size=file_with_detail(store)
    managed_absence(store,sha,size)
    detail=store.get_file_text(key,'retained-doc')
    assert 'line items' in detail['text']
    assert detail['original_available'] is False
    assert detail['original_residency']=='evicted'
    assert store.search_files(key,query='Bluebird')['items'][0]['summary']
    metadata=store.get_attachment(key,'retained-doc')
    assert metadata['reason']=='original_evicted'
    assert metadata['summary']=='A synthetic invoice for Bluebird bicycles.'
    assert 'download_url' not in metadata


def test_unmanaged_missing_bytes_still_fail_and_do_not_refresh_idle_clock(seeded):
    store,key,_=seeded
    sha,_=file_with_detail(store)
    (store.files/sha).unlink()
    with pytest.raises(APIError,match='attachment_unavailable'):
        store.get_file_text(key,'retained-doc')
    assert store.search_files(key,query='Bluebird')['items']==[]
    assert store.get_attachment(key,'retained-doc')['reason']=='original_missing'


def test_evicted_catalog_without_completed_journal_does_not_claim_managed_absence(seeded):
    store,key,_=seeded
    sha,size=file_with_detail(store)
    identity=uuid4();root=store.files.stat()
    with store.pool.connection() as c:
        c.execute("INSERT INTO attachment_eviction_journal(id,digest,bytes,root_device,root_inode,state,completed_at) VALUES(%s,%s,%s,%s,%s,'cancelled',clock_timestamp())",
            (identity,sha,size,root.st_dev,root.st_ino))
        c.execute("UPDATE attachment_residency SET state='evicted',eviction_id=%s,evicted_at=clock_timestamp() WHERE digest=%s",(identity,sha))
    (store.files/sha).unlink()
    assert store.get_attachment(key,'retained-doc')['reason']=='original_missing'
    with pytest.raises(APIError,match='attachment_unavailable'):
        store.get_file_text(key,'retained-doc')


@pytest.mark.parametrize('invalid_entry',['wrong_size','symlink','directory'])
def test_invalid_reappeared_original_is_not_managed_absence(seeded,tmp_path,invalid_entry):
    store,key,_=seeded
    sha,size=file_with_detail(store)
    managed_absence(store,sha,size)
    if invalid_entry=='wrong_size':
        (store.files/sha).write_bytes(b'corrupt')
    elif invalid_entry=='symlink':
        target=tmp_path/'synthetic-external';target.write_bytes(b'x'*size)
        (store.files/sha).symlink_to(target)
    else:
        (store.files/sha).mkdir()
    try:
        assert store.search_files(key,query='Bluebird')['items']==[]
        with pytest.raises(APIError,match='attachment_unavailable'):
            store.get_file_text(key,'retained-doc')
        metadata=store.get_attachment(key,'retained-doc')
        assert metadata['reason']=='original_missing'
        assert metadata['original_residency']=='missing'
        request=store.request_file_download(key,'retained-doc')
        assert request['request_status']=='unavailable'
        assert request['reason']=='original_missing'
    finally:
        if invalid_entry=='directory':(store.files/sha).rmdir()
        else:(store.files/sha).unlink()


def test_retained_understanding_honors_acl_profile_and_source_invalidation(seeded):
    store,key,telegram_only=seeded
    file_with_detail(store,'private-wa','demo-wa-1')
    assert store.search_files(telegram_only,query='Bluebird')['items']==[]
    with pytest.raises(APIError,match='not_found'):
        store.get_file_text(telegram_only,'private-wa')
    assert store.search_files(key,query='Bluebird')['items']
    with store.pool.connection() as c:
        c.execute("UPDATE messages SET revision=revision+1 WHERE id='demo-wa-1'")
    # A caption/formatting revision alone cannot invalidate the same file.
    assert store.search_files(key,query='Bluebird')['items']
    replacement=b'Synthetic replacement with different bytes.'
    sha=hashlib.sha256(replacement).hexdigest()
    (store.files/sha).write_bytes(replacement)
    with store.pool.connection() as c:
        c.execute("UPDATE attachments SET digest=%s,bytes=%s WHERE id='private-wa'",(sha,len(replacement)))
    assert store.search_files(key,query='Bluebird')['items']==[]


def test_search_and_text_do_not_touch_original_but_original_request_does(seeded):
    store,key,_=seeded
    sha,_=file_with_detail(store)
    with store.pool.connection() as c:
        before=c.execute("UPDATE attachment_residency SET last_accessed_at=clock_timestamp()-interval '40 days' WHERE digest=%s RETURNING last_accessed_at",(sha,)).fetchone()['last_accessed_at']
    store.search_files(key,query='invoice')
    store.get_file_text(key,'retained-doc')
    with store.pool.connection() as c:
        assert c.execute('SELECT last_accessed_at FROM attachment_residency WHERE digest=%s',(sha,)).fetchone()['last_accessed_at']==before
    assert store.get_attachment(key,'retained-doc')['download_url']
    with store.pool.connection() as c:
        assert c.execute('SELECT last_accessed_at>%s AS touched FROM attachment_residency WHERE digest=%s',(before,sha)).fetchone()['touched']


def test_audio_transcript_and_segments_survive_managed_eviction(seeded,tmp_path):
    from test_enrichment import add_audio,worker
    store,key,_=seeded
    sha=add_audio(store)
    indexer=FileIndexer(store.pool.conninfo,store.files)
    for _ in range(10):
        if not indexer.run_once()['claimed']:break
    assert worker(store,tmp_path).once()
    with store.pool.connection() as c:
        c.execute("""UPDATE attachment_understanding SET state='ready',source_artifact_hash=%s,
            profile_hash=%s,published_profile_hash=%s,published_provider='ollama',published_model='synthetic-model',
            title='Contract voice note',summary='The speaker discusses a contract.',
            provider='ollama',model='synthetic-model',indexed_at=clock_timestamp()
            WHERE attachment='enrich-audio'""",('a'*64,'b'*64,'b'*64))
    managed_absence(store,sha,len(b'synthetic-audio'))
    transcript=store.get_file_text(key,'enrich-audio',content='enriched')
    assert transcript['text']=='Расшифровка договора'
    assert transcript['title']=='Contract voice note'
    assert transcript['original_available'] is False
    segments=store.get_file_segments(key,'enrich-audio')
    assert segments['segments'][0]['text']=='Расшифровка договора'
    assert segments['segments'][0]['end']==1
    assert segments['original_available'] is False


def test_profile_change_retains_last_good_title_and_summary(seeded):
    store,key,_=seeded
    file_with_detail(store)
    with store.pool.connection() as c:
        c.execute("""INSERT INTO file_understanding_profiles(account,kind,profile_hash,provider,model,expires_at)
            VALUES('telegram-demo','document',%s,'ollama','replacement',clock_timestamp()+interval '1 hour')""",('c'*64,))
    assert store.search_files(key,query='Bluebird')['items'][0]['title']=='Bluebird invoice'
    result=store.get_file_text(key,'retained-doc')
    assert 'line items' in result['text']
    assert result['title']=='Bluebird invoice' and result['summary']
    assert result['understanding']['state']=='ready'
    assert result['understanding']['profile_current'] is False
    assert result['understanding']['refresh_state']=='queued'
    assert result['understanding']['model']=='synthetic-model'


def test_evicted_asr_profile_refresh_preserves_transcript_and_segments(seeded,tmp_path):
    from test_enrichment import add_audio,worker
    store,key,_=seeded
    sha=add_audio(store)
    old=worker(store,tmp_path)
    assert old.once()
    managed_absence(store,sha,len(b'synthetic-audio'))
    replacement=worker(store,tmp_path)
    replacement.config['profiles']['audio']['model']='replacement-asr-model'
    with store.pool.connection() as c:
        c.execute("UPDATE attachment_enrichment SET next_due=clock_timestamp()-interval '1 second' WHERE attachment='enrich-audio'")
    assert replacement.claim() is None
    result=store.get_file_text(key,'enrich-audio',content='enriched')
    assert result['text']=='Расшифровка договора'
    assert store.get_file_segments(key,'enrich-audio')['segments'][0]['end']==1


def test_resident_asr_profile_refresh_reuses_completed_transcript(seeded,tmp_path):
    from test_enrichment import add_audio,worker
    store,key,_=seeded
    add_audio(store)
    assert worker(store,tmp_path).once()
    replacement=worker(store,tmp_path)
    replacement.config['profiles']['audio']['model']='replacement-asr-model'
    with store.pool.connection() as c:
        c.execute("UPDATE attachment_enrichment SET next_due=clock_timestamp()-interval '1 second' WHERE attachment='enrich-audio'")
    assert replacement.claim() is None
    assert store.get_file_text(key,'enrich-audio',content='enriched')['text']=='Расшифровка договора'
