"""Actual native durable capture -> unchanged Python consumer integrity contract."""
import os
from pathlib import Path
import shutil
import subprocess

from messaging_service.collector_state import CaptureSpool


def test_actual_go_capture_export_drains_through_python_spool(tmp_path):
    go = shutil.which('go')
    assert go, 'Go is required for actual capture publisher/Python consumer integration'
    root = Path(__file__).resolve().parents[1]
    export = tmp_path / 'native-export'
    result = subprocess.run(
        [go, 'test', '-count=1', '-run', '^TestCaptureSpoolExportForPython$', '.'],
        cwd=root / 'whatsapp' / 'whatsmeow',
        env={**os.environ, 'ANYMSG_CAPTURE_EXPORT_DIRECTORY': str(export)},
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    queue = CaptureSpool(export / 'events', reserve_bytes=0)
    messages = []
    assert queue.drain(messages.append) == 4
    assert all(item['kind'] == 'message' for item in messages)
    envelopes = [item['envelope'] for item in messages]
    assert [item['origin'] for item in envelopes] == ['live', 'backfill', 'live', 'live']
    assert [item['direction'] for item in envelopes] == ['incoming', 'incoming', 'outgoing', 'incoming']
    assert [item['provider_id'].rsplit('_', 1)[1] for item in envelopes] == [
        'incoming-live', 'incoming-history', 'outgoing', 'replayed',
    ]
    assert list((export / 'events').glob('*.json')) == []
