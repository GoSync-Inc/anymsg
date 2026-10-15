import asyncio

import httpx
import pytest

from agent_gateway.http_clients import Clients, Budget
from agent_gateway.policy import Denied


def test_saturated_module_and_file_pool_do_not_block_other_metadata():
    async def run():
        release = asyncio.Event()

        async def slow(request):
            await release.wait()
            return httpx.Response(200, json={"done": True})

        async def fast(request):
            return httpx.Response(200, json={"coverage": "unknown"})

        deployment = {
            "modules": ["calls", "calendar", "messaging"],
            "backends": {name: {"url": "http://" + name} for name in ("calls", "calendar", "messaging")},
        }
        async with Clients(
            deployment,
            {
                "calls": httpx.MockTransport(slow),
                "calendar": httpx.MockTransport(fast),
                "messaging": httpx.MockTransport(fast),
            },
        ) as clients:
            blocked = [asyncio.create_task(clients.call("calls", "read", "synthetic", {})) for _ in range(32)]
            try:
                await asyncio.sleep(0.02)
                with pytest.raises(Denied, match="backend_busy"):
                    await clients.call("calls", "read", "synthetic", {})
                async with clients.bulk_budgets["messaging"].slot(), clients.bulk_budgets["messaging"].slot():
                    async with asyncio.timeout(0.2):
                        assert (await clients.call("calendar", "agenda", "synthetic", {}))[
                            "coverage"
                        ] == "unknown"
                        assert (await clients.call("messaging", "search", "synthetic", {}))[
                            "coverage"
                        ] == "unknown"
            finally:
                for task in blocked:
                    task.cancel()
                await asyncio.gather(*blocked, return_exceptions=True)
            assert all(b.pending == 0 for b in clients.budgets.values())
        assert clients.closed

    asyncio.run(run())


def test_queue_timeout_and_cancellation_release_capacity():
    async def run():
        budget = Budget(1, 1)
        async with budget.slot():
            with pytest.raises(Denied, match="backend_busy"):
                async with budget.slot():
                    pytest.fail("must not enter a saturated pool")
            assert budget.pending == 1
        assert budget.pending == 0
        async with budget.slot():
            assert budget.pending == 1

    asyncio.run(run())
