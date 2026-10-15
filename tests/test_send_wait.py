"""Passive send waits use synthetic status readers and never invoke a provider."""
import asyncio
import threading
import time

import pytest

from messaging_service.store import APIError


class StatusReader:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def get_send_status(self, token, send_id):
        self.calls.append((token, send_id, threading.get_ident()))
        result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(result, Exception):
            raise result
        return result


def test_wait_observes_exact_receipt_without_dispatching(monkeypatch):
    from messaging_service import send_wait
    monkeypatch.setattr(send_wait, 'POLL_INTERVAL_S', 0.001)
    receipt = {'send_id': 'synthetic', 'status': 'sent',
        'provider_message_id': 'exact-receipt', 'automatic_retry': False,
        'delivery_receipt': {'recipient_delivery_confirmed': False}}
    store = StatusReader({'status': 'queued'}, {'status': 'dispatching'}, receipt)
    main_thread = threading.get_ident()
    result = asyncio.run(send_wait.wait_for_send(store, 'scoped-key', 'synthetic', 1))
    assert {key: result[key] for key in receipt} == receipt
    assert result['wait_timed_out'] is False
    assert 0 <= result['wait_elapsed_s'] < 1
    assert len(store.calls) == 3
    assert all(token == 'scoped-key' and send_id == 'synthetic' and thread != main_thread
        for token, send_id, thread in store.calls)
    assert 'wait_elapsed_s' not in receipt


@pytest.mark.parametrize('status', ['sent', 'failed', 'unknown', 'cancelled', 'expired'])
def test_terminal_status_is_preserved_without_another_poll(status):
    from messaging_service.send_wait import wait_for_send
    store = StatusReader({'status': status, 'automatic_retry': False}, {'status': 'sent'})
    result = asyncio.run(wait_for_send(store, 'scoped-key', 'synthetic', 1))
    assert result['status'] == status
    assert result['automatic_retry'] is False
    assert result['wait_timed_out'] is False
    assert len(store.calls) == 1


def test_wait_timeout_returns_last_pending_observation(monkeypatch):
    from messaging_service import send_wait
    monkeypatch.setattr(send_wait, 'POLL_INTERVAL_S', 0.01)
    store = StatusReader({'status': 'queued', 'next_action': 'get_send_status'})
    started = time.monotonic()
    result = asyncio.run(send_wait.wait_for_send(store, 'scoped-key', 'synthetic', 0.04))
    assert result['status'] == 'queued'
    assert result['next_action'] == 'get_send_status'
    assert result['wait_timed_out'] is True
    assert 0.03 <= result['wait_elapsed_s'] < 0.3
    assert time.monotonic() - started < 0.3
    assert 2 <= len(store.calls) <= 6


def test_permission_revocation_propagates_on_next_poll(monkeypatch):
    from messaging_service import send_wait
    monkeypatch.setattr(send_wait, 'POLL_INTERVAL_S', 0.001)
    monkeypatch.setattr(send_wait, '_WAIT_SLOTS', threading.BoundedSemaphore(1))
    store = StatusReader({'status': 'queued'}, APIError('forbidden', 403))
    with pytest.raises(APIError, match='forbidden'):
        asyncio.run(send_wait.wait_for_send(store, 'revoked-key', 'synthetic', 1))
    assert len(store.calls) == 2
    assert asyncio.run(send_wait.wait_for_send(StatusReader({'status': 'sent'}),
        'other-key', 'other-action', 1))['status'] == 'sent'


@pytest.mark.parametrize('wait_s', [True, False, -1, 25.01, float('inf'), float('nan'), '1', None])
def test_invalid_wait_never_reads_or_queues(wait_s):
    from messaging_service.send_wait import validate_wait_s, wait_for_send
    store = StatusReader({'status': 'queued'})
    with pytest.raises(APIError, match='invalid_wait_s'):
        validate_wait_s(wait_s)
    with pytest.raises(APIError, match='invalid_wait_s'):
        asyncio.run(wait_for_send(store, 'scoped-key', 'synthetic', wait_s))
    assert not store.calls


def test_zero_wait_reads_once_without_polling():
    from messaging_service.send_wait import wait_for_send
    store = StatusReader({'status': 'dispatching'})
    result = asyncio.run(wait_for_send(store, 'scoped-key', 'synthetic', 0))
    assert result['status'] == 'dispatching'
    assert result['wait_timed_out'] is False
    assert len(store.calls) == 1


