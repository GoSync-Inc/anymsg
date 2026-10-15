"""Offline WhatsApp contract boundaries; no provider/session is activated."""
import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
import signal
from types import SimpleNamespace

import pytest

from messaging_service import whatsapp_collector as wa
from messaging_service.whatsapp_collector import NodeProvider


@pytest.fixture
def private_wa_config(tmp_path):
    config = tmp_path / 'config.json'
    permit = tmp_path / 'permit.json'
    session = tmp_path / 'session'
    session.mkdir(mode=0o700)
    cfg = {'enabled': True, 'account': 'wa-contract', 'expected_wid': '123456789@c.us',
        'permit_file': str(permit), 'session_directory': str(session),
        'events_directory': str(tmp_path / 'events'), 'pages_directory': str(tmp_path / 'pages'),
        'chromium_executable': '/usr/bin/chromium', 'node_executable': '/usr/bin/node'}
    permit.write_text(json.dumps({'account': cfg['account'], 'expected_wid': cfg['expected_wid'],
        'prior_clients_stopped': True}))
    permit.chmod(0o600)
    def save(changes=None):
        config.write_text(json.dumps({**cfg, **(changes or {})}))
        config.chmod(0o600)
        return config
    return cfg, permit, save


@pytest.mark.parametrize('field,value', [
    ('account', 'bad account'), ('expected_wid', '123@lid'),
    ('session_directory', 'relative'), ('node_executable', 'node'),
    ('batch_size', 0), ('batch_size', 101), ('batch_size', True),
    ('scan_interval_seconds', 9), ('scan_interval_seconds', 3601),
    ('max_dialogs', 0), ('max_dialogs', 10001),
    ('max_media_bytes', 0), ('max_media_bytes', 4 * 1024**3 + 1),
    ('disk_reserve_bytes', 1024**3 - 1), ('disk_reserve_bytes', 100 * 1024**3 + 1),
    ('media_disk_reserve_bytes', 1024**3 - 1), ('unknown_provider_option', True),
])
def test_private_configuration_rejects_invalid_identity_paths_and_resource_bounds(private_wa_config, field, value):
    _, _, save = private_wa_config
    with pytest.raises((ValueError, RuntimeError)):
        wa.configuration(save({field: value}))


@pytest.mark.parametrize('changes', [
    {'account': 'another-account'}, {'expected_wid': '999999999@c.us'},
    {'prior_clients_stopped': False}, {'prior_clients_stopped': 1},
])
def test_handover_permit_must_bind_exact_account_identity_and_explicit_stop(private_wa_config, changes):
    cfg, permit, save = private_wa_config
    permit.write_text(json.dumps({'account': cfg['account'], 'expected_wid': cfg['expected_wid'],
        'prior_clients_stopped': True, **changes}))
    with pytest.raises(RuntimeError, match='session_handover_not_confirmed'):
        wa.configuration(save())


@pytest.mark.parametrize('unsafe', ['public', 'symlink'])
def test_collector_refuses_nonprivate_or_symlink_session_before_database_access(private_wa_config, tmp_path, unsafe, monkeypatch):
    cfg, _, save = private_wa_config
    session = Path(cfg['session_directory'])
    if unsafe == 'public':
        session.chmod(0o755)
    else:
        session.rmdir()
        target = tmp_path / 'other'
        target.mkdir(mode=0o700)
        session.symlink_to(target, target_is_directory=True)
    monkeypatch.setattr(wa, 'Store', lambda *_: pytest.fail('database accessed before private-session check'))
    with pytest.raises(RuntimeError, match='private_session_required'):
        asyncio.run(wa.run_config(save()))


