"""Owner CLI contracts with synthetic collaborators and one isolated DB policy test."""
import json
from contextlib import contextmanager
from pathlib import Path
import sys

import pytest

from anymsg import __version__
from anymsg import cli as facade
from messaging_service import __main__ as operator


@pytest.mark.parametrize('arguments', [
    ['serve'], ['migrate'], ['set-access', '--principal', 'synthetic'],
    ['revoke-key', '--key-id', 'synthetic-key'], ['unrecognized-command'],
])
def test_public_facade_delegates_operator_arguments_and_restores_argv(monkeypatch, arguments):
    before = sys.argv
    seen = []
    def invoke():
        seen.append(sys.argv.copy())
        return 17
    monkeypatch.setattr(operator, 'main', invoke)
    assert facade.main(arguments) == 17
    assert seen == [['anymsg', *arguments]]
    assert sys.argv is before


def test_public_facade_restores_argv_when_operator_rejects_command(monkeypatch):
    before = sys.argv
    def reject():
        assert sys.argv == ['anymsg', 'unrecognized-command']
        raise SystemExit(2)
    monkeypatch.setattr(operator, 'main', reject)
    with pytest.raises(SystemExit) as result:
        facade.main(['unrecognized-command'])
    assert result.value.code == 2
    assert sys.argv is before


