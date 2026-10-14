"""Bounded-memory synthetic original-file benchmark; no provider access."""
import hashlib
import json
from pathlib import Path
import resource
import sys
import tempfile
import time

from messaging_service.blobs import BlobWriter


for size in (32,256):
    with tempfile.TemporaryDirectory(prefix='messaging-media-') as directory:
        writer=BlobWriter(Path(directory),reserve_bytes=0)
        chunk=b'lighthouse-42---'*(1024*1024//16); sha=hashlib.sha256()
        for _ in range(size): sha.update(chunk)
        start=time.monotonic()
        blob=writer.write((chunk for _ in range(size)),expected_bytes=size*1024**2,expected_sha256=sha.hexdigest())
        writer.verify(blob)
        peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        print(json.dumps({'status':'PASS','synthetic':True,'size_mib':size,'sha256':blob.digest,
            'elapsed_seconds':round(time.monotonic()-start,3),
            'peak_rss_bytes':peak if sys.platform=='darwin' else peak*1024}))
