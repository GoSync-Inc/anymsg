"""Local owner CLI wiring, with a synthetic store and no provider/database startup."""
import json
import stat
import sys

import pytest

from messaging_service import __main__ as operator
from messaging_service.policy import UNSET


class RecordingStore:
    calls = []
    def __init__(self, *args): pass
    def close(self): self.calls.append(('close',))
    def issue_key(self, *args, **kwargs):
        self.calls.append(('issue_key', args, kwargs)); return 'synthetic-secret-token'
    def set_key_permissions(self, key_id, permissions):
        self.calls.append(('set_key_permissions', key_id, permissions))
        return {'id': key_id, 'permissions': permissions}
    def set_messenger_operations(self, account, operations):
        self.calls.append(('set_messenger_operations', account, operations))


@pytest.fixture
def cli(monkeypatch, tmp_path):
    RecordingStore.calls = []
    monkeypatch.setattr(operator, 'Store', RecordingStore)
    dsn = tmp_path/'synthetic.dsn'
    dsn.write_text('postgresql://synthetic.invalid/messaging_probe_test')
    monkeypatch.setenv('DATABASE_URL_FILE', str(dsn))
    def invoke(*arguments):
        monkeypatch.setattr(sys, 'argv', ['anymsg', *arguments])
        operator.main()
    return invoke, RecordingStore.calls


def test_cli_issues_independently_narrowed_key_and_never_prints_token(cli, tmp_path, capsys):
    invoke, calls = cli
    path = tmp_path/'synthetic.token'
    invoke('issue-key', '--principal', 'agent-synthetic', '--accounts', 'telegram-synthetic',
        '--permissions', 'read', 'groups-manage', '--key-permissions', 'read', '--key-file', str(path))
    assert calls[0][0] == 'issue_key'
    assert calls[0][2]['permissions'] == ['read', 'groups-manage']
    assert calls[0][2]['key_permissions'] == ['read']
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.read_text() == 'synthetic-secret-token\n'
    assert 'synthetic-secret-token' not in capsys.readouterr().out


def test_cli_old_issue_arguments_keep_safe_backend_default(cli, tmp_path):
    invoke, calls = cli
    invoke('issue-key', '--principal', 'agent-synthetic', '--accounts', 'telegram-synthetic',
        '--key-file', str(tmp_path/'synthetic.token'))
    assert calls[0][2]['key_permissions'] is UNSET


def test_cli_can_deny_all_permissions_for_a_single_key(cli, capsys):
    invoke, calls = cli
    key_id = '00000000-0000-4000-8000-000000000001'
    invoke('set-key-permissions', '--key-id', key_id, '--permissions')
    assert calls == [('set_key_permissions', key_id, []), ('close',)]
    assert json.loads(capsys.readouterr().out)['permissions'] == []


@pytest.mark.parametrize('arguments', [
    ['set-key-permissions', '--permissions', 'read'],
    ['set-key-permissions', '--key-id', '00000000-0000-4000-8000-000000000001'],
    ['set-messenger-operations', '--operations', 'chat_info'],
    ['set-messenger-operations', '--account', 'telegram-synthetic'],
])
def test_cli_grant_changes_require_explicit_scope_and_values(cli, arguments):
    invoke, calls = cli
    with pytest.raises(SystemExit) as failure: invoke(*arguments)
    assert failure.value.code == 2
    assert not any(call[0] in ('set_key_permissions', 'set_messenger_operations') for call in calls)


@pytest.mark.parametrize('operations', [[], ['chat_info', 'send_reaction']])
def test_cli_account_operations_are_explicit_opt_in_and_can_be_disabled(cli, capsys, operations):
    invoke, calls = cli
    invoke('set-messenger-operations', '--account', 'telegram-synthetic', '--operations', *operations)
    assert calls == [('set_messenger_operations', 'telegram-synthetic', operations), ('close',)]
    assert json.loads(capsys.readouterr().out)['operations_enabled'] == operations


def test_cli_refuses_unknown_operation_without_granting_it(cli):
    invoke, calls = cli
    with pytest.raises(SystemExit) as failure:
        invoke('set-messenger-operations', '--account', 'telegram-synthetic', '--operations', '__raw_rpc__')
    assert failure.value.code == 2 and not calls
