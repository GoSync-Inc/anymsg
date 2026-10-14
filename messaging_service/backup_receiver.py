"""Small append-only ciphertext dropbox, invoked ONLY by an SSH forced command.

Standalone stdlib file so the destination needs no app credentials or packages.
No path, command, read, list or delete operation is accepted from the sender.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime,timezone,timedelta
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import struct
import sys
import tempfile

BACKUP_ID=re.compile(r'[0-9]{8}T[0-9]{6}Z-[a-f0-9]{16}')
DIGEST=re.compile(r'[a-f0-9]{64}')
AGE_HEADER=b'age-encryption.org/v1\n'


def fsync_dir(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)


def sha_file(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as f:
        st=os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_nlink!=1:raise ValueError('invalid_stored_file')
        return st.st_size,hashlib.file_digest(f,'sha256').hexdigest()


def header_from(source,max_bytes):
    raw=source.readline(4097)
    if len(raw)>4096 or not raw.endswith(b'\n'):raise ValueError('invalid_header')
    try:header=json.loads(raw)
    except (ValueError,UnicodeError):raise ValueError('invalid_header') from None
    if isinstance(header,dict) and type(header.get('v')) is int and header['v']==2:
        if (set(header)!={'v','id','max_bytes','snapshot_sha256'} or
            not isinstance(header['id'],str) or not BACKUP_ID.fullmatch(header['id']) or
            type(header['max_bytes']) is not int or not 64<=header['max_bytes']<=max_bytes or
            not isinstance(header['snapshot_sha256'],str) or not DIGEST.fullmatch(header['snapshot_sha256'])):
            raise ValueError('invalid_header')
        return header
    if (not isinstance(header,dict) or set(header)!={'v','id','bytes','sha256'} or
        type(header['v']) is not int or header['v']!=1 or
        not isinstance(header['id'],str) or not BACKUP_ID.fullmatch(header['id']) or
        type(header['bytes']) is not int or not 64<=header['bytes']<=max_bytes or
        not isinstance(header['sha256'],str) or not DIGEST.fullmatch(header['sha256'])):
        raise ValueError('invalid_header')
    return header


@contextmanager
def locked(directory):
    directory=Path(directory)
    if not directory.is_absolute() or any(p.is_symlink() for p in [directory,*directory.parents]):
        raise ValueError('invalid_receiver_directory')
    if not directory.is_dir() or directory.stat().st_mode&0o077:raise ValueError('private_receiver_directory_required')
    fd=os.open(directory/'.receiver.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('receiver_busy') from None
        yield
    finally:os.close(fd)


def receive(directory,source,*,max_bytes=20*1024**3,quota_bytes=30*1024**3,reserve_bytes=1024**3,max_files=4096,respond=None):
    header=header_from(source,max_bytes);directory=Path(directory)
    if header['v']==2:
        return receive_stream(directory,source,header,quota_bytes=quota_bytes,reserve_bytes=reserve_bytes,
                              max_files=max_files,respond=respond)
    with locked(directory):
        # A killed process may leave only its unpublished temporary file. No other
        # uploader can be alive while this lock is held. Never touch committed data.
        for p in directory.glob('.incoming-*'):
            if p.is_symlink() or not p.is_file():raise ValueError('invalid_stored_file')
            p.unlink()
        target=directory/(header['id']+'.tar.age')
        existing=None
        if target.exists() or target.is_symlink():
            existing=sha_file(target)
            if existing!=(header['bytes'],header['sha256']):raise ValueError('backup_id_conflict')
        total=0;count=0
        for p in directory.glob('*.tar.age'):
            info=p.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise ValueError('invalid_stored_file')
            total+=info.st_size;count+=1
            if count>max_files:raise RuntimeError('receiver_quota')
        if not existing and count>=max_files:raise RuntimeError('receiver_quota')
        if total+(0 if existing else header['bytes'])>quota_bytes:raise RuntimeError('receiver_quota')
        if shutil.disk_usage(directory).free-header['bytes']<reserve_bytes:raise RuntimeError('receiver_disk_reserve')
        fd,temporary_name=tempfile.mkstemp(prefix='.incoming-',dir=directory);temporary=Path(temporary_name)
        try:
            sha=hashlib.sha256();remaining=header['bytes'];prefix=b''
            with os.fdopen(fd,'wb') as out:
                while remaining:
                    data=source.read(min(1024**2,remaining))
                    if not data:raise ValueError('truncated_backup')
                    if len(data)>remaining:raise ValueError('backup_size_mismatch')
                    if len(prefix)<len(AGE_HEADER):prefix+=data[:len(AGE_HEADER)-len(prefix)]
                    sha.update(data);out.write(data);remaining-=len(data)
                if source.read(1):raise ValueError('backup_size_mismatch')
                if prefix!=AGE_HEADER:raise ValueError('encrypted_backup_required')
                if sha.hexdigest()!=header['sha256']:raise ValueError('backup_hash_mismatch')
                out.flush();os.fsync(out.fileno())
            if existing is None:
                # link is atomic and never replaces an existing ID, even if a
                # maintenance process that ignores the lock unexpectedly races us.
                os.link(temporary,target);temporary.unlink();fsync_dir(directory)
            return {'v':1,'status':'stored','id':header['id'],'bytes':header['bytes'],
                    'sha256':header['sha256'],'duplicate':existing is not None,
                    'received_at':datetime.fromtimestamp(target.stat().st_mtime,timezone.utc).isoformat()}
        finally:temporary.unlink(missing_ok=True)


def exact_read(source,count):
    data=bytearray()
    while len(data)<count:
        chunk=source.read(count-len(data))
        if not chunk:raise ValueError('truncated_backup')
        data.extend(chunk)
    return bytes(data)


def receive_stream(directory,source,header,*,quota_bytes,reserve_bytes,max_files,respond=None):
    """Framed ciphertext only; trusted SSH binds the final sender hash to bytes.

    A logical-snapshot digest permits retry after losing the final receipt without
    re-creating random age ciphertext. No ciphertext read/list/delete is exposed.
    Sidecar is committed BEFORE the immutable object: a crash may leave an orphan
    sidecar, never an object lacking its binding. Incomplete payloads are discarded.
    """
    with locked(directory):
        for p in directory.glob('.incoming-*'):
            if p.is_symlink() or not p.is_file():raise ValueError('invalid_stored_file')
            p.unlink()
        target=directory/(header['id']+'.tar.age');binding=directory/(header['id']+'.receipt.json')
        if target.exists() or target.is_symlink():
            size,digest=sha_file(target)
            fd=os.open(binding,os.O_RDONLY|os.O_NOFOLLOW)
            with os.fdopen(fd,'rb') as f:
                st=os.fstat(f.fileno())
                if not stat.S_ISREG(st.st_mode) or st.st_size>4096 or st.st_nlink!=1:raise ValueError('invalid_stored_file')
                saved=json.load(f)
            if (saved.get('snapshot_sha256')!=header['snapshot_sha256'] or
                saved.get('id')!=header['id'] or saved.get('bytes')!=size or saved.get('sha256')!=digest):
                raise ValueError('backup_id_conflict')
            return {**saved,'status':'stored','duplicate':True}
        total=count=0
        for p in directory.glob('*.tar.age'):
            info=p.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise ValueError('invalid_stored_file')
            total+=info.st_size;count+=1
        if count>=max_files or total+header['max_bytes']>quota_bytes:raise RuntimeError('receiver_quota')
        if shutil.disk_usage(directory).free-header['max_bytes']<reserve_bytes:raise RuntimeError('receiver_disk_reserve')
        if respond:respond({'v':2,'status':'ready','id':header['id'],'snapshot_sha256':header['snapshot_sha256']})
        fd,name=tempfile.mkstemp(prefix='.incoming-',dir=directory);temporary=Path(name)
        try:
            sha=hashlib.sha256();size=0;prefix=b''
            with os.fdopen(fd,'wb') as out:
                while True:
                    length=struct.unpack('!I',exact_read(source,4))[0]
                    if length==0:break
                    if length>1024**2:raise ValueError('invalid_backup_frame')
                    if size+length>header['max_bytes']:raise ValueError('backup_size_mismatch')
                    if shutil.disk_usage(directory).free-length<reserve_bytes:raise RuntimeError('receiver_disk_reserve')
                    data=exact_read(source,length);size+=length;sha.update(data);out.write(data)
                    if len(prefix)<len(AGE_HEADER):prefix+=data[:len(AGE_HEADER)-len(prefix)]
                raw=source.readline(4097)
                if len(raw)>4096 or not raw.endswith(b'\n'):raise ValueError('truncated_backup')
                try:footer=json.loads(raw)
                except (ValueError,UnicodeError):raise ValueError('invalid_header') from None
                if (not isinstance(footer,dict) or set(footer)!={'bytes','sha256'} or
                    type(footer['bytes']) is not int or footer['bytes']!=size):raise ValueError('backup_size_mismatch')
                if footer['sha256']!=sha.hexdigest():raise ValueError('backup_hash_mismatch')
                if source.read(1):raise ValueError('backup_size_mismatch')
                if prefix!=AGE_HEADER:raise ValueError('encrypted_backup_required')
                out.flush();os.fsync(out.fileno())
            result={'v':2,'status':'stored','id':header['id'],'bytes':size,'sha256':sha.hexdigest(),
                    'snapshot_sha256':header['snapshot_sha256'],'duplicate':False,
                    'received_at':datetime.now(timezone.utc).isoformat()}
            # Binding creation is private, bounded and replaceable only while no
            # ciphertext with this ID exists. No existing backup is replaced.
            binding_fd,binding_name=tempfile.mkstemp(prefix='.incoming-',dir=directory)
            try:
                with os.fdopen(binding_fd,'w') as out:
                    out.write(json.dumps(result,separators=(',',':'))+'\n');out.flush();os.fsync(out.fileno())
                os.replace(binding_name,binding);fsync_dir(directory)
            finally:Path(binding_name).unlink(missing_ok=True)
            os.link(temporary,target);temporary.unlink();fsync_dir(directory)
            return result
        finally:temporary.unlink(missing_ok=True)


def prune(directory,protected_file,*,now=None,keep_last=24,keep_daily=7,keep_weekly=4,min_age_seconds=86400,max_copies=None):
    """Receiver-host maintenance only; the upload key cannot select this argv.

    Refuse ALL deletion unless a separately verified restore anchor exists and
    its ciphertext still matches. The sender must not be able to edit this file.
    """
    directory=Path(directory);protected_file=Path(protected_file)
    if protected_file.is_symlink():raise ValueError('invalid_protected_backup')
    info=protected_file.stat()
    if info.st_uid!=0 or info.st_mode&0o022 or info.st_size>65536:raise ValueError('invalid_protected_backup')
    protected=json.loads(protected_file.read_text())
    if not isinstance(protected,list) or not protected:raise ValueError('verified_restore_anchor_required')
    if max_copies is not None and (type(max_copies) is not int or not 1<=max_copies<=3):
        raise ValueError('invalid_backup_retention')
    now=now or datetime.now(timezone.utc)
    with locked(directory):
        keep=set()
        for item in protected:
            if (not isinstance(item,dict) or set(item)!={'id','bytes','sha256'} or not BACKUP_ID.fullmatch(item['id']) or
                type(item['bytes']) is not int or not DIGEST.fullmatch(item['sha256'])):raise ValueError('invalid_protected_backup')
            name=item['id']+'.tar.age'
            if sha_file(directory/name)!=(item['bytes'],item['sha256']):raise ValueError('protected_backup_corrupt')
            keep.add(name)
        if max_copies is not None and len(keep)>max_copies:raise ValueError('protected_backups_exceed_retention')
        files=[]
        for p in directory.glob('*.tar.age'):
            info=p.lstat()
            if not BACKUP_ID.fullmatch(p.name[:-8]) or not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
                raise ValueError('invalid_stored_file')
            files.append((p,datetime.fromtimestamp(info.st_mtime,timezone.utc),info.st_size))
        files.sort(key=lambda x:x[1],reverse=True)
        if max_copies is not None:
            for path,_,_ in files:
                if len(keep)<max_copies:keep.add(path.name)
        else:
            keep.update(p.name for p,_,_ in files[:keep_last]);days=set();weeks=set()
            for path,created,size in files:
                if (now-created).total_seconds()<min_age_seconds:keep.add(path.name)
                if created.date()>=(now-timedelta(days=keep_daily-1)).date() and created.date() not in days:
                    keep.add(path.name);days.add(created.date())
                week=created.isocalendar()[:2]
                if created>=now-timedelta(weeks=keep_weekly) and week not in weeks:
                    keep.add(path.name);weeks.add(week)
        removed=[];released=0
        for path,_,size in files:
            if path.name not in keep:
                path.unlink();(directory/(path.name[:-8]+'.receipt.json')).unlink(missing_ok=True)
                removed.append(path.name);released+=size
        fsync_dir(directory)
        return {'status':'pruned','kept':len(files)-len(removed),'removed':len(removed),'released_bytes':released,
                'protected':len(protected)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--max-bytes',type=int,default=20*1024**3)
    parser.add_argument('--quota-bytes',type=int,default=30*1024**3)
    parser.add_argument('--timeout-seconds',type=int,default=900)
    parser.add_argument('--prune',action='store_true')
    parser.add_argument('--protected-file',type=Path)
    parser.add_argument('--max-copies',type=int,help='retain 1..3 copies including all verified anchors')
    args=parser.parse_args();os.umask(0o077)
    def timed_out(*_):raise TimeoutError('receiver_timeout')
    signal.signal(signal.SIGALRM,timed_out);signal.alarm(args.timeout_seconds)
    try:
        # SSH_ORIGINAL_COMMAND is deliberately never parsed or executed.
        if args.prune:
            if os.geteuid()!=0 or not args.protected_file:raise ValueError('operator_retention_required')
            result=prune(args.directory,args.protected_file,max_copies=args.max_copies)
        else:result=receive(args.directory,sys.stdin.buffer,max_bytes=args.max_bytes,quota_bytes=args.quota_bytes,
                           respond=lambda item:print(json.dumps(item,separators=(',',':')),flush=True))
        print(json.dumps(result,separators=(',',':')),flush=True)
    except Exception as exc:
        safe={'invalid_header','receiver_busy','receiver_quota','receiver_disk_reserve','backup_id_conflict',
              'truncated_backup','backup_size_mismatch','backup_hash_mismatch','encrypted_backup_required','receiver_timeout','invalid_backup_frame'}
        print(json.dumps({'v':1,'status':'error','code':str(exc) if str(exc) in safe else 'receiver_failed'}),flush=True)
        raise SystemExit(1) from None
    finally:signal.alarm(0)


if __name__=='__main__':main()
