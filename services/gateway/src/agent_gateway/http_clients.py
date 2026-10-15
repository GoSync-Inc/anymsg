"""Independent bounded pools; no provider libraries and no automatic retries."""

import asyncio
from contextlib import asynccontextmanager
import json

import httpx

from .policy import Denied


class Budget:
    def __init__(self, concurrency=8, waiting=24):
        self.semaphore = asyncio.Semaphore(concurrency)
        self.capacity, self.pending = concurrency + waiting, 0

    @asynccontextmanager
    async def slot(self):
        if self.pending >= self.capacity:
            raise Denied("backend_busy", 503)
        self.pending += 1
        acquired = False
        try:
            try:
                await asyncio.wait_for(self.semaphore.acquire(), timeout=0.25)
                acquired = True
            except TimeoutError:
                raise Denied("backend_busy", 503) from None
            yield
        finally:
            if acquired:
                self.semaphore.release()
            self.pending -= 1


class Clients:
    def __init__(self, deployment, transports=None):
        self.deployment, self.transports = deployment, transports or {}
        self.metadata, self.bulk, self.budgets, self.bulk_budgets = {}, {}, {}, {}
        self.closed = False

    async def __aenter__(self):
        try:
            for module in self.deployment["modules"]:
                target = self.deployment["backends"][module]
                for container, maximum in ((self.metadata, 8), (self.bulk, 2)):
                    container[module] = httpx.AsyncClient(
                        base_url=target["url"].rstrip("/"),
                        timeout=httpx.Timeout(35, connect=5, pool=0.25),
                        trust_env=False,
                        follow_redirects=False,
                        limits=httpx.Limits(max_connections=maximum, max_keepalive_connections=maximum),
                        transport=self.transports.get(module),
                        headers={"Host": target["host_header"]} if target.get("host_header") else None,
                    )
                self.budgets[module], self.bulk_budgets[module] = Budget(), Budget(2, 4)
            return self
        except BaseException:
            await self.__aexit__(None, None, None)
            raise

    async def __aexit__(self, *args):
        for client in [*self.metadata.values(), *self.bulk.values()]:
            await client.aclose()
        self.closed = True

    async def call(self, module, operation, key, arguments):
        if module not in self.metadata:
            raise Denied("backend_unavailable", 503)
        async with self.budgets[module].slot():
            try:
                async with self.metadata[module].stream(
                    "POST", "/v1/" + operation, headers={"Authorization": "Bearer " + key}, json=arguments
                ) as response:
                    if response.status_code >= 500:
                        raise Denied("backend_unavailable", 503)
                    if response.status_code in (301, 302, 303, 307, 308):
                        raise Denied("backend_unavailable", 503)
                    body = bytearray()
                    async for chunk in response.aiter_bytes(65536):
                        if len(body) + len(chunk) > 8 * 1024 * 1024:
                            raise Denied("backend_response_too_large", 503)
                        body.extend(chunk)
                    result = json.loads(body)
                    if not isinstance(result, dict):
                        raise Denied("backend_unavailable", 503)
                    if response.status_code >= 400:
                        error = Denied("backend_denied", response.status_code)
                        # Existing operation receipts/ACK errors remain visible, but
                        # never leak transport exceptions or arbitrary response text.
                        error.detail = result
                        raise error
                    return result
            except (httpx.HTTPError, ValueError):
                raise Denied("backend_unavailable", 503) from None
