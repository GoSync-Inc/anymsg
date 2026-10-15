"""Immutable original files, bounded streaming and crash-safe publication."""
from dataclasses import dataclass
import hashlib
import os
import re
from pathlib import Path
import secrets
import shutil

from .spool import fsync_dir


@dataclass(frozen=True)
class Blob:
    digest: str
    bytes: int


class BlobWriter:
    def __init__(self,root,*,max_bytes=512*1024**2,reserve_bytes=1024**3):
        self.root=Path(root); self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
        if self.root.is_symlink(): raise ValueError('blob_root_symlink')
        self.max_bytes,self.reserve_bytes=max_bytes,reserve_bytes

    def write(self,chunks,*,expected_bytes=None,expected_sha256=None):
        with BlobSink(self,expected_bytes,expected_sha256) as sink:
            for chunk in chunks: sink.push(chunk)
            return sink.finish()

    async def write_async(self,chunks,*,expected_bytes=None,expected_sha256=None,reservation=None):
        import asyncio
        with BlobSink(self,expected_bytes,expected_sha256,reservation=reservation) as sink:
            async for chunk in chunks:
                sink.push(chunk)
                await asyncio.sleep(0)  # Heartbeats run even with an in-memory provider fixture.
            return sink.finish()

    def verify(self,blob):
        if not re.fullmatch(r'[a-f0-9]{64}',blob.digest) or type(blob.bytes) is not int or blob.bytes<0:
            raise ValueError('invalid_blob')
        path=self.root/blob.digest
        if path.is_symlink() or not path.is_file() or path.stat().st_size!=blob.bytes:
            raise RuntimeError('blob_corrupt')
        with path.open('rb') as file:
            if hashlib.file_digest(file,'sha256').hexdigest()!=blob.digest: raise RuntimeError('blob_corrupt')
        return path


class BlobSink:
    """One bounded chunk at a time; same integrity path for sync and async providers."""
    def __init__(self,writer,expected_bytes,expected_sha256,*,reservation=None):
        if expected_bytes is not None and not 0<=expected_bytes<=writer.max_bytes:
            raise ValueError('media_size_limit')
        self.writer,self.expected_bytes,self.expected_sha256=writer,expected_bytes,expected_sha256
        self.reservation=reservation
        self.temporary=writer.root/('.pending-'+secrets.token_hex(16))
        self.count=0; self.sha=hashlib.sha256(); self.out=None

    def __enter__(self):
        if self.reservation is not None:self.reservation.check(0)
        fd=os.open(self.temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        self.out=os.fdopen(fd,'wb'); return self

    def push(self,chunk):
        if not isinstance(chunk,(bytes,bytearray,memoryview)) or len(chunk)>1024**2:
            raise ValueError('invalid_media_chunk')
        self.count+=len(chunk)
        if self.reservation is not None:self.reservation.check(self.count)
        if self.count>self.writer.max_bytes: raise ValueError('media_size_limit')
        if shutil.disk_usage(self.writer.root).free-len(chunk)<self.writer.reserve_bytes:
            raise RuntimeError('disk_reserve')
        self.out.write(chunk); self.sha.update(chunk)

    def finish(self):
        if self.reservation is not None:self.reservation.check(self.count)
        if self.expected_bytes is not None and self.count!=self.expected_bytes: raise ValueError('media_size_mismatch')
        digest=self.sha.hexdigest()
        if self.expected_sha256 is not None and digest!=self.expected_sha256: raise ValueError('media_hash_mismatch')
        self.out.flush(); os.fsync(self.out.fileno())
        target=self.writer.root/digest
        try: os.link(self.temporary,target)
        except FileExistsError:
            self.writer.verify(Blob(digest,self.count))
        fsync_dir(self.writer.root)
        return Blob(digest,self.count)

    def __exit__(self,*args):
        if self.out: self.out.close()
        self.temporary.unlink(missing_ok=True)
