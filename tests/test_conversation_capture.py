"""Production-shaped Node events must cross both durable capture boundaries."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from messaging_service.collector_state import CaptureSpool, validate_capture


@pytest.mark.parametrize('kind,duration,expected', [('chat', None, 'text'), ('ptt', 12.5, 'voice')])
def test_node_conversation_metadata_survives_live_and_backfill_spools(tmp_path, kind, duration, expected):
    if not shutil.which('node'):
        pytest.skip('Node runtime is required for the provider capture contract')
    module = (Path(__file__).parents[1] / 'whatsapp/capture.mjs').as_uri()
    live_dir = tmp_path / 'live'
    source = f'''
import {{event, DiskCapture}} from {json.dumps(module)};
const message = {{id:{{_serialized:'false_1234567890@c.us_X1',remote:'1234567890@c.us'}},
    type:{json.dumps(kind)},duration:{json.dumps(duration)},timestamp:1700000000,
    from:'1234567890@c.us',fromMe:false,body:'Synthetic conversation',hasMedia:false,_data:{{}}}};
const item = {{kind:'whatsapp_event',account:'synthetic-wa',event:event(message),
    change:'message',origin:'live',observed_at:'2026-10-15T01:00:00Z'}};
new DiskCapture(process.argv[1], {{reserveBytes:0}}).put(item);
process.stdout.write(JSON.stringify(item));
'''
    item = json.loads(subprocess.check_output(['node', '--input-type=module', '-e', source, str(live_dir)]))
    assert item['event']['timestampResolution'] == 'second'
    assert item['event']['durationSeconds'] == duration
    committed = []
    # Actual Node fsync/hash publication -> Python validation/drain.
    assert CaptureSpool(live_dir, reserve_bytes=0).drain(committed.append) == 1
    normalized = committed[0]
    assert normalized['envelope']['metadata']['message_kind'] == expected
    assert normalized['envelope']['metadata']['timestamp_resolution'] == 'second'
    assert normalized['envelope']['metadata']['media_duration_seconds'] == duration
    assert validate_capture(normalized) == normalized
    # History pages enter the Python publication path with the same raw event.
    item['origin'] = 'backfill'
    history = CaptureSpool(tmp_path / 'history', reserve_bytes=0)
    history.put(item)
    replayed = []
    assert history.drain(replayed.append) == 1
    assert replayed[0]['envelope']['metadata'] == normalized['envelope']['metadata']
    assert not list(live_dir.glob('*.json')) and not list(history.path.glob('*.json'))
