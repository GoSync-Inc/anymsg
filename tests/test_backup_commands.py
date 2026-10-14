"""Schema25 command inventory and quarantine contract, with synthetic fixtures."""
from contextlib import contextmanager
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb
import pytest

from messaging_service.backup import database_manifest, quarantine_restore


class Cursor:
    def __init__(self, record): self.record = record
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def execute(self, _): pass
    def __iter__(self): return iter([{'record': self.record}])


class Connection:
    def __init__(self, version=25, command=None, database='synthetic_restore_test'):
        self.version, self.command, self.database = version, command, database
        self.queries = []
        self.last_query = ''
    def execute(self, query):
        self.last_query = query
        self.queries.append(query)
        return self
    def fetchone(self):
        return {'name': self.database} if 'current_database' in self.last_query else {'value': str(self.version)}
    def cursor(self, *, name):
        return Cursor(self.command or {} if name == 'backup_messenger_commands' else {})


class Pool:
    def __init__(self, connection): self.c = connection
    @contextmanager
    def connection(self): yield self.c


class Store:
    def __init__(self, connection): self.pool = Pool(connection)


def test_schema25_backup_hashes_exact_command_intent_and_uncertain_result():
    before = database_manifest(Connection(command={'id': 'op_synthetic',
        'payload': {'parameters': {'emoji': '👍'}}, 'result': {'outcome': 'submitted'}}))
    changed_payload = database_manifest(Connection(command={'id': 'op_synthetic',
        'payload': {'parameters': {'emoji': '🎉'}}, 'result': {'outcome': 'submitted'}}))
    changed_result = database_manifest(Connection(command={'id': 'op_synthetic',
        'payload': {'parameters': {'emoji': '👍'}}, 'result': {'outcome': 'confirmed'}}))
    assert len(before) == 46 and before['messenger_commands']['rows'] == 1
    assert before['messenger_commands']['sha256'] != changed_payload['messenger_commands']['sha256']
    assert before['messenger_commands']['sha256'] != changed_result['messenger_commands']['sha256']
    assert before['messages'] == changed_payload['messages'] == changed_result['messages']


@pytest.mark.parametrize('version', [2, 21, 22, 23, 24])
def test_legacy_backups_do_not_query_a_nonexistent_command_table(version):
    assert 'messenger_commands' not in database_manifest(Connection(version=version))


def test_schema25_quarantine_disables_every_operation_and_preserves_uncertain_effects():
    connection = Connection()
    quarantine_restore(Store(connection))
    statements = '\n'.join(connection.queries)
    assert "UPDATE account_policies SET operations_enabled='{}'" in statements
    assert 'UPDATE messenger_commands SET status=CASE' in statements
    assert "WHEN status='queued' THEN 'cancelled' ELSE 'unknown'" in statements
    assert "WHERE status IN ('queued','dispatching')" in statements
    assert 'UPDATE api_keys SET revoked=true' in statements
    assert 'UPDATE outgoing_messages' in statements


@pytest.mark.parametrize('version', [21, 22, 24])
def test_legacy_quarantine_keeps_existing_columns_and_never_queries_new_ones(version):
    connection = Connection(version=version)
    quarantine_restore(Store(connection))
    statements = '\n'.join(connection.queries)
    assert 'operations_enabled' not in statements and 'messenger_commands' not in statements
    assert ('UPDATE outgoing_messages' in statements) is (version >= 22)
    assert 'UPDATE api_keys SET revoked=true' in statements


def test_quarantine_refuses_active_database_before_any_update():
    connection = Connection(database='messaging_probe_test')
    with pytest.raises(ValueError, match='not_restore_database'):
        quarantine_restore(Store(connection))
    assert not any(query.startswith('UPDATE') for query in connection.queries)


def test_native_schema25_quarantine_changes_only_pending_commands(database, tmp_path):
    """Exercise actual PostgreSQL state in a uniquely owned disposable restore DB."""
    from messaging_service.store import Store as NativeStore
    base_dsn = database.pool.conninfo
    restore_name = 'anymsg_'+uuid4().hex+'_restore_test'
    restored = None
    created = False
    try:
        with psycopg.connect(base_dsn, autocommit=True) as owner:
            owner.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(restore_name)))
        created = True
        restored = NativeStore(make_conninfo(base_dsn, dbname=restore_name),
            tmp_path/'synthetic-originals', 'http://testserver')
        restored.migrate()
        restored.seed()
        token = restored.issue_key('synthetic-restore-agent', ['telegram-demo'])
        key_id = restored.capabilities(token)['credential_id']
        with restored.pool.connection() as connection:
            connection.execute("""INSERT INTO account_policies(account,enabled,send_enabled,operations_enabled)
                SELECT id,true,true,ARRAY['mark_read'] FROM accounts""")
            for index, status in enumerate(('queued', 'dispatching', 'succeeded', 'unknown')):
                connection.execute('''INSERT INTO messenger_commands(principal,key_id,scope_version,
                    key_permission_version,request_id,request_hash,account,operation,permission,
                    mutation,payload,status,result)
                    VALUES(%s,%s,1,1,%s,%s,'telegram-demo','mark_read','read-state',true,%s,%s,%s)''',
                    ('synthetic-restore-agent', key_id, 'synthetic-restore-'+str(index), 'a'*64,
                     Jsonb({'synthetic': True}), status,
                     Jsonb({'outcome': 'confirmed'}) if status == 'succeeded' else None))
        quarantine_restore(restored)
        with restored.pool.connection() as connection:
            policies = connection.execute('SELECT enabled,send_enabled,operations_enabled FROM account_policies').fetchall()
            commands = connection.execute('SELECT status,error_code,result FROM messenger_commands ORDER BY request_id').fetchall()
            keys = connection.execute('SELECT revoked FROM api_keys').fetchall()
        assert policies and all(not row['enabled'] and not row['send_enabled'] and row['operations_enabled'] == [] for row in policies)
        assert [row['status'] for row in commands] == ['cancelled', 'unknown', 'succeeded', 'unknown']
        assert [row['error_code'] for row in commands] == ['restored_disabled', 'restored_disabled', None, None]
        assert commands[2]['result'] == {'outcome': 'confirmed'}
        assert keys and all(row['revoked'] for row in keys)
    finally:
        if restored is not None: restored.close()
        if created:
            with psycopg.connect(base_dsn, autocommit=True) as owner:
                owner.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(restore_name)))