def test_private_policy_refresh_keeps_send_disabled_and_operations_empty_by_default(tmp_path):
    async def run():
        p = NodeProvider({'events_directory': str(tmp_path)}, tmp_path / 'unused', 3)
        await p.set_policy({'discover_chats': True, 'include_chats': [], 'exclude_chats': ['999@lid']})
        file = tmp_path / '.policy'
        saved = json.loads(file.read_text())
        assert file.stat().st_mode & 0o777 == 0o600
        assert saved['policy'] == {'discover_chats': True, 'include_chats': [],
            'exclude_chats': ['999@lid'], 'enabled': True, 'send_enabled': False, 'operations_enabled': []}
        import time
        assert 43 < saved['expires_at'] - time.time() <= 45
        assert not list(tmp_path.glob('.policy-*'))
    asyncio.run(run())


def test_private_calls_enforce_send_gate_preflight_payload_allowlist_and_timeouts(tmp_path):
    async def run():
        p = NodeProvider({}, tmp_path / 'unused', 3)
        events = []
        async def request(method, params, *, sending=False):
            events.append(('request', method, params, sending))
            return 1
        async def reply(request_id, timeout=90, *, sending=False):
            events.append(('reply', timeout, sending))
            return {'id': request_id, 'result': {'accepted': True}}
        async def preflight(): events.append(('preflight',))
        p.request, p.reply = request, reply
        for method in ['send_text', 'messenger_operation']:
            with pytest.raises(RuntimeError, match='send_gate_required'):
                await p.call(method, {})
        with pytest.raises(RuntimeError, match='send_gate_required'):
            await p.execute_operation({'operation': 'chat_info'})
        await p.call('start', {})
        await p.call('page', {})
        assert [e[1] for e in events if e[0] == 'reply'] == [300, 90]
        events.clear()
        async with p.send_gate:
            await p.execute_operation({'operation': 'chat_info', 'parameters': {},
                'provider_chat': '123@c.us', 'expires_at': 1800000000, '_preflight': preflight,
                'credential_id': 'never-forward', 'token': 'private-token'})
        assert events[0] == ('preflight',)
        assert events[1] == ('request', 'messenger_operation', {'operation': 'chat_info',
            'parameters': {}, 'provider_chat': '123@c.us', 'expires_at': 1800000000}, True)
        assert events[2] == ('reply', 90, True)
    asyncio.run(run())


def test_connecting_while_existing_provider_is_alive_is_rejected_before_spawn(tmp_path, monkeypatch):
    async def run():
        p = NodeProvider({}, tmp_path / 'unused', 3)
        p.process = SimpleNamespace(returncode=None)
        async def forbidden(*_, **__): pytest.fail('second provider process was started')
        monkeypatch.setattr(wa.asyncio, 'create_subprocess_exec', forbidden)
        with pytest.raises(RuntimeError, match='provider_already_connected'): await p.connect()
    asyncio.run(run())


@pytest.mark.parametrize('kind,expected', [
    ('partial', 'whatsapp_history_partial'), ('limited', 'dialog_discovery_limited'),
    ('missing', 'configured_chat_unavailable'), ('unavailable', 'whatsapp_chat_unavailable'),
    ('gap', 'whatsapp_catchup_gap'),
])
def test_history_pass_status_names_distinguish_gaps_missing_chats_and_discovery_bounds(kind, expected):
    async def run():
        phases, statuses, inventories, failures = [], [], [], []
        selected = {'discover_chats': True, 'include_chats': ['999@c.us'] if kind == 'missing' else [], 'exclude_chats': []}
        state = SimpleNamespace(
            phase=lambda *args: phases.append(args), policy=lambda: selected,
            inventory=lambda dialogs, _: inventories.extend(dialogs), scan_order=lambda dialogs: dialogs,
            allows=lambda *_: True, failed=lambda *args: failures.append(args),
            status=lambda *args: statuses.append(args))
        class Provider:
            async def set_policy(self, value): assert value == selected
            async def dialogs(self, limit):
                assert limit == (1 if kind == 'limited' else 2000)
                return [('123@c.us', 'user', 'Synthetic')]
        worker = wa.WhatsAppCollector.__new__(wa.WhatsAppCollector)
        worker.state, worker.provider = state, Provider()
        worker.max_dialogs = 1 if kind == 'limited' else 2000
        async def sync_chat(*_):
            assert inventories  # Catalog comes before message/history work.
            if kind == 'unavailable': raise wa.ProviderOperationError('chat_unavailable')
            return kind == 'gap'
        worker.sync_chat = sync_chat
        await worker.sync_once()
        assert statuses == [('degraded' if kind in ('gap', 'missing', 'unavailable') else 'receiving', expected)]
        assert phases == [('history', 'scanning', 'history_scan'), ('history', 'waiting', 'history_scan_finished')]
        assert failures == ([('123@c.us', 'provider_unavailable')] if kind == 'unavailable' else [])
    asyncio.run(run())


