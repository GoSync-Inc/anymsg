"""Admission protects independent scopes, native children and archive fences."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
from uuid import uuid4

import pytest

from messaging_service.enrichment import EnrichmentWorker, configuration
from messaging_service.enrichment_providers import normalize_result, validate_profile
from messaging_service.file_extract import Extraction
from messaging_service.file_index import FileIndexer
from messaging_service import resource_admission as admission


def local_profile(tmp_path,kind='audio'):
    raw={'provider':'faster_whisper','model':str(tmp_path)} if kind=='audio' else {
        'provider':'ollama','model':'synthetic-local','local_only_daemon':True}
    return validate_profile(raw,kind)


def cloud_profile(tmp_path,kind='image'):
    return validate_profile({'provider':'openai','model':'synthetic-cloud','allow_cloud':True,
        'api_key_file':str(tmp_path/'unused-secret')},kind)


def add_file(s,name,message='demo-tg-1',mime='audio/ogg',data=b'synthetic-audio'):
    digest=hashlib.sha256(data).hexdigest();(s.files/digest).write_bytes(data)
    with s.pool.connection() as c:
        c.execute('INSERT INTO attachments(id,message,digest,filename,mime,bytes) VALUES(%s,%s,%s,%s,%s,%s)',
            (name,message,digest,name,mime,len(data)))


@pytest.fixture
def configured(seeded,tmp_path):
    s,_,_=seeded
    with s.pool.connection() as c:c.execute('TRUNCATE enrichment_budget')
    lock=tmp_path/'locks';lock.mkdir(mode=0o700)
    resources=[]
    def make(profiles=None,accounts=None,**limits):
        w=EnrichmentWorker(s.pool.conninfo,s.files,{'accounts':accounts or ['telegram-demo'],
            'max_jobs_per_day':20,'profiles':profiles or {'audio':local_profile(tmp_path)},**limits},
            lock_directory=lock)
        old=w.claim
        def claim():
            value=old()
            if value and value[0].get('_resource_lease'):resources.append(value[0]['_resource_lease'])
            return value
        w.claim=claim
        return w
    yield s,make,lock
    for resource in resources:resource.close()


def test_configuration_scoped_limits_are_explicit_and_bounded(tmp_path):
    path=tmp_path/'config.json'
    raw={'enabled':True,'accounts':['fixture'],'max_jobs_per_day':30,
        'profile_max_jobs_per_day':{'audio':20},'account_max_jobs_per_day':{'fixture':10},
        'profiles':{'audio':{'provider':'faster_whisper','model':str(tmp_path)}}}
    path.write_text(json.dumps(raw));path.chmod(0o600)
    assert configuration(path)['profile_max_jobs_per_day']=={'audio':20}
    for field,value in [('profile_max_jobs_per_day',{'unknown':1}),
                        ('account_max_jobs_per_day',{'other':1}),
                        ('profile_max_jobs_per_day',{'audio':True}),
                        ('account_max_jobs_per_day',{'fixture':0})]:
        path.write_text(json.dumps({**raw,field:value}))
        with pytest.raises(ValueError,match='invalid_enrichment_budget'):configuration(path)


def test_lock_failclosed_missing_directory_symlinks_and_public_lock(tmp_path):
    with pytest.raises(FileNotFoundError):admission.lock_file(tmp_path/'missing')
    link=tmp_path/'link';link.symlink_to(tmp_path,target_is_directory=True)
    with pytest.raises(ValueError):admission.lock_file(link)
    file=tmp_path/'local-heavy.lock';file.write_text('');file.chmod(0o666)
    with pytest.raises(ValueError):admission.lock_file(tmp_path)
    file.unlink();file.symlink_to(tmp_path/'missing-file')
    with pytest.raises(OSError):admission.lock_file(tmp_path)


def test_inherited_child_holds_slot_after_parent_descriptor_closes(tmp_path):
    fd=admission.lock_file(tmp_path)
    child=subprocess.Popen([sys.executable,'-c',
        'import sys;print("ready",flush=True);sys.stdin.read()'],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,pass_fds=(fd,))
    try:
        assert child.stdout.readline()==b'ready\n'
        os.close(fd)
        assert admission.lock_file(tmp_path) is None
        child.communicate(timeout=10)
        next_fd=admission.lock_file(tmp_path);assert next_fd is not None;os.close(next_fd)
    finally:
        if child.poll() is None:child.terminate();child.wait(timeout=10)


def test_actual_parent_death_does_not_release_inherited_child_slot(tmp_path):
    root=str(Path(__file__).resolve().parents[1]);pidfile=tmp_path/'child.pid'
    source='''import os,subprocess,sys
sys.path.insert(0,sys.argv[1])
from messaging_service.resource_admission import lock_file
fd=lock_file(sys.argv[2])
p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],pass_fds=(fd,))
open(sys.argv[3],'w').write(str(p.pid))
os._exit(0)
'''
    parent=subprocess.run([sys.executable,'-c',source,root,str(tmp_path),str(pidfile)],timeout=10)
    assert parent.returncode==0
    pid=int(pidfile.read_text())
    try:assert admission.lock_file(tmp_path) is None
    finally:os.kill(pid,15)
    # Ask another bounded process to acquire after the child terminates, without
    # leaving a test-owned sleeper or polling in the parent process.
    check='''import sys,time,os
sys.path.insert(0,sys.argv[1])
from messaging_service.resource_admission import lock_file
for _ in range(100):
 fd=lock_file(sys.argv[2])
 if fd is not None:os.close(fd);sys.exit(0)
 time.sleep(.02)
sys.exit(1)
'''
    assert subprocess.run([sys.executable,'-c',check,root,str(tmp_path)],timeout=5).returncode==0


@pytest.mark.parametrize('header',[b'%PDF-1.7',b'\x89PNG\r\n\x1a\n',b'\xff\xd8\xff'])
def test_declared_text_cannot_bypass_native_admission(tmp_path,header):
    digest=hashlib.sha256(header).hexdigest();(tmp_path/digest).write_bytes(header)
    assert admission.extraction_heavy(tmp_path,{'source_digest':digest,'source_mime':'text/plain','source_filename':'note.txt'})


def test_profile_sublimit_does_not_starve_other_profile(configured,tmp_path):
    s,make,_=configured;add_file(s,'voice');add_file(s,'image',mime='image/png')
    w=make({'audio':cloud_profile(tmp_path,'audio'),'image':cloud_profile(tmp_path)},
        profile_max_jobs_per_day={'audio':1})
    row,_=w.claim();assert row['kind']=='audio';assert w.finish(row,normalize_result({'text':'voice'}))
    add_file(s,'voice-two')
    row,_=w.claim();assert row['kind']=='image';assert w.finish(row,normalize_result({'title':'image'}))
    assert w.claim() is None
    with s.pool.connection() as c:
        assert c.execute('SELECT attempts FROM enrichment_budget WHERE day=current_date').fetchone()['attempts']==2
        assert c.execute("SELECT attempts FROM attachment_enrichment WHERE attachment='voice-two'").fetchone()['attempts']==0


def test_account_sublimit_and_auth_pause_are_independent(configured,tmp_path):
    s,make,_=configured;add_file(s,'tg-image',mime='image/png');add_file(s,'wa-image','demo-wa-1',mime='image/png')
    w=make({'image':cloud_profile(tmp_path)},accounts=['telegram-demo','whatsapp-demo-1'],
        account_max_jobs_per_day={'telegram-demo':1})
    row,_=w.claim();paused=row['account'];assert w.finish(row,error='model_auth_failed')
    add_file(s,'extra','demo-tg-1' if paused=='telegram-demo' else 'demo-wa-1',mime='image/png')
    other,_=w.claim();assert other['account']!=paused
    assert w.finish(other,normalize_result({'title':'ok'}));assert w.claim() is None
    w.publish_profiles()
    with s.pool.connection() as c:
        saved=c.execute('SELECT pause_reason,paused_until FROM processing_profiles WHERE account=%s',(paused,)).fetchone()
        assert saved=={'pause_reason':'model_auth_failed','paused_until':None}
        assert c.execute('SELECT attempts FROM enrichment_budget WHERE day=current_date').fetchone()['attempts']==2


def test_cooldown_persists_and_new_profile_does_not_reset_day_limit(configured,tmp_path):
    s,make,_=configured;add_file(s,'image',mime='image/png')
    w=make({'image':cloud_profile(tmp_path)},profile_max_jobs_per_day={'image':1})
    row,_=w.claim();assert w.finish(row,error='model_rate_limited')
    with s.pool.connection() as c:
        pause=c.execute('SELECT pause_reason,paused_until>clock_timestamp() active FROM processing_control').fetchone()
        assert pause=={'pause_reason':'model_rate_limited','active':True}
    w.config['profiles']['image']['model']='changed-model'
    assert w.claim() is None  # Model change must not evade the profile's daily cap.


def test_shared_local_slot_blocks_other_worker_without_charging_attempt(configured,tmp_path):
    s,make,lock=configured;add_file(s,'voice');add_file(s,'scan',mime='image/png')
    whisper=make();vision=make({'image':local_profile(tmp_path,'image')})
    row,_=whisper.claim()
    assert vision.claim() is None
    indexer=FileIndexer(s.pool.conninfo,s.files,lock_directory=lock)
    # Text remains processable while native work waits for the heavy slot.
    job=indexer.claim();assert job['source_filename']=='probe-note.txt'
    assert indexer.finish(job,Extraction('ready','note','stdlib-text-v1'))=='ready'
    assert indexer.claim() is None
    with s.pool.connection() as c:
        assert c.execute('SELECT attempts FROM enrichment_budget WHERE day=current_date').fetchone()['attempts']==1
        assert c.execute("SELECT attempts FROM attachment_text WHERE attachment='scan'").fetchone()['attempts']==0
    assert whisper.finish(row,normalize_result({'text':'voice'}))
    next_row,_=vision.claim();assert next_row['kind']=='image';assert vision.finish(next_row,normalize_result({'title':'scan'}))


def test_expired_db_lease_is_recoverable_but_live_os_owner_still_fences(configured):
    s,make,_=configured;add_file(s,'voice');w=make();row,_=w.claim()
    with s.pool.connection() as c:
        c.execute("UPDATE attachment_enrichment SET lease_until=clock_timestamp()-interval '1 second'")
        c.execute("UPDATE processing_resource_leases SET lease_until=clock_timestamp()-interval '1 second'")
    assert w.claim() is None
    row['_resource_lease'].close()  # Simulates exit of all surviving native processes.
    replacement,_=w.claim();assert replacement['lease_token']!=row['lease_token']
    assert not w.finish(row,normalize_result({'text':'stale'}))
    assert w.finish(replacement,normalize_result({'text':'current'}))


def test_concurrent_workers_share_global_ceiling_and_scoped_counter(configured,tmp_path):
    s,make,_=configured;add_file(s,'image-one',mime='image/png');add_file(s,'image-two',mime='image/png')
    workers=[make({'image':cloud_profile(tmp_path)},max_jobs_per_day=1) for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:values=list(pool.map(lambda w:w.claim(),workers))
    assert sum(value is not None for value in values)==1
    with s.pool.connection() as c:
        assert c.execute('SELECT sum(attempts) n FROM processing_budget').fetchone()['n']==1
        assert c.execute('SELECT attempts FROM enrichment_budget WHERE day=current_date').fetchone()['attempts']==1


def test_fresh_voice_is_selected_before_old_backlog(configured):
    s,make,_=configured;add_file(s,'old','demo-tg-1');add_file(s,'fresh','demo-tg-2')
    with s.pool.connection() as c:
        c.execute("UPDATE messages SET sent_at=clock_timestamp()-interval '1 year' WHERE id='demo-tg-1'")
        c.execute("UPDATE messages SET sent_at=clock_timestamp() WHERE id='demo-tg-2'")
    w=make();row,_=w.claim();assert row['attachment']=='fresh';assert w.finish(row,normalize_result({'text':'new'}))


def test_worker_passes_inherited_fence_into_isolated_process(tmp_path,monkeypatch):
    import messaging_service.enrichment as module
    data=b'synthetic';digest=hashlib.sha256(data).hexdigest();(tmp_path/digest).write_bytes(data)
    fd=admission.lock_file(tmp_path);lease=admission.ResourceLease(fd,uuid4());captured={}
    class Child:
        pid=123456;returncode=0
        def __init__(self,args,**kwargs):captured.update(kwargs);self.output=kwargs['stdout']
        def communicate(self,*args,**kwargs):self.output.write(b'{"text":"ok"}')
    monkeypatch.setattr(module.subprocess,'Popen',Child)
    monkeypatch.setattr(module.os,'killpg',lambda *_:None)
    try:
        assert module.process(tmp_path,{'source_digest':digest,'bytes':len(data),'kind':'audio',
            'mime':'audio/ogg','_resource_lease':lease},local_profile(tmp_path))['text']=='ok'
        assert captured['pass_fds']==(fd,)
        assert captured['env']['PROCESSING_RESOURCE_FD']==str(fd)
    finally:lease.close()


def test_native_subprocess_receives_inherited_fence(tmp_path,monkeypatch):
    import messaging_service.file_extract as native
    fd=admission.lock_file(tmp_path);captured={}
    monkeypatch.setenv('PROCESSING_RESOURCE_FD',str(fd))
    def run(command,**kwargs):captured.update(kwargs);return subprocess.CompletedProcess(command,0)
    monkeypatch.setattr(native.subprocess,'run',run)
    try:
        native._run_native(['fixture'])
        assert captured['pass_fds']==(fd,)
    finally:os.close(fd)


def test_scoped_profile_pause_survives_worker_restart_and_resumes_explicitly(configured,tmp_path):
    s,make,_=configured;add_file(s,'image',mime='image/png');p=cloud_profile(tmp_path)
    first=make({'image':p});row,_=first.claim();assert first.finish(row,error='model_auth_failed')
    with s.pool.connection() as c:
        c.execute("UPDATE attachment_enrichment SET next_due=clock_timestamp() WHERE attachment='image'")
    restarted=make({'image':p});assert restarted.claim() is None
    with s.pool.connection() as c:
        c.execute('UPDATE processing_control SET pause_reason=NULL,paused_until=NULL')
    current,_=restarted.claim();assert current['kind']=='image'
    assert restarted.finish(current,normalize_result({'title':'ok'}))


def test_processing_role_can_claim_without_archive_write_rights(configured,tmp_path):
    from contextlib import contextmanager
    import messaging_service.enrichment as module
    s,make,_=configured;add_file(s,'voice')
    role='test_processing_enricher_020'
    ddl=Path(module.__file__).with_name('enrichment_role.sql').read_text().replace('messaging_enricher',role)
    with s.pool.connection() as c:c.execute(ddl)
    @contextmanager
    def restricted():
        with s.pool.connection() as c:
            c.execute('SET LOCAL ROLE '+role)
            yield c
    w=make();w.connection=restricted
    try:
        w.publish_profiles();row,_=w.claim();assert w.finish(row,normalize_result({'text':'ok'}))
        with restricted() as c:
            assert not c.execute("SELECT has_table_privilege(current_user,'messages','UPDATE') allowed").fetchone()['allowed']
            assert not c.execute("SELECT has_table_privilege(current_user,'attachments','DELETE') allowed").fetchone()['allowed']
    finally:
        with s.pool.connection() as c:c.execute('DROP OWNED BY '+role);c.execute('DROP ROLE '+role)


def test_failed_claim_commit_closes_fence_without_leaking_descriptor(tmp_path):
    from contextlib import contextmanager
    @contextmanager
    def failure():
        yield object()
        raise RuntimeError('synthetic-commit-failure')
    with pytest.raises(RuntimeError,match='synthetic-commit-failure'):
        with admission.transaction(failure()) as (_,resources):
            resources.append(admission.ResourceLease(admission.lock_file(tmp_path),uuid4()))
    fd=admission.lock_file(tmp_path);assert fd is not None;os.close(fd)


def test_application_ogg_is_claimed_by_the_only_audio_profile(configured):
    s,make,_=configured;add_file(s,'application-ogg',mime='application/ogg')
    w=make()
    row,_=w.claim();assert row['attachment']=='application-ogg' and row['kind']=='audio'
    assert w.finish(row,normalize_result({'text':'synthetic transcript'},audio=True))


def test_application_ogg_never_becomes_a_document_job(configured,tmp_path):
    s,make,_=configured;add_file(s,'application-ogg',mime='application/ogg')
    with s.pool.connection() as c:
        c.execute("UPDATE attachment_text SET state='ready',text_content='synthetic parser output' WHERE attachment='application-ogg'")
    w=make({'document':cloud_profile(tmp_path,'document')})
    assert w.claim() is None
    with s.pool.connection() as c:
        assert c.execute("SELECT attempts FROM attachment_enrichment WHERE attachment='application-ogg'").fetchone()['attempts']==0
