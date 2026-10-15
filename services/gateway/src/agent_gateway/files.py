"""Bounded ephemeral bearer tickets for original bytes; never an arbitrary proxy."""

import asyncio
from contextlib import AsyncExitStack, suppress
import hashlib
import hmac
import re
import secrets
import time
from urllib.parse import quote, urlsplit

import httpx
from starlette.responses import JSONResponse, StreamingResponse

from .policy import Denied, key_from_file


def origin(value):
    parsed = urlsplit(value)
    return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)


class FileResponse(StreamingResponse):
    def __init__(self, body, stack, check, headers, backend_check=None):
        super().__init__(body, media_type="application/octet-stream", headers=headers)
        self.stack, self.check = stack, check
        self.backend_check = backend_check

    async def __call__(self, scope, receive, send):
        owner = asyncio.current_task()

        async def monitor():
            last_backend_check = time.monotonic()
            while True:
                await asyncio.sleep(0.25)
                try:
                    self.check()
                    if self.backend_check and time.monotonic() - last_backend_check >= 1:
                        await self.backend_check()
                        last_backend_check = time.monotonic()
                except Exception:
                    # Also interrupts a blocked upstream read or client send.
                    # Headers may already be sent; abort instead of a second
                    # response pretending the partial file was complete.
                    owner.cancel()
                    return

        watch = asyncio.create_task(monitor())
        try:
            await super().__call__(scope, receive, send)
        finally:
            watch.cancel()
            with suppress(asyncio.CancelledError):
                await watch
            await self.stack.aclose()


class Tickets:
    def __init__(self, policy, clients, public_url, capacity=4096):
        self.policy, self.clients, self.public_url = policy, clients, public_url
        self.capacity, self.entries = capacity, {}

    def clear(self):
        self.entries.clear()

    def issue(self, result, key, agent, snapshot, credential, attachment, started):
        try:
            source = urlsplit(result["download_url"])
            target = snapshot["deployment"]["backends"]["messaging"]
            ttl, size, sha = result["download_expires_in"], result["bytes"], result["sha256"]
            if origin(result["download_url"]) != origin(target.get("download_origin") or target["url"]):
                raise ValueError
            prefix = urlsplit(target.get("download_origin") or target["url"]).path.rstrip("/")
            backend_path = source.path.removeprefix(prefix)
            if (
                not source.path.startswith(prefix + "/")
                or source.username
                or source.password
                or source.query
                or source.fragment
                or not re.fullmatch(r"/v1/download/[A-Za-z0-9_-]{43}", backend_path)
            ):
                raise ValueError
            if (
                type(ttl) is not int
                or not 0 < ttl <= 120
                or type(size) is not int
                or not 0 <= size <= 2**63 - 1
            ):
                raise ValueError
            if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
                raise ValueError
            expires = started + ttl - 1
            if expires <= time.time():
                raise ValueError
        except (KeyError, ValueError, TypeError):
            raise Denied("invalid_download_grant", 503) from None
        now = time.time()
        self.entries = {k: v for k, v in self.entries.items() if v["expires"] > now}
        while len(self.entries) >= self.capacity:
            del self.entries[next(iter(self.entries))]
        token = secrets.token_urlsafe(32)
        self.entries[hashlib.sha256(token.encode()).hexdigest()] = {
            "key_digest": hashlib.sha256(key.encode()).hexdigest(),
            "principal": agent["id"],
            "backend_key_digest": hashlib.sha256(credential.encode()).hexdigest(),
            "target": target,
            "path": backend_path,
            "expires": expires,
            "sha256": sha,
            "bytes": size,
            "filename": str(result.get("filename") or "download"),
            "attachment": attachment,
        }
        return {
            **result,
            "download_url": self.public_url + "/v1/files/" + token,
            "download_expires_in": max(0, int(expires - now)),
            "notice": "Temporary bearer link; current access and expiry are checked when downloading. Do not share or log.",
        }

    def check(self, row, *, admission=False):
        """Recheck the ticket's current policy and credential binding.

        The TTL bounds admission only: a transfer that has started may outlive
        it, but never a revocation of the principal, tool, backend or key.
        """
        if admission and row["expires"] <= time.time():
            raise Denied("not_found", 404)
        snapshot, agent, _ = self.policy.authorize_digest(row["key_digest"], "messages_get_attachment")
        if (
            agent["id"] != row["principal"]
            or snapshot["deployment"]["backends"].get("messaging") != row["target"]
        ):
            raise Denied("not_found", 404)
        binding = agent["credentials"]["messaging"]
        if not hmac.compare_digest(binding["key_sha256"], row["backend_key_digest"]):
            raise Denied("not_found", 404)
        key_from_file(binding["key_file"], row["backend_key_digest"])

    async def check_backend(self, row):
        # The existing download route validates the grant/key/resource for HEAD
        # too. Use the independent metadata pool so bulk saturation cannot mask
        # a backend ACL change. No body is consumed and no new grant is minted.
        try:
            async with asyncio.timeout(2):
                async with self.clients.metadata["messaging"].stream("HEAD", row["path"]) as response:
                    if response.status_code != 200:
                        raise Denied("not_found", 404)
        except (TimeoutError, httpx.HTTPError):
            raise Denied("backend_unavailable", 503) from None

    async def download(self, token, request):
        stack = AsyncExitStack()
        try:
            if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
                raise Denied("not_found", 404)
            row = self.entries.get(hashlib.sha256(token.encode()).hexdigest())
            if row is None:
                raise Denied("not_found", 404)
            try:
                self.check(row, admission=True)
            except Denied:
                raise Denied("not_found", 404) from None
            if request.headers.get("range") is not None:
                raise Denied("ranges_not_supported", 416)
            await stack.enter_async_context(self.clients.bulk_budgets["messaging"].slot())
            response = await stack.enter_async_context(
                self.clients.bulk["messaging"].stream(
                    "GET", row["path"], headers={"Accept-Encoding": "identity"}
                )
            )
            if (
                response.status_code != 200
                or response.headers.get("content-encoding", "identity") != "identity"
            ):
                raise Denied(
                    "backend_unavailable" if response.status_code != 404 else "not_found",
                    503 if response.status_code != 404 else 404,
                )
            if response.headers.get("content-length") not in (None, str(row["bytes"])):
                raise Denied("backend_file_integrity_error", 503)
            self.check(row)

            async def chunks():
                digest, length = hashlib.sha256(), 0
                async for chunk in response.aiter_raw(65536):
                    self.check(row)
                    length += len(chunk)
                    if length > row["bytes"]:
                        raise RuntimeError("backend_file_integrity_error")
                    digest.update(chunk)
                    yield chunk
                if length != row["bytes"] or not hmac.compare_digest(digest.hexdigest(), row["sha256"]):
                    raise RuntimeError("backend_file_integrity_error")

            return FileResponse(
                chunks(),
                stack,
                lambda: self.check(row),
                {
                    "Content-Length": str(row["bytes"]),
                    "Content-Disposition": "attachment; filename*=UTF-8''" + quote(row["filename"], safe=""),
                },
                lambda: self.check_backend(row),
            )
        except (Denied, httpx.HTTPError) as error:
            await stack.aclose()
            code, status = (
                (error.code, error.status) if isinstance(error, Denied) else ("backend_unavailable", 503)
            )
            return JSONResponse({"error": code}, status_code=status)
        except BaseException:
            await stack.aclose()
            raise