def test_event_and_page_spools_are_drained_together_before_next_poll(monkeypatch):
    from messaging_service import outgoing
    async def run():
        worker = wa.WhatsAppCollector.__new__(wa.WhatsAppCollector)
        worker.events_spool, worker.spool = object(), object()
        actions = []
        receipt=object()
        class ReceiptSpool:
            def drain(self,apply,limit):
                actions.append(('receipt_spool',limit))
                apply(receipt)
        worker.send_receipts=ReceiptSpool()
        monkeypatch.setattr(outgoing,'reconcile_send_receipt',
            lambda state,item:actions.append(('receipt',state,item)))
        async def drain(spools): actions.append(('drain', spools))
        worker.drain_captures = drain
        worker.state = SimpleNamespace(apply_tombstones=lambda: actions.append(('tombstones',)))
        async def stop_after_cycle(seconds):
            assert seconds == .25
            raise RuntimeError('end synthetic cycle')
        monkeypatch.setattr(wa.asyncio, 'sleep', stop_after_cycle)
        with pytest.raises(RuntimeError, match='end synthetic cycle'): await worker.drain_loop()
        assert actions == [('receipt_spool',100),('receipt',worker.state,receipt),
            ('drain', [worker.events_spool, worker.spool]), ('tombstones',)]
    asyncio.run(run())


@pytest.mark.parametrize('outcome,records', [
    ('excluded', False), ('conflict', False), ('stale', False), ('tombstoned', False),
    ('deleted', False), ('duplicate', True), ('inserted', True),
])
def test_identity_observation_write_is_gated_by_actual_ingest_outcome(monkeypatch, outcome, records):
    from messaging_service.collector_state import CollectorState
    from messaging_service import whatsapp_identity
    from test_whatsapp_identity import observed
    calls = []
    state = CollectorState.__new__(CollectorState)
    state.account, state.lease = 'wa-identities', ('fixture', 1)
    state.archive = SimpleNamespace(ingest=lambda *_, **__: {'outcome': outcome})
    state.policy = lambda: {'discover_chats': True, 'include_chats': [], 'exclude_chats': []}
    state.chat = lambda *_: None
    monkeypatch.setattr(whatsapp_identity, 'record', lambda *args: calls.append(args))
    assert state.apply(observed()) == {'outcome': outcome}
    assert bool(calls) is records
    if records:
        assert calls[0][2][0]['provenance'] == 'webjs_contact_cache_v1'


@pytest.mark.parametrize('response,exception', [
    ({'id': 2, 'result': {}}, 'provider_protocol_error'),
    ({'id': 1, 'extra': True, 'result': {}}, 'provider_protocol_error'),
])
def test_desynchronized_private_responses_disconnect_before_reuse(tmp_path, response, exception):
    async def run():
        p = NodeProvider({}, tmp_path / 'unused', 3)
        stopped = []
        async def request(*_, **__): return 1
        async def disconnect(): stopped.append(True)
        p.process = SimpleNamespace(stdout=asyncio.StreamReader())
        p.process.stdout.feed_data(json.dumps(response).encode() + b'\n')
        p.request, p.disconnect = request, disconnect
        with pytest.raises(RuntimeError, match=exception): await p.call('page', {})
        assert stopped == [True]
    asyncio.run(run())


