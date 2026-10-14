"""Opt-in Tiden diagnostics. Only an allowlisted code location leaves the host."""
from __future__ import annotations

import atexit
from collections import OrderedDict
import logging
import math
import os
from pathlib import Path
import queue
import re
import sys
import threading
import time

from . import __version__

COMPONENTS = {'gateway', 'telegram', 'whatsapp', 'indexer', 'enricher', 'subscriptions'}
CODES = {'request_failed', 'tool_failed', 'collector_failed', 'indexer_failed',
         'enrichment_failed', 'subscription_match_failed', 'uncaught_exception', 'setup_check'}
ROOT = Path(__file__).resolve().parent
SOURCE_FILES = frozenset(p.name for p in ROOT.glob('*.py'))
EXCEPTION_TYPES = {'RuntimeError', 'ValueError', 'TypeError', 'KeyError', 'AttributeError',
    'OSError', 'TimeoutError', 'ConnectionError', 'PermissionError', 'FileNotFoundError',
    'OperationalError', 'InterfaceError', 'PoolTimeout', 'IntegrityError', 'ProgrammingError',
    'AssertionError', 'MemoryError', 'RecursionError', 'OverflowError', 'IndexError'}
_reporter = None
log = logging.getLogger('anymsg.telemetry')


def safe_event(error, component, code):
    """Never stringify exceptions, inspect locals, read source lines or retain a traceback."""
    if component not in COMPONENTS or code not in CODES:
        return None
    frames = []
    tb = error.__traceback__ if isinstance(error, BaseException) else None
    # Bound traversal even if a pathological exception has an enormous stack.
    for _ in range(128):
        if tb is None:
            break
        filename = Path(tb.tb_frame.f_code.co_filename)
        if filename.parent == ROOT and filename.name in SOURCE_FILES:
            frames.append({'filename': 'messaging_service/' + filename.name,
                           'lineno': tb.tb_lineno, 'in_app': True})
        tb = tb.tb_next
    kind = type(error).__name__
    if kind not in EXCEPTION_TYPES:
        kind = 'UnexpectedError'
    return {'platform': 'python', 'level': 'error', 'release': 'anymsg@' + __version__,
            'tags': {'component': component, 'error_code': code},
            'exception': {'values': [{'type': kind, 'value': code,
                                     'stacktrace': {'frames': frames[-16:]}}]}}


def before_send(event):
    """Defense in depth: discard all SDK/request/user/breadcrumb extras."""
    try:
        tags = event['tags']
        component, code = tags['component'], tags['error_code']
        clean = safe_event(None, component, code)
        if clean is None or event.get('environment') not in {'production', 'staging', 'synthetic'}:
            return None
        value = event['exception']['values'][0]
        clean['exception']['values'][0]['type'] = value['type'] if value['type'] in EXCEPTION_TYPES else 'UnexpectedError'
        frames = []
        for frame in value.get('stacktrace', {}).get('frames', [])[-16:]:
            filename, lineno = frame.get('filename'), frame.get('lineno')
            if filename not in {'messaging_service/' + f for f in SOURCE_FILES}:
                continue
            if type(lineno) is int and 0 < lineno <= 100000:
                frames.append({'filename': filename, 'lineno': lineno, 'in_app': True})
        clean['exception']['values'][0]['stacktrace']['frames'] = frames
        event_id, timestamp = event.get('event_id', ''), event.get('timestamp')
        if not isinstance(event_id, str) or not re.fullmatch('[0-9a-f]{32}', event_id):
            return None
        if type(timestamp) not in (int, float) or not math.isfinite(timestamp):
            return None
        clean.update(event_id=event_id, timestamp=timestamp, environment=event['environment'],
                     sdk={'name': 'tiden.python', 'version': '0.1.0'})
        return clean
    except Exception:
        return None


class Reporter:
    """A lossy, bounded diagnostic queue; archive operations never wait on Tiden."""
    def __init__(self, client, component, environment='production'):
        if component not in COMPONENTS or environment not in {'production', 'staging', 'synthetic'}:
            raise ValueError('invalid_telemetry_configuration')
        self.client, self.component, self.environment = client, component, environment
        self.queue = queue.Queue(maxsize=32)
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.seen = OrderedDict()
        self.window, self.count = time.monotonic(), 0
        self.thread = threading.Thread(target=self._run, name='anymsg-telemetry', daemon=True)
        self.thread.start()

    def capture(self, error, code):
        event = safe_event(error, self.component, code)
        if event is None:
            return False
        event['environment'] = self.environment
        last = event['exception']['values'][0]
        key = (code, last['type'], tuple((f['filename'], f['lineno']) for f in last['stacktrace']['frames']))
        with self.lock:
            if self.stop.is_set():
                return False
            now = time.monotonic()
            if now - self.window >= 60:
                self.window, self.count = now, 0
            if self.count >= 20 or now - self.seen.get(key, -60) < 60:
                return False
            try:
                self.queue.put_nowait(event)
            except queue.Full:
                return False
            self.count += 1
            self.seen[key] = now
            if len(self.seen) > 128:
                self.seen.popitem(last=False)
        # Accepted locally is not a delivery receipt. The SDK swallows HTTP failures.
        return True

    def _run(self):
        while not self.stop.is_set() or not self.queue.empty():
            try:
                event = self.queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                self.client.capture_event(event)
            except Exception:
                pass  # No recursive telemetry or network error payload in local logs.
            finally:
                self.queue.task_done()

    def close(self):
        with self.lock:
            if self.stop.is_set():
                return
            self.stop.set()
        self.thread.join(timeout=1.5)


def capture(error, code):
    try:
        return bool(_reporter and _reporter.capture(error, code))
    except Exception:
        return False


def configure(component):
    """No SDK import, thread, hook or network without explicit opt-in and a DSN file."""
    global _reporter
    if _reporter is not None:
        return True
    if os.environ.get('ANYMSG_TIDEN_ENABLED') != '1':
        return False
    try:
        from tiden_telemetry import Client
        dsn = Path(os.environ['ANYMSG_TIDEN_DSN_FILE']).read_text().strip()
        from urllib.parse import urlsplit
        url = urlsplit(dsn)
        if url.scheme != 'https' or not url.username or url.password or not url.hostname:
            raise ValueError('invalid_telemetry_dsn')
        client = Client(dsn, send_default_pii=False, before_send=before_send,
                        http_timeout=1.0, install_excepthook=False)
        reporter = Reporter(client, component, os.environ.get('ANYMSG_TIDEN_ENVIRONMENT', 'production'))
    except Exception:
        log.warning('telemetry_configuration_unavailable')
        return False
    _reporter = reporter
    previous = sys.excepthook

    def hook(kind, error, tb):
        capture(error, 'uncaught_exception')
        reporter.close()
        previous(kind, error, tb)

    sys.excepthook = hook
    previous_thread = threading.excepthook

    def thread_hook(args):
        capture(args.exc_value, 'uncaught_exception')
        previous_thread(args)

    threading.excepthook = thread_hook
    atexit.register(reporter.close)
    return True
