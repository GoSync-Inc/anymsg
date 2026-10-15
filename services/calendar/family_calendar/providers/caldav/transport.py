"""Bounded HTTPS CalDAV requests; mutations are never automatically retried."""

import re
from urllib.parse import urlsplit, urljoin, unquote

import httpx

from family_calendar.errors import CalendarError

MAX_RESPONSE = 1_048_576


def provider_url(url):
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.username
        or parts.password
        or parts.port not in (None, 443)
        or not re.fullmatch(r"(?:p\d+-)?caldav\.icloud\.com", parts.hostname or "")
        or parts.query
        or parts.fragment
    ):
        raise CalendarError("unsafe_provider_url")
    decoded = unquote(unquote(parts.path))
    if "\\" in decoded or any(x in ("..", ".") for x in decoded.split("/")):
        raise CalendarError("unsafe_provider_url")
    return url


def within_collection(href, collection):
    provider_url(href)
    provider_url(collection)
    h, c = urlsplit(href), urlsplit(collection)
    return h.netloc == c.netloc and h.path.startswith(c.path.rstrip("/") + "/") and h.path != c.path


class DAVTransport:
    def __init__(self, username, password, *, client=None):
        self._auth = httpx.BasicAuth(username, password)
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(20, connect=10), follow_redirects=False, trust_env=False
        )

    async def close(self):
        await self.client.aclose()

    async def request(self, method, url, *, content=b"", headers=None, allow_missing=False):
        if method not in {"GET", "PROPFIND", "REPORT", "PUT", "DELETE"}:
            raise CalendarError("unsupported_provider_method")
        mutation = method in {"PUT", "DELETE"}
        for _ in range(4):
            provider_url(url)
            try:
                async with self.client.stream(
                    method, url, content=content, headers=headers, auth=self._auth, follow_redirects=False
                ) as stream:
                    body = bytearray()
                    async for chunk in stream.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_RESPONSE:
                            raise CalendarError(
                                "write_unknown" if mutation else "provider_response_too_large"
                            )
                    # aiter_bytes() has already decoded Content-Encoding. The
                    # buffered response must describe those decoded bytes.
                    response_headers = stream.headers.copy()
                    for name in ("content-encoding", "content-length", "transfer-encoding"):
                        response_headers.pop(name, None)
                    response = httpx.Response(
                        stream.status_code,
                        headers=response_headers,
                        content=bytes(body),
                        request=stream.request,
                    )
            except httpx.HTTPError:
                raise CalendarError("write_unknown" if mutation else "provider_unavailable") from None
            if response.status_code in {301, 302, 303, 307, 308}:
                # Do not replay a mutation even on an apparently safe redirect.
                if mutation:
                    raise CalendarError("write_unknown")
                target = urljoin(url, response.headers.get("location", ""))
                provider_url(target)
                url = target
                continue
            if response.status_code == 401:
                raise CalendarError("reauth_required")
            if response.status_code == 403:
                raise CalendarError("provider_forbidden")
            if response.status_code == 412:
                raise CalendarError("version_conflict")
            if response.status_code == 404 and allow_missing:
                return response
            if response.status_code >= 500 or response.status_code in {408, 429}:
                raise CalendarError("write_unknown" if mutation else "provider_unavailable")
            if not 200 <= response.status_code < 300:
                raise CalendarError("provider_request_rejected")
            return response
        raise CalendarError("provider_redirect_limit")