@pytest.mark.parametrize('error,expected', [
    ('private diagnostic with token', 'provider_operation_failed'),
    ('send_expired', 'send_expired'), ('operation_disabled', 'operation_disabled'),
    ('media_unavailable', 'media_unavailable'), ('provider_page_stalled', 'provider_page_stalled'),
])
def test_provider_error_frames_map_to_safe_codes(tmp_path, error, expected):
    async def run():
        p = NodeProvider({}, tmp_path / 'unused', 3)
        p.process = SimpleNamespace(stdout=asyncio.StreamReader())
        p.process.stdout.feed_data(json.dumps({'id': 1, 'error': error}).encode() + b'\n')
        with pytest.raises(Exception) as rejected: await p.reply(1)
        assert str(rejected.value) == expected
    asyncio.run(run())


def test_disconnect_escalates_only_owned_process_group_after_bounded_wait(tmp_path, monkeypatch):
    async def run():
        events = []
        class Process:
            returncode = None
            pid = 99999999
            def terminate(self): events.append('term-child')
            async def wait(self):
                events.append('wait')
                self.returncode = -9
        p = NodeProvider({}, tmp_path / 'unused', 3)
        p.process = Process()
        async def bounded_wait(coro, timeout):
            assert timeout == 12
            coro.close()
            raise TimeoutError
        monkeypatch.setattr(wa.asyncio, 'wait_for', bounded_wait)
        monkeypatch.setattr(wa.os, 'killpg', lambda pid, sig: events.append((pid, sig)))
        await p.disconnect()
        assert events == ['term-child', (p.process.pid, signal.SIGKILL), 'wait', (p.process.pid, signal.SIGTERM)]
    asyncio.run(run())


@pytest.mark.parametrize('outcome', ['success', 'heartbeat', 'timeout', 'synthetic_account'])
def test_owner_pairing_enforces_registration_deadline_and_releases_lease(private_wa_config, tmp_path, monkeypatch, capsys, outcome):
    """Exercise the real owner command orchestration with fake DB/process boundaries."""
    from messaging_service import whatsapp_pair as pair
    _, _, save = private_wa_config
    events = []
    heartbeat = {}
    class Cursor:
        def execute(self, *_): return self
        def fetchone(self): return {'platform': 'whatsapp', 'synthetic': outcome == 'synthetic_account'}
    class Pool:
        @contextmanager
        def connection(self): yield Cursor()
    class Store:
        pool = Pool()
        def __init__(self, *_): pass
        def close(self): events.append('store-close')
    class Archive:
        def __init__(self, *_): pass
        def acquire(self, account, owner): events.append(('acquire', account)); return 7
        def renew(self, account, owner, epoch):
            events.append(('renew', account, epoch))
            if outcome == 'heartbeat': heartbeat['loop'].call_soon_threadsafe(heartbeat['seen'].set)
        def release(self, account, owner, epoch): events.append(('release', account, epoch))
    class Process:
        pid = 99999999
        returncode = None
        def terminate(self): events.append('terminate'); self.returncode = -15
        async def wait(self):
            if outcome == 'heartbeat': await heartbeat['seen'].wait()
            self.returncode = 0
            return 0
    async def start(*args, **kwargs):
        assert kwargs['start_new_session'] is True
        assert kwargs['stdin'] == asyncio.subprocess.DEVNULL
        assert kwargs['stdout'] == asyncio.subprocess.DEVNULL
        assert len(kwargs['pass_fds']) == 1
        assert 'stderr' not in kwargs  # QR stays only in the owner's terminal.
        events.append('child-start')
        return Process()
    real_wait = asyncio.wait
    async def wait(tasks, *, timeout, return_when):
        assert timeout == 300
        if outcome == 'timeout': return set(), set(tasks)
        return await real_wait(tasks, timeout=timeout, return_when=return_when)
    dsn = tmp_path / 'dsn'
    dsn.write_text('postgresql://synthetic.invalid/messaging_probe_test')
    monkeypatch.setenv('DATABASE_URL_FILE', str(dsn))
    monkeypatch.setenv('PROBE_FILES', str(tmp_path / 'files'))
    monkeypatch.setattr(pair, 'require_owner_terminal', lambda: events.append('owner-tty'))
    monkeypatch.setattr(pair, 'Store', Store)
    monkeypatch.setattr(pair, 'Archive', Archive)
    monkeypatch.setattr(pair.asyncio, 'create_subprocess_exec', start)
    monkeypatch.setattr(pair.asyncio, 'wait', wait)
    monkeypatch.setattr(pair.os, 'killpg', lambda *_: events.append('group-stop'))
    async def run():
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(loop, 'add_signal_handler', lambda *_: None)
        if outcome == 'heartbeat':
            heartbeat.update(loop=loop, seen=asyncio.Event())
            real_sleep = asyncio.sleep
            async def fast_heartbeat(seconds):
                assert seconds == 15
                events.append(('heartbeat-sleep', seconds))
                await real_sleep(0)
            monkeypatch.setattr(pair.asyncio, 'sleep', fast_heartbeat)
        await pair.pair_config(save())
    if outcome in ('success', 'heartbeat'):
        asyncio.run(run())
        assert json.loads(capsys.readouterr().out) == {'status': 'paired', 'collector_started': False}
        assert ('renew', 'wa-contract', 7) in events
        if outcome == 'heartbeat': assert ('heartbeat-sleep', 15) in events
    else:
        expected = 'pairing_timeout' if outcome == 'timeout' else 'live_account_required'
        with pytest.raises(RuntimeError, match=expected): asyncio.run(run())
    assert events[0] == 'owner-tty'
    assert events[-1] == 'store-close'
    if outcome == 'synthetic_account': assert 'child-start' not in events
    else: assert ('release', 'wa-contract', 7) in events


