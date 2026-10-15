import asyncio
from contextlib import AsyncExitStack
import tracemalloc

import pytest

from agent_gateway.files import FileResponse
from agent_gateway.policy import Denied


def test_large_stream_is_bounded_and_closes_without_buffering():
    async def run():
        stack = AsyncExitStack()
        closed = []
        stack.callback(lambda: closed.append(True))
        sent = 0

        async def body():
            for _ in range(1024):
                yield b"x" * 65536

        async def send(event):
            nonlocal sent
            if event["type"] == "http.response.body":
                assert len(event.get("body", b"")) <= 65536
                sent += len(event.get("body", b""))
            await asyncio.sleep(0)

        async def receive():
            await asyncio.Future()

        response = FileResponse(body(), stack, lambda: None, {})
        tracemalloc.start()
        try:
            await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert sent == 64 * 1024 * 1024
        assert peak < 2 * 1024 * 1024
        assert closed == [True]

    asyncio.run(run())


@pytest.mark.parametrize("blocked", ["upstream", "downstream"])
def test_revocation_interrupts_blocked_io_and_releases_upstream(blocked):
    async def run():
        stack = AsyncExitStack()
        closed, events = [], []
        stack.callback(lambda: closed.append(True))
        started = asyncio.Event()
        revoked = False

        def check():
            if revoked:
                raise Denied()

        async def body():
            if blocked == "upstream":
                started.set()
                await asyncio.Future()
            yield b"synthetic"

        async def send(event):
            events.append(event)
            if blocked == "downstream" and event["type"] == "http.response.body":
                started.set()
                await asyncio.Future()

        async def receive():
            await asyncio.Future()

        response = FileResponse(body(), stack, check, {})
        task = asyncio.create_task(response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send))
        await asyncio.wait_for(started.wait(), 1)
        revoked = True
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        assert closed == [True]
        assert sum(e["type"] == "http.response.start" for e in events) == 1
        assert not any(e["type"] == "http.response.body" and not e.get("more_body") for e in events)

    asyncio.run(run())