def test_cancellation_during_sleep_releases_admission(monkeypatch):
    from messaging_service import send_wait
    monkeypatch.setattr(send_wait, '_WAIT_SLOTS', threading.BoundedSemaphore(1))
    monkeypatch.setattr(send_wait, 'POLL_INTERVAL_S', 1)
    store = StatusReader({'status': 'queued'})

    async def scenario():
        waiting = asyncio.create_task(send_wait.wait_for_send(store, 'key', 'first', 2))
        while not store.calls:
            await asyncio.sleep(0.001)
        await asyncio.sleep(0.005)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        result = await send_wait.wait_for_send(StatusReader({'status': 'unknown'}), 'key', 'next', 1)
        assert result['status'] == 'unknown'
    asyncio.run(scenario())


def test_timed_out_inflight_read_keeps_bound_until_thread_exits(monkeypatch):
    from messaging_service import send_wait
    monkeypatch.setattr(send_wait, '_WAIT_SLOTS', threading.BoundedSemaphore(1))
    monkeypatch.setattr(send_wait, 'POLL_INTERVAL_S', 0.001)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    class BlockingReader:
        calls = 0

        def get_send_status(self, token, send_id):
            self.calls += 1
            if self.calls == 1:
                return {'status': 'dispatching'}
            entered.set()
            try:
                assert release.wait(2), 'synthetic reader was not released'
                return {'status': 'sent'}
            finally:
                finished.set()

    async def scenario():
        try:
            result = await send_wait.wait_for_send(BlockingReader(), 'key', 'first', 0.03)
            assert entered.is_set()
            assert result['status'] == 'dispatching'
            assert result['wait_timed_out'] is True
            with pytest.raises(APIError, match='send_wait_busy'):
                await send_wait.wait_for_send(StatusReader({'status': 'sent'}), 'key', 'second', 1)
        finally:
            release.set()
        while not finished.is_set():
            await asyncio.sleep(0.001)
        result = await send_wait.wait_for_send(StatusReader({'status': 'sent'}), 'key', 'third', 1)
        assert result['status'] == 'sent'
    asyncio.run(scenario())


def test_first_status_read_has_a_bounded_deadline_and_cleans_up(monkeypatch):
    from messaging_service import send_wait
    monkeypatch.setattr(send_wait, '_WAIT_SLOTS', threading.BoundedSemaphore(1))
    entered, release = threading.Event(), threading.Event()

    class BlockingReader:
        def get_send_status(self, token, send_id):
            entered.set()
            assert release.wait(2), 'synthetic reader was not released'
            return {'status': 'sent'}

    async def scenario():
        started = time.monotonic()
        try:
            with pytest.raises(APIError, match='send_status_timeout'):
                await send_wait.wait_for_send(BlockingReader(), 'key', 'first', 0.03)
            assert entered.is_set()
            assert time.monotonic() - started < 0.3
        finally:
            release.set()
    asyncio.run(scenario())


def test_cancellation_during_status_read_keeps_thread_bound(monkeypatch):
    from messaging_service import send_wait
    monkeypatch.setattr(send_wait, '_WAIT_SLOTS', threading.BoundedSemaphore(1))
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    class BlockingReader:
        def get_send_status(self, token, send_id):
            entered.set()
            try:
                assert release.wait(2), 'synthetic reader was not released'
                return {'status': 'sent'}
            finally:
                finished.set()

    async def scenario():
        waiting = asyncio.create_task(send_wait.wait_for_send(BlockingReader(), 'key', 'first', 1))
        try:
            while not entered.is_set():
                await asyncio.sleep(0.001)
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting
            with pytest.raises(APIError, match='send_wait_busy'):
                await send_wait.wait_for_send(StatusReader({'status': 'sent'}), 'key', 'next', 1)
        finally:
            release.set()
        while not finished.is_set():
            await asyncio.sleep(0.001)
        assert (await send_wait.wait_for_send(StatusReader({'status': 'sent'}),
            'key', 'last', 1))['status'] == 'sent'
    asyncio.run(scenario())


@pytest.mark.parametrize('wait_s', [0, 0.5, 25])
def test_valid_wait_boundaries(wait_s):
    from messaging_service.send_wait import validate_wait_s
    assert validate_wait_s(wait_s) == wait_s
