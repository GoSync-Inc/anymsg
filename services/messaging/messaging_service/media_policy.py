"""Shared original-prefetch and disk-floor configuration for all collectors."""
from .storage_admission import MEDIA_RESERVE_BYTES


MEDIA_POLICY_FIELDS=frozenset({'media_disk_reserve_bytes','media_live_disk_reserve_bytes',
    'media_live_max_bytes','media_live_window_seconds','media_prefetch_days'})
DEFAULT_MEDIA_PREFETCH_DAYS=7


def validate_prefetch_days(value):
    # Explicit null is the compatibility opt-in for unbounded available history.
    if value is not None and (type(value) is not int or not 0<=value<=36500):
        raise ValueError('invalid_media_prefetch_days')
    return value


def configure_media_policy(cfg):
    """Validate after the caller has validated capture/max-file limits."""
    value=cfg.setdefault('media_disk_reserve_bytes',max(MEDIA_RESERVE_BYTES,cfg['disk_reserve_bytes']))
    if type(value) is not int or not cfg['disk_reserve_bytes']<=value<=100*1024**3:
        raise ValueError('invalid_media_disk_reserve_bytes')
    value=cfg.setdefault('media_live_disk_reserve_bytes',None)
    if value is not None and (type(value) is not int or
            not cfg['disk_reserve_bytes']<=value<=cfg['media_disk_reserve_bytes']):
        raise ValueError('invalid_media_live_disk_reserve_bytes')
    for key,default,lo,hi in [
            ('media_live_max_bytes',min(32*1024**2,cfg['max_media_bytes']),1,cfg['max_media_bytes']),
            ('media_live_window_seconds',86400,600,7*86400)]:
        value=cfg.setdefault(key,default)
        if type(value) is not int or not lo<=value<=hi:raise ValueError('invalid_'+key)
    validate_prefetch_days(cfg.setdefault('media_prefetch_days',DEFAULT_MEDIA_PREFETCH_DAYS))
    return cfg
