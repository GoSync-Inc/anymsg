"""Operator-only cold snapshots of sessions/settings/spools, never live activation.

The caller stops its collectors first. The same session flocks and an expired
database lease are required throughout the snapshot. Arbitrary outside clients
that ignore the ownership protocol are not made safe by this module.
"""
from contextlib import ExitStack,contextmanager
import hashlib
import fcntl
import os
from pathlib import Path,PurePosixPath
import re
import shutil
import stat

from .spool import fsync_dir

ALIAS=re.compile(r'[a-z][a-z0-9_-]{0,63}')
DIGEST=re.compile(r'[a-f0-9]{64}')
MAX_FILES=100000
MAX_BYTES=20*1024**3


def read_manifest(directory):
    import json
    fd=os.open(Path(directory)/'manifest.json',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as f:
        info=os.fstat(f.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size>64*1024**2:
            raise ValueError('invalid_backup_manifest')
        return json.load(f)


def verify_account_coverage(c,accounts):
    known={r['id'] for r in c.execute('SELECT id FROM accounts WHERE NOT synthetic').fetchall()}
    if known!=set(accounts): raise ValueError('recovery_account_coverage')
    if c.execute('SELECT 1 FROM collector_leases WHERE account=ANY(%s) AND expires_at>now() LIMIT 1',
                 (sorted(accounts),)).fetchone():
        raise RuntimeError('recovery_collector_active')


def relative_name(value):
    if (not isinstance(value,str) or not value or len(value)>4096 or '\\' in value or
        any(ord(c)<32 or ord(c)==127 for c in value)):
        raise ValueError('invalid_recovery_path')
    path=PurePosixPath(value)
    if path.is_absolute() or any(p in ('','.','..') for p in value.split('/')):
        raise ValueError('invalid_recovery_path')
    return value


def private_path(value):
    path=Path(value)
    if not path.is_absolute() or '..' in path.parts: raise ValueError('recovery_absolute_path_required')
    if any(p.is_symlink() for p in [path,*path.parents]): raise ValueError('recovery_symlink')
    if not path.exists(): raise ValueError('recovery_source_missing')
    return path


@contextmanager
def cold_session_lock(session):
    # Read-only backup mounts must not create a root-owned lock that would later
    # prevent the unprivileged collector from opening its own session.
    fd=os.open(str(session)+'.owner.lock',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_mode&0o077:raise ValueError('invalid_session_lock')
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('session_already_owned') from None
        yield
    finally:os.close(fd)


@contextmanager
def recovery_guard(store,config,destination):
    """Only an operator config can name sources. No command or network endpoint."""
    if not isinstance(config,dict) or set(config)!={'accounts','sources'}:
        raise ValueError('invalid_recovery_config')
    if not isinstance(config['accounts'],list) or not isinstance(config['sources'],list):
        raise ValueError('invalid_recovery_config')
    accounts={}; sources={}; destination=Path(destination).absolute()
    for item in config['accounts']:
        if not isinstance(item,dict) or set(item)!={'account','session_path'}:
            raise ValueError('invalid_recovery_account')
        account=item['account']
        if not isinstance(account,str) or not account or account in accounts:
            raise ValueError('invalid_recovery_account')
        accounts[account]=private_path(item['session_path'])
    for item in config['sources']:
        if (not isinstance(item,dict) or set(item)!={'name','kind','path','account'} or
            not isinstance(item['name'],str) or not ALIAS.fullmatch(item['name']) or item['name'] in sources or
            item['kind'] not in ('session','spool','settings') or
            (item['kind']=='settings' and item['account'] is not None) or
            (item['kind']!='settings' and item['account'] not in accounts)):
            raise ValueError('invalid_recovery_source')
        path=private_path(item['path'])
        if path==destination or path in destination.parents or destination in path.parents:
            raise ValueError('recovery_destination_overlap')
        for prior in sources.values():
            if path==prior['path'] or path in prior['path'].parents or prior['path'] in path.parents:
                raise ValueError('recovery_source_overlap')
        sources[item['name']]={**item,'path':path}
    if not any(s['kind']=='settings' for s in sources.values()): raise ValueError('recovery_settings_required')
    for account,session in accounts.items():
        roots=[s['path'] for s in sources.values() if s['account']==account and s['kind']=='session']
        if len(roots)!=1 or not (session==roots[0] or roots[0] in session.parents):
            raise ValueError('recovery_session_scope')
        if not any(s['account']==account and s['kind']=='spool' for s in sources.values()):
            raise ValueError('recovery_spool_required')
    with ExitStack() as stack:
        for account in sorted(accounts): stack.enter_context(cold_session_lock(accounts[account]))
        with store.pool.connection() as c:
            verify_account_coverage(c,accounts)
        yield sources


def snapshot_sources(sources,destination,max_bytes=MAX_BYTES,max_files=MAX_FILES):
    """Copy through no-follow descriptors, verify stable stats, and hash all bytes."""
    destination=Path(destination); destination.mkdir(mode=0o700)
    entries=[]; total=0
    def record(source,name,fd):
        nonlocal total
        relative_name(name)
        if len(entries)>=max_files: raise ValueError('recovery_file_limit')
        before=os.fstat(fd)
        if stat.S_ISDIR(before.st_mode):
            entries.append({'source':source,'path':name,'type':'directory'})
            names=sorted(os.listdir(fd))
            # Chromium uses these three symlinks for process-local ownership,
            # not authentication. A cold browser profile must not restore them.
            # Never follow a target; every other special entry still fails closed.
            browser_profile=False
            if sources[source]['kind']=='session':
                try:
                    browser_profile=(stat.S_ISDIR(os.stat('Default',dir_fd=fd,follow_symlinks=False).st_mode)
                        and stat.S_ISREG(os.stat('Local State',dir_fd=fd,follow_symlinks=False).st_mode))
                except FileNotFoundError:pass
            for child in names:
                info=os.stat(child,dir_fd=fd,follow_symlinks=False)
                if (browser_profile and child in {'SingletonLock','SingletonSocket','SingletonCookie'}
                        and stat.S_ISLNK(info.st_mode)):
                    continue
                if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                    raise ValueError('recovery_special_file')
                flags=os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK
                if stat.S_ISDIR(info.st_mode): flags|=os.O_DIRECTORY
                child_fd=os.open(child,flags,dir_fd=fd)
                try: record(source,name+'/'+child,child_fd)
                finally: os.close(child_fd)
        elif stat.S_ISREG(before.st_mode) and before.st_nlink==1:
            if before.st_size>max_bytes-total: raise ValueError('recovery_size_limit')
            if shutil.disk_usage(destination).free-before.st_size<1024**3:
                raise RuntimeError('backup_disk_reserve')
            temporary=destination/('.copy-'+str(len(entries)))
            sha=hashlib.sha256(); size=0
            out_fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(out_fd,'wb') as out:
                while chunk:=os.read(fd,1024**2):
                    size+=len(chunk)
                    if size>before.st_size or total+size>max_bytes: raise ValueError('recovery_source_changed')
                    out.write(chunk);sha.update(chunk)
                out.flush();os.fsync(out.fileno())
            if size!=before.st_size: raise ValueError('recovery_source_changed')
            digest=sha.hexdigest(); target=destination/digest
            try: os.link(temporary,target)
            except FileExistsError:
                if target.stat().st_size!=size: raise RuntimeError('recovery_digest_conflict')
            temporary.unlink()
            entries.append({'source':source,'path':name,'type':'file','bytes':size,'sha256':digest})
            total+=size
        else: raise ValueError('recovery_special_file')
        after=os.fstat(fd)
        if (before.st_ino,before.st_dev,before.st_size,before.st_mtime_ns,before.st_ctime_ns,before.st_nlink)!=(
            after.st_ino,after.st_dev,after.st_size,after.st_mtime_ns,after.st_ctime_ns,after.st_nlink):
            raise ValueError('recovery_source_changed')
    for name,item in sorted(sources.items()):
        # Root is called "root"; absolute source paths never enter the manifest.
        fd=os.open(item['path'],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        try: record(name,'root',fd)
        finally:os.close(fd)
    fsync_dir(destination)
    return {'sources':[{k:v for k,v in item.items() if k!='path'} for item in sources.values()],
            'entries':entries,'logical_bytes':total,'quiesced':True,
            'restore_policy':'quarantine_only_no_automatic_sessions_or_permits'}


def verify_recovery(directory,manifest):
    directory=Path(directory); recovery=manifest.get('recovery')
    if manifest.get('format')==1:
        if recovery is not None or (directory/'recovery').exists(): raise ValueError('unexpected_recovery_state')
        return {'sources':0,'files':0}
    if (manifest.get('format')!=2 or not isinstance(recovery,dict) or
        set(recovery)!={'sources','entries','logical_bytes','quiesced','restore_policy'} or
        recovery['quiesced'] is not True or recovery['restore_policy']!='quarantine_only_no_automatic_sessions_or_permits'):
        raise ValueError('invalid_recovery_manifest')
    sources={}
    if not isinstance(recovery['sources'],list) or len(recovery['sources'])>1000:
        raise ValueError('invalid_recovery_manifest')
    for s in recovery['sources']:
        if (not isinstance(s,dict) or set(s)!={'name','kind','account'} or not isinstance(s['name'],str) or
            not ALIAS.fullmatch(s['name']) or s['name'] in sources or s['kind'] not in ('session','spool','settings') or
            (s['kind']=='settings' and s['account'] is not None) or
            (s['kind']!='settings' and (not isinstance(s['account'],str) or not s['account']))):
            raise ValueError('invalid_recovery_manifest')
        sources[s['name']]=s
    seen={}; digests=set();total=0;files=0
    if not isinstance(recovery['entries'],list) or len(recovery['entries'])>MAX_FILES:
        raise ValueError('invalid_recovery_manifest')
    for entry in recovery['entries']:
        if not isinstance(entry,dict): raise ValueError('invalid_recovery_manifest')
        name=relative_name(entry.get('path')); key=(entry.get('source'),name)
        if key[0] not in sources or key in seen or name.split('/')[0]!='root':
            raise ValueError('invalid_recovery_manifest')
        kind=entry.get('type');seen[key]=kind
        if kind=='directory' and set(entry)=={'source','path','type'}: continue
        if kind!='file' or set(entry)!={'source','path','type','bytes','sha256'}:
            raise ValueError('invalid_recovery_manifest')
        if type(entry['bytes']) is not int or entry['bytes']<0 or not isinstance(entry['sha256'],str) or not DIGEST.fullmatch(entry['sha256']):
            raise ValueError('invalid_recovery_manifest')
        total+=entry['bytes'];files+=1
        if total>MAX_BYTES: raise ValueError('recovery_size_limit')
        path=directory/'recovery'/entry['sha256']
        if path.is_symlink() or not path.is_file() or path.stat().st_size!=entry['bytes']:
            raise RuntimeError('recovery_file_corrupt')
        if entry['sha256'] not in digests:
            with path.open('rb') as f:
                if hashlib.file_digest(f,'sha256').hexdigest()!=entry['sha256']: raise RuntimeError('recovery_file_corrupt')
            digests.add(entry['sha256'])
    for source,name in seen:
        for parent in PurePosixPath(name).parents:
            if str(parent)!='.' and seen.get((source,str(parent)))!='directory':
                raise ValueError('invalid_recovery_manifest')
    if any((s,'root') not in seen for s in sources) or total!=recovery['logical_bytes']:
        raise ValueError('invalid_recovery_manifest')
    root=directory/'recovery'
    if root.is_symlink() or not root.is_dir() or {p.name for p in root.iterdir()}!=digests:
        raise ValueError('recovery_file_set_mismatch')
    return {'sources':len(sources),'files':files}


def materialize_recovery(directory,destination):
    """Extract ONLY to a new private quarantine directory, never original paths."""
    import tempfile
    directory=Path(directory); destination=Path(destination)
    manifest=read_manifest(directory);verify_recovery(directory,manifest)
    if manifest.get('format')!=2: raise ValueError('recovery_state_missing')
    if destination.exists(): raise ValueError('recovery_destination_exists')
    destination.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
    temporary=Path(tempfile.mkdtemp(prefix='.recovery-',dir=destination.parent))
    try:
        for entry in sorted(manifest['recovery']['entries'],key=lambda e:(e['path'].count('/'),e['source'],e['path'])):
            target=temporary/entry['source']/entry['path'];target.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
            if entry['type']=='directory':target.mkdir(mode=0o700,exist_ok=True)
            else:
                fd=os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
                with (directory/'recovery'/entry['sha256']).open('rb') as src,os.fdopen(fd,'wb') as out:
                    shutil.copyfileobj(src,out,1024**2);out.flush();os.fsync(out.fileno())
        for path,dirs,files in os.walk(temporary,topdown=False):fsync_dir(Path(path))
        os.rename(temporary,destination);fsync_dir(destination.parent)
        return {'status':'quarantined','sources':len(manifest['recovery']['sources']),'activated':False}
    finally:
        if temporary.exists():shutil.rmtree(temporary)
