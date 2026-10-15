"""Independent bounded pools; no provider libraries and no automatic retries."""

import asyncio
from collections import deque
from contextlib import asynccontextmanager
import hashlib
import json
import time

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


class UploadRate:
    """Bound both byte-bearing request rate and retained credential state."""

    def __init__(self):
        self.observations = {}

    def admit(self, key):
        now = time.monotonic()
        digest = hashlib.sha256(key.encode()).digest()
        if digest not in self.observations:
            if len(self.observations) >= 1024:
                self.observations = {
                    k: times for k, times in self.observations.items() if times and times[-1] > now - 1
                }
                if len(self.observations) >= 1024:
                    raise Denied("backend_busy", 503)
            self.observations[digest] = deque()
        times = self.observations[digest]
        while times and times[0] <= now - 1:
            times.popleft()
        if len(times) >= 8:
            raise Denied("upload_rate_limited", 429)
        times.append(now)


class Clients:
    def __init__(self, deployment, transports=None):
        self.deployment, self.transports = deployment, transports or {}
        self.metadata, self.bulk, self.budgets, self.bulk_budgets = {}, {}, {}, {}
        self.uploads, self.upload_budgets = {}, {}
        self.upload_rate = UploadRate()
        self.closed = False

    async def __aenter__(self):
        try:
            for module in self.deployment["modules"]:
                target = self.deployment["backends"][module]
                pools = [(self.metadata, 8), (self.bulk, 2)]
                if module == "messaging":
                    pools.append((self.uploads, 2))
                for container, maximum in pools:
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
                if module == "messaging":
                    self.upload_budgets[module] = Budget(2, 4)
            return self
        except BaseException:
            await self.__aexit__(None, None, None)
            raise

    async def __aexit__(self, *args):
        for client in [*self.metadata.values(), *self.bulk.values(), *self.uploads.values()]:
            await client.aclose()
        self.upload_rate.observations.clear()
        self.closed = True

    async def call(self, module, operation, key, arguments):
        if module not in self.metadata:
            raise Denied("backend_unavailable", 503)
        uploading = module == "messaging" and operation == "upload_file_chunk"
        if uploading:
            self.upload_rate.admit(key)
        budget = self.upload_budgets[module] if uploading else self.budgets[module]
        client = self.uploads[module] if uploading else self.metadata[module]
        async with budget.slot():
            try:
                async with (
                    asyncio.timeout(10 if uploading else 35),
                    client.stream(
                        "POST", "/v1/" + operation, headers={"Authorization": "Bearer " + key}, json=arguments
                    ) as response,
                ):
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
            except (httpx.HTTPError, ValueError, TimeoutError):
                raise Denied("backend_unavailable", 503) from None
