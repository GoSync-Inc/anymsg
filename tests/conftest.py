import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from messaging_service.store import Store


@pytest.fixture(scope='session')
def database(tmp_path_factory):
    filename=os.environ.get('PROBE_TEST_DSN_FILE')
    if not filename:
        pytest.fail('PROBE_TEST_DSN_FILE is required; PostgreSQL tests must not silently skip')
    dsn=Path(filename).read_text().strip()
    if not dsn.endswith('/messaging_probe_test'):
        pytest.fail('Tests may only reset the isolated messaging_probe_test database')
    store=Store(dsn,tmp_path_factory.mktemp('originals'),'http://testserver')
    store.migrate()
    yield store
    store.close()


@pytest.fixture()
def seeded(database):
    with database.pool.connection() as c:
        c.execute('TRUNCATE accounts,principals,feed_counter,audit_log CASCADE')
        c.execute('INSERT INTO feed_counter(id) VALUES(1)')
    database.seed()
    one=database.issue_key('agent-a',['telegram-demo','whatsapp-demo-1'])
    two=database.issue_key('agent-b',['telegram-demo'])
    return database,one,two
