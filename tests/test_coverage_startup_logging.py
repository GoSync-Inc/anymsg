"""Fresh-process checks for diagnostics emitted during client construction.

Database and telemetry clients are synthetic; no socket or provider session opens.
Running in a new interpreter prevents another test's global log factory from
concealing unsafe startup order.
"""
import os
from pathlib import Path
import subprocess
import sys
import textwrap


ROOT = Path(__file__).resolve().parents[1]

LOG_PROBE = r'''
import logging
records = []
class Recorder(logging.Handler):
    def emit(self, record):
        records.append(record)
logging.getLogger().addHandler(Recorder())
def library_diagnostic(namespace):
    try:
        raise ValueError('SYNTHETIC_STARTUP_EXCEPTION')
    except ValueError:
        logging.getLogger(namespace).error('SYNTHETIC_STARTUP_BODY %r',
            {'token': 'SYNTHETIC_STARTUP_ARGUMENT'}, exc_info=True, stack_info=True)
def assert_safe_records(count):
    assert len(records) == count
    for record in records:
        assert record.name.startswith('personal_os_messaging.library.')
        assert record.getMessage() == 'library_event exception_type=ValueError'
        assert record.args == ('ValueError',)
        assert record.exc_info is record.exc_text is record.stack_info is None
        assert 'SYNTHETIC_STARTUP' not in logging.Formatter().format(record)
'''


def run_fresh(script):
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith('ANYMSG_TIDEN_')}
    completed = subprocess.run([sys.executable, '-c', LOG_PROBE + textwrap.dedent(script)],
        cwd=ROOT, env=environment, capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ''
    assert 'SYNTHETIC_STARTUP' not in completed.stderr


def test_database_pool_startup_diagnostics_are_redacted_before_client_construction():
    run_fresh('''
        from pathlib import Path
        from messaging_service import store
        class Pool:
            def __init__(self, *args, **kwargs):
                library_diagnostic('psycopg.pool.startup.new_client')
            def wait(self, **kwargs):
                library_diagnostic('psycopg.pool.startup.wait')
            def close(self):
                pass
        store.ConnectionPool = Pool
        instance = store.Store('synthetic', Path('.'), 'http://synthetic.invalid')
        instance.close()
        assert_safe_records(2)
    ''')


def test_telemetry_startup_diagnostics_are_redacted_before_sdk_construction(tmp_path):
    dsn = tmp_path / 'synthetic.dsn'
    dsn.write_text('https://synthetic@telemetry.invalid/1')
    run_fresh(f'''
        import os
        import sys
        from types import SimpleNamespace
        from messaging_service import telemetry
        class Client:
            def __init__(self, *args, **kwargs):
                library_diagnostic('httpx.telemetry.startup.new_client')
                library_diagnostic('httpcore.telemetry.startup.connection')
            def capture_event(self, event):
                raise AssertionError('this check does not deliver diagnostics')
        sys.modules['tiden_telemetry'] = SimpleNamespace(Client=Client)
        os.environ['ANYMSG_TIDEN_ENABLED'] = '1'
        os.environ['ANYMSG_TIDEN_DSN_FILE'] = {str(dsn)!r}
        os.environ['ANYMSG_TIDEN_ENVIRONMENT'] = 'synthetic'
        try:
            assert telemetry.configure('gateway') is True
            assert_safe_records(2)
        finally:
            if telemetry._reporter is not None:
                telemetry._reporter.close()
    ''')
