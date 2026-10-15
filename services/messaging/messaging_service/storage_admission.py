"""Conservative media capacity leases, independent of durable message capture.

All collectors sharing an archive DB serialize admission. A filesystem device
key groups bind mounts/path aliases. Reserved bytes include the entire payload
even after some bytes have been written: this deliberately underestimates spare
capacity rather than promising an exact post-transfer free-space value.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil


STREAM_WORKSPACE_BYTES = 1024**2
MEDIA_RESERVE_BYTES = 10*1024**3


def volume_key(root):
    return 'fs:'+str(os.stat(Path(root),follow_symlinks=False).st_dev)


def payload_bound(expected_bytes, max_bytes):
    if expected_bytes is None:
        return max_bytes
    if type(expected_bytes) is not int or not 0<=expected_bytes<=max_bytes:
        raise ValueError('media_size_limit')
    return expected_bytes


def has_capacity(free_bytes, reserved_bytes, requested_bytes, reserve_bytes):
    return free_bytes-reserved_bytes-requested_bytes>=reserve_bytes


@dataclass
class MediaReservation:
    """Local streaming guard for a DB reservation, renewed with the media lease."""
    payload_bytes: int
    expires_at: datetime

    def check(self, count):
        if datetime.now(timezone.utc)>=self.expires_at:
            raise RuntimeError('media_lease_lost')
        if count>self.payload_bytes:
            raise ValueError('media_size_limit')


class StorageAdmission:
    def __init__(self, writer):
        self.writer=writer
        self.volume=volume_key(writer.root)

    @staticmethod
    def lock(c):
        c.execute("SELECT pg_advisory_xact_lock(hashtextextended('media-storage-admission',0))")

    @staticmethod
    def lock_and_reap(c):
        StorageAdmission.lock(c)
        # Supersede/delete invalidates the job lease. A crashed writer's expired
        # reservation is reclaimed on the next admission, without a scheduler.
        c.execute('''DELETE FROM media_reservations r WHERE r.expires_at<=clock_timestamp()
            OR NOT EXISTS(SELECT 1 FROM media_jobs j WHERE j.id=r.job AND j.state='leased'
                AND j.lease_epoch=r.lease_epoch AND j.lease_until>clock_timestamp())''')

    def reserve(self, c, job, expires_at):
        payload=payload_bound(job['descriptor']['bytes'],self.writer.max_bytes)
        requested=payload+STREAM_WORKSPACE_BYTES
        reserved=c.execute('''SELECT coalesce(sum(reserved_bytes),0) AS bytes
            FROM media_reservations WHERE volume_key=%s''',(self.volume,)).fetchone()['bytes']
        free=shutil.disk_usage(self.writer.root).free
        if not has_capacity(free,reserved,requested,self.writer.reserve_bytes):
            return None
        c.execute('''INSERT INTO media_reservations(job,volume_key,lease_epoch,reserved_bytes,expires_at)
            VALUES(%s,%s,%s,%s,%s)''',
            (job['id'],self.volume,job['lease_epoch']+1,requested,expires_at))
        return MediaReservation(payload,expires_at)

    @staticmethod
    def release(c, job):
        # An old worker must never release the replacement worker's capacity.
        c.execute('DELETE FROM media_reservations WHERE job=%s AND lease_epoch=%s',
            (job['id'],job['lease_epoch']))
