import asyncio
import gzip
import zlib

import httpx
import pytest

from family_calendar.errors import CalendarError
from family_calendar.providers.caldav.transport import DAVTransport
from family_calendar.providers.caldav.adapter import CalDAVAdapter


def run(coro):
    return asyncio.run(coro)


def transport(handler, username="a"):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return DAVTransport(username, "never-log-me", client=client)


@pytest.mark.parametrize("encoding,compress", [("gzip", gzip.compress), ("deflate", zlib.compress)])
def test_compressed_response_returns_decoded_body(encoding, compress):
    body = b'<d:multistatus xmlns:d="DAV:"/>'
    compressed = compress(body)

    def handler(request):
        return httpx.Response(
            207,
            headers={
                "Content-Encoding": encoding,
                "Content-Length": str(len(compressed)),
                "Content-Type": "application/xml",
                "ETag": '"v1"',
            },
            stream=httpx.ByteStream(compressed),
        )

    async def exercise():
        t = transport(handler)
        try:
            response = await t.request("PROPFIND", "https://caldav.icloud.com/")
            assert response.content == body
            assert response.headers["content-type"] == "application/xml"
            assert response.headers["etag"] == '"v1"'
            assert int(response.headers["content-length"]) == len(body)
            assert "content-encoding" not in response.headers
        finally:
            await t.close()

    run(exercise())


@pytest.mark.parametrize(
    "method,error_code", [("GET", "provider_response_too_large"), ("PUT", "write_unknown")]
)
def test_compressed_response_limit_applies_after_decoding(monkeypatch, method, error_code):
    monkeypatch.setattr("family_calendar.providers.caldav.transport.MAX_RESPONSE", 128)
    compressed = gzip.compress(b"x" * 1024)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            headers={"Content-Encoding": "gzip"},
            stream=httpx.ByteStream(compressed),
        )

    async def exercise():
        t = transport(handler)
        try:
            with pytest.raises(CalendarError, match=error_code):
                await t.request(method, "https://caldav.icloud.com/a/c/e.ics")
        finally:
            await t.close()

    run(exercise())
    assert len(calls) == 1


def test_redirect_never_sends_credentials_to_other_host():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={"location": "https://evil.example.invalid/"})

    async def exercise():
        t = transport(handler)
        try:
            with pytest.raises(CalendarError, match="unsafe_provider_url"):
                await t.request("PROPFIND", "https://caldav.icloud.com/")
        finally:
            await t.close()

    run(exercise())
    assert len(calls) == 1


def test_conditional_headers_and_conflict():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(412)

    async def exercise():
        t = transport(handler)
        try:
            adapter = CalDAVAdapter("a", t)
            with pytest.raises(CalendarError, match="version_conflict"):
                await adapter.put("https://caldav.icloud.com/a/c/event.ics", b"event", etag='"v1"')
            with pytest.raises(CalendarError, match="version_conflict"):
                await adapter.put("https://caldav.icloud.com/a/c/new.ics", b"event", create=True)
        finally:
            await t.close()

    run(exercise())
    assert calls[0].headers["if-match"] == '"v1"'
    assert calls[1].headers["if-none-match"] == "*"


def test_missing_or_weak_etag_blocks_update():
    async def exercise():
        t = transport(lambda req: httpx.Response(200))
        try:
            adapter = CalDAVAdapter("a", t)
            for etag in (None, "", 'W/"v1"', "*"):
                with pytest.raises(CalendarError, match="conditional_write_unavailable"):
                    await adapter.put("https://caldav.icloud.com/a/c/e.ics", b"event", etag=etag)
        finally:
            await t.close()

    run(exercise())


def test_unauthorized_is_not_empty_calendar_or_secret_error():
    async def exercise():
        t = transport(lambda req: httpx.Response(401, text="never-log-me"))
        try:
            with pytest.raises(CalendarError, match="reauth_required") as exc:
                await t.request("GET", "https://caldav.icloud.com/a/c/e.ics")
            assert "never-log-me" not in str(exc.value)
        finally:
            await t.close()

    run(exercise())


def test_mutation_redirect_is_unknown_and_is_not_replayed():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(307, headers={"location": "https://p123-caldav.icloud.com/a/c/e.ics"})

    async def exercise():
        t = transport(handler)
        try:
            with pytest.raises(CalendarError, match="write_unknown"):
                await t.request("PUT", "https://caldav.icloud.com/a/c/e.ics", content=b"event")
        finally:
            await t.close()

    run(exercise())
    assert len(calls) == 1


def test_discovery_uses_only_successful_properties():
    responses = [
        "<d:current-user-principal><d:href>/a/principal/</d:href></d:current-user-principal>",
        "<c:calendar-home-set><d:href>/a/calendars/</d:href></c:calendar-home-set>",
        "<d:resourcetype><d:collection/><c:calendar/></d:resourcetype><d:displayname>Pilot</d:displayname>"
        '<d:getetag>"collection-v1"</d:getetag><c:calendar-timezone>Synthetic VTIMEZONE</c:calendar-timezone>'
        "<d:current-user-privilege-set><d:privilege><d:read/></d:privilege></d:current-user-privilege-set>",
    ]

    def handler(req):
        props = responses.pop(0)
        href = "/a/calendars/pilot/" if "displayname" in props else req.url.path
        xml = (
            f'<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:response>'
            f"<d:href>{href}</d:href><d:propstat><d:prop>{props}</d:prop><d:status>HTTP/1.1 200 OK</d:status>"
            "</d:propstat><d:propstat><d:prop><d:displayname>Denied</d:displayname></d:prop>"
            "<d:status>HTTP/1.1 403 Forbidden</d:status></d:propstat></d:response></d:multistatus>"
        )
        return httpx.Response(207, text=xml)

    async def exercise():
        t = transport(handler)
        try:
            calendars = await CalDAVAdapter("a", t).discover()
            assert len(calendars) == 1
            assert calendars[0]["label"] == "Pilot"
            assert calendars[0]["privilege"] == "read_only"
            assert calendars[0]["collection_etag"] == '"collection-v1"'
            assert calendars[0]["timezone_ical"] == "Synthetic VTIMEZONE"
        finally:
            await t.close()

    run(exercise())


def test_report_rejects_non_multistatus_body_instead_of_empty_window():
    async def exercise():
        t = transport(lambda req: httpx.Response(200, text="<html/>"))
        try:
            with pytest.raises(CalendarError, match="invalid_provider_xml"):
                await CalDAVAdapter("a", t).read_window(
                    "https://caldav.icloud.com/a/c/", "20261019T000000Z", "20261020T000000Z"
                )
        finally:
            await t.close()

    run(exercise())