def test_identity_conflict_survives_deletion_of_conflicting_message(seeded):
    from messaging_service.archive import Archive
    from messaging_service.whatsapp_collector import WhatsAppState
    from test_whatsapp_identity import observed
    s, *_ = seeded
    archive = Archive(s)
    archive.register('wa-identities', 'whatsapp', 'Synthetic identities', discover=True)
    owner = 'coverage-whatsapp'
    epoch = archive.acquire('wa-identities', owner)
    state = WhatsAppState(archive, 'wa-identities', (owner, epoch))
    key = s.issue_key('coverage-wa-reader', ['wa-identities'])
    state.apply(observed())
    conflicting = observed(mid=2, pn='1111111111@c.us')
    state.apply(conflicting)
    deletion = {**conflicting, 'change': 'delete', 'observed_at': '2026-10-14T02:00:00Z'}
    state.apply(deletion)
    # A fresh visible observation cannot rehabilitate the old conflicting pair.
    state.apply(observed(mid=3))
    assert s.search_people(key, '1234567890')['items'] == []
    identity = s.search_people(key, 'Alice')['items'][0]['identity']
    assert identity['state'] == 'conflict' and identity['pn'] is None and identity['lid'] is None


def test_excluded_and_tombstoned_messages_cannot_record_identity_observations(seeded):
    from messaging_service.archive import Archive
    from messaging_service.whatsapp_collector import WhatsAppState
    from test_whatsapp_identity import observed
    s, *_ = seeded
    archive = Archive(s)
    archive.register('wa-identities', 'whatsapp', 'Synthetic identities', discover=True,
        exclude_chats=['901@lid'])
    owner = 'coverage-wa-reader'
    state = WhatsAppState(archive, 'wa-identities', (owner, archive.acquire('wa-identities', owner)))
    excluded = observed(chat='901@lid', sender='901@lid')
    assert state.apply(excluded)['outcome'] == 'excluded'
    tombstone = {'kind': 'whatsapp_delete', 'account': 'wa-identities', 'chat': '900@lid',
        'ids': [observed()['event']['messageId']], 'observed_at': '2026-10-14T02:00:00Z'}
    state.apply(tombstone)
    assert state.apply(observed())['outcome'] == 'deleted'
    with s.pool.connection() as c:
        assert c.execute('SELECT count(*) AS n FROM whatsapp_identity_observations').fetchone()['n'] == 0
