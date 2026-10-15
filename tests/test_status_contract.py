from datetime import datetime,timedelta,timezone

from messaging_service.status_details import describe, freshness


def test_freshness_uses_scans_not_the_age_of_the_last_message():
    now=datetime.now(timezone.utc)
    assert freshness(now-timedelta(seconds=10),now)['state']=='recent_scan'
    assert freshness(now-timedelta(minutes=6),now)['state']=='scan_stale'
    assert freshness(None,now)['state']=='scan_unknown'
    assert describe('whatsapp_catchup_gap')['next_action']=='inspect_chat_gaps'
    assert describe('version_conflict')['next_action']=='get_context'


def test_status_cache_remains_scoped_and_reports_age(seeded):
    s,key,other=seeded
    first=s.get_sync_status(key,refresh=False)
    again=s.get_sync_status(key,refresh=False)
    assert first['cache']['hit'] is False
    assert again['cache']['hit'] is True
    assert again['cache']['max_age_seconds']==10
    assert {r['account'] for r in s.get_sync_status(other)['sources']}=={'telegram-demo'}
    s.set_access('agent-a',accounts=['telegram-demo'])
    restricted=s.get_sync_status(key,refresh=False)
    assert restricted['cache']['hit'] is False
    assert {r['account'] for r in restricted['sources']}=={'telegram-demo'}