def test_public_facade_version_and_asset_export_do_not_delegate(monkeypatch, tmp_path, capsys):
    def forbidden():
        raise AssertionError('facade command must not start an operator/database')
    monkeypatch.setattr(operator, 'main', forbidden)
    assert facade.main(['--version']) == 0
    assert capsys.readouterr().out.strip() == 'anymsg ' + __version__
    destination = tmp_path / 'synthetic-node-sources'
    assert facade.main(['whatsapp-assets', '--directory', str(destination)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['installed_dependencies'] is False
    assert {'provider.mjs', 'pair.mjs', 'package-lock.json'} <= set(result['files'])
    assert not (destination / 'node_modules').exists()


@pytest.mark.parametrize('overrides,expected', [
    ({}, {'public_url': 'http://127.0.0.1:18940', 'files': './probe-files',
          'host': '127.0.0.1', 'port': 18940,
          'hosts': ['127.0.0.1:18940', 'localhost:18940']}),
    ({'PROBE_PUBLIC_URL': 'https://synthetic.example', 'PROBE_FILES': '/synthetic/originals',
      'PROBE_BIND': '127.0.0.2', 'PROBE_PORT': '18941',
      'PROBE_ALLOWED_HOSTS': 'synthetic.example,127.0.0.2:18941'},
     {'public_url': 'https://synthetic.example', 'files': '/synthetic/originals',
      'host': '127.0.0.2', 'port': 18941,
      'hosts': ['synthetic.example', '127.0.0.2:18941']}),
])
def test_serve_reads_private_dsn_and_keeps_network_trust_bounded(monkeypatch, tmp_path, overrides, expected):
    import uvicorn
    from messaging_service import app, telemetry
    calls = []
    dsn = tmp_path / 'synthetic.dsn'
    dsn.write_text('postgresql://synthetic.invalid/messaging_probe_test\n')
    monkeypatch.setenv('DATABASE_URL_FILE', str(dsn))
    for name in ['PROBE_PUBLIC_URL', 'PROBE_FILES', 'PROBE_BIND', 'PROBE_PORT', 'PROBE_ALLOWED_HOSTS']:
        monkeypatch.delenv(name, raising=False)
    for name, value in overrides.items():
        monkeypatch.setenv(name, value)
    class Store:
        def __init__(self, url, files, public_url):
            calls.append(('store', url, files, public_url))
        def close(self):
            calls.append(('close',))
    def application(store, hosts):
        calls.append(('app', isinstance(store, Store), hosts))
        return 'synthetic-asgi'
    monkeypatch.setattr(operator, 'Store', Store)
    monkeypatch.setattr(app, 'create_app', application)
    monkeypatch.setattr(telemetry, 'configure', lambda component: calls.append(('telemetry', component)))
    monkeypatch.setattr(uvicorn, 'run', lambda application, **kwargs: calls.append(('run', application, kwargs)))
    monkeypatch.setattr(sys, 'argv', ['anymsg', 'serve'])
    operator.main()
    assert calls == [
        ('telemetry', 'gateway'),
        ('store', 'postgresql://synthetic.invalid/messaging_probe_test', Path(expected['files']), expected['public_url']),
        ('app', True, expected['hosts']),
        ('run', 'synthetic-asgi', {'host': expected['host'], 'port': expected['port'],
         'access_log': False, 'proxy_headers': False, 'log_level': 'warning', 'limit_concurrency': 32}),
        ('close',),
    ]


def test_operator_administration_is_absent_from_both_remote_catalogs():
    from starlette.testclient import TestClient
    from messaging_service.app import create_app
    class Store:
        public_url = 'http://testserver'
        def authenticate(self, token):
            assert token == 'synthetic'
            return 'synthetic-agent'
        def audit_boundary(self, code):
            assert code == 'not_found'
    administrative = {'init', 'migrate', 'issue_key', 'revoke_key', 'set_access', 'list_keys',
                      'audit_tail', 'register_account', 'replay', 'snapshot', 'verify_restore',
                      'quarantine_restore', 'set_sending', 'set_key_permissions', 'set_messenger_operations'}
    with TestClient(create_app(Store(), ['testserver'])) as client:
        headers = {'Authorization': 'Bearer synthetic', 'Accept': 'application/json, text/event-stream'}
        result = client.post('/mcp', headers=headers,
            json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
        assert result.status_code == 200
        assert administrative.isdisjoint(tool['name'] for tool in result.json()['result']['tools'])
        for name in administrative:
            result = client.post('/v1/' + name, headers=headers, json={})
            assert result.status_code == 404
            assert result.json()['error'] == 'not_found'


@pytest.mark.parametrize('command', ['init', 'migrate'])
def test_local_schema_commands_initialize_only_on_explicit_init(monkeypatch, tmp_path, capsys, command):
    calls = []
    class Connection:
        def execute(self, sql):
            assert sql == "SELECT value FROM probe_metadata WHERE name='schema_version'"
            calls.append(('schema-version',))
            return self
        def fetchone(self):
            return {'value': '27'}
    class Pool:
        @contextmanager
        def connection(self):
            yield Connection()
    class Store:
        pool = Pool()
        def __init__(self, *args):
            pass
        def migrate(self):
            calls.append(('migrate',))
        def seed(self):
            calls.append(('seed',))
        def close(self):
            calls.append(('close',))
    dsn = tmp_path / 'synthetic.dsn'
    dsn.write_text('postgresql://synthetic.invalid/messaging_probe_test')
    monkeypatch.setenv('DATABASE_URL_FILE', str(dsn))
    monkeypatch.setattr(operator, 'Store', Store)
    monkeypatch.setattr(sys, 'argv', ['anymsg', command])
    operator.main()
    if command == 'init':
        assert calls == [('migrate',), ('seed',), ('close',)]
        assert json.loads(capsys.readouterr().out) == {'initialized': True, 'synthetic': True}
    else:
        assert calls == [('migrate',), ('schema-version',), ('close',)]
        assert json.loads(capsys.readouterr().out) == {'schema_version': 27}


@pytest.mark.parametrize('arguments', [
    ['set-sending', '--enable'], ['set-sending', '--account', 'synthetic'],
    ['set-sending', '--account', 'synthetic', '--enable', '--disable'],
])
def test_sending_policy_cli_requires_account_and_exactly_one_state(monkeypatch, tmp_path, arguments):
    from messaging_service import archive
    calls = []
    class Store:
        def __init__(self, *args):
            pass
        def close(self):
            calls.append(('close',))
    monkeypatch.setattr(operator, 'Store', Store)
    monkeypatch.setattr(archive.Archive, 'set_sending', lambda *args: calls.append(('policy',)))
    dsn = tmp_path / 'synthetic.dsn'
    dsn.write_text('postgresql://synthetic.invalid/messaging_probe_test')
    monkeypatch.setenv('DATABASE_URL_FILE', str(dsn))
    monkeypatch.setattr(sys, 'argv', ['anymsg', *arguments])
    with pytest.raises(SystemExit) as result:
        operator.main()
    assert result.value.code == 2
    assert not any(call[0] == 'policy' for call in calls)


@pytest.mark.parametrize('enabled', [True, False])
def test_sending_policy_cli_prints_exact_account_and_state(monkeypatch, tmp_path, capsys, enabled):
    from messaging_service import archive
    calls = []
    class Store:
        def __init__(self, *args):
            pass
        def close(self):
            calls.append(('close',))
    monkeypatch.setattr(operator, 'Store', Store)
    monkeypatch.setattr(archive.Archive, 'set_sending', lambda self, account, state: calls.append((account, state)))
    dsn = tmp_path / 'synthetic.dsn'
    dsn.write_text('postgresql://synthetic.invalid/messaging_probe_test')
    monkeypatch.setenv('DATABASE_URL_FILE', str(dsn))
    monkeypatch.setattr(sys, 'argv', ['anymsg', 'set-sending', '--account', 'synthetic', '--enable' if enabled else '--disable'])
    operator.main()
    assert calls == [('synthetic', enabled), ('close',)]
    assert json.loads(capsys.readouterr().out) == {'account': 'synthetic', 'send_enabled': enabled}


def test_owner_send_policy_changes_only_send_flag_with_transactional_audit(seeded):
    from messaging_service.archive import Archive
    store, _, _ = seeded
    archive = Archive(store)
    account = 'coverage-policy-synthetic'
    archive.register(account, 'telegram', 'Synthetic policy fixture', include_chats=['100'])
    with store.pool.connection() as c:
        before = c.execute('SELECT * FROM account_policies WHERE account=%s', (account,)).fetchone()
        audit_before = c.execute("SELECT count(*) AS n FROM audit_log WHERE operation='set_sending'").fetchone()['n']
    archive.set_sending(account, True)
    with store.pool.connection() as c:
        after = c.execute('SELECT * FROM account_policies WHERE account=%s', (account,)).fetchone()
        audit_after = c.execute("SELECT * FROM audit_log WHERE operation='set_sending' ORDER BY id DESC LIMIT 1").fetchone()
        assert c.execute("SELECT count(*) AS n FROM audit_log WHERE operation='set_sending'").fetchone()['n'] == audit_before + 1
    assert after == {**before, 'send_enabled': True}
    assert audit_after['outcome'] == 'ok'
    with pytest.raises(ValueError, match='unknown_account'):
        archive.set_sending('unknown-synthetic-account', True)
    with store.pool.connection() as c:
        assert c.execute("SELECT count(*) AS n FROM audit_log WHERE operation='set_sending'").fetchone()['n'] == audit_before + 1
        assert c.execute('SELECT * FROM account_policies WHERE account=%s', (account,)).fetchone() == after
