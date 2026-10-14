"""Bounded fsync-backed inbox. Failed DB commits leave the original envelope queued."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import time

from .envelope import canonical, validate


def fsync_dir(path):
    fd=os.open(path,os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


class DurableSpool:
    def __init__(self,path,*,max_bytes=256*1024**2,max_entries=10000,reserve_bytes=1024**3,validator=validate):
        self.path=Path(path); self.path.mkdir(mode=0o700,parents=True,exist_ok=True)
        if self.path.is_symlink(): raise ValueError('spool_symlink')
        self.max_bytes,self.max_entries,self.reserve_bytes=max_bytes,max_entries,reserve_bytes
        self.validator=validator

    @contextmanager
    def locked(self,name='queue.lock'):
        fd=os.open(self.path/name,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX); yield
        finally: os.close(fd)

    def put(self,data):
        payload=canonical(self.validator(data)); digest=hashlib.sha256(payload).hexdigest()
        with self.locked():
            pending=list(self.path.glob('*.json'))
            existing=next((p for p in pending if p.name.endswith('-'+digest+'.json')),None)
            if existing: return existing.name
            # Include abandoned temp files in quota; never silently discard corrupt/unknown data.
            occupied=sum(p.stat().st_size for p in self.path.iterdir() if p.is_file())
            if len(pending)>=self.max_entries or occupied+len(payload)>self.max_bytes:
                raise RuntimeError('spool_full')
            if shutil.disk_usage(self.path).free-len(payload)<self.reserve_bytes:
                raise RuntimeError('disk_reserve')
            target=self.path/f'{time.time_ns():020d}-{digest}.json'
            temporary=self.path/('.pending-'+secrets.token_hex(12))
            try:
                fd=os.open(temporary,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
                with os.fdopen(fd,'wb') as out:
                    out.write(payload); out.flush(); os.fsync(out.fileno())
                os.replace(temporary,target); fsync_dir(self.path)
            finally:
                temporary.unlink(missing_ok=True)
            return target.name

    def drain(self,ingest,limit=100):
        count=0
        # A stalled database must not block durable capture. Serialize drainers
        # independently, holding the publication lock only for filesystem work.
        with self.locked('drain.lock'):
            for path in sorted(self.path.glob('*.json'))[:limit]:
                if path.is_symlink(): raise RuntimeError('spool_corrupt')
                payload=path.read_bytes()
                if hashlib.sha256(payload).hexdigest()!=path.stem.split('-')[-1]:
                    raise RuntimeError('spool_corrupt')
                ingest(self.validator(json.loads(payload)))  # Return only after DB commit.
                with self.locked():
                    path.unlink(); fsync_dir(self.path); count+=1
        return count
