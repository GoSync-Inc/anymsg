import asyncio
from datetime import datetime, timezone
import httpx
import pytest
from family_calendar.errors import CalendarError


def test_google_conditional_patch_preserves_unrelated_fields():
    from family_calendar.providers.google.adapter import GoogleAdapter

    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "event1", "etag": '"v2"'})

    async def run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        adapter = GoogleAdapter("conn", client, lambda: "canary-token")
        await adapter.patch("calendar", "event1", {"summary": "Changed"}, '"v1"')
        await client.aclose()

    asyncio.run(run())
    assert requests[0].headers["if-match"] == '"v1"'
    assert requests[0].url.params["sendUpdates"] == "none"
    assert requests[0].content == b'{"summary":"Changed"}'


def test_google_create_uses_provider_native_insert_condition():
    from family_calendar.providers.google.adapter import GoogleAdapter

    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "fcs012345", "etag": '"v1"'})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            await GoogleAdapter("conn", client, lambda: "token").create("calendar", {"id": "fcs012345"})

    asyncio.run(run())
    assert b'"id":"fcs012345"' in requests[0].content
    assert "if-none-match" not in requests[0].headers


def test_google_definite_bad_request_is_distinct_from_unknown_write():
    from family_calendar.providers.google.adapter import GoogleAdapter

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(400, json={"error": {"message": "private"}})
            )
        ) as client:
            with pytest.raises(CalendarError, match="write_rejected"):
                await GoogleAdapter("conn", client, lambda: "token").create("calendar", {"id": "fcs012345"})

    asyncio.run(run())


def test_google_does_not_retry_ambiguous_mutation():
    from family_calendar.providers.google.adapter import GoogleAdapter

    count = 0

    def handle(request):
        nonlocal count
        count += 1
        raise httpx.ReadTimeout("canary secret should not escape")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            adapter = GoogleAdapter("conn", client, lambda: "canary-token")
            with pytest.raises(CalendarError, match="unknown_result"):
                await adapter.create("calendar", {"id": "stable-id"})

    asyncio.run(run())
    assert count == 1


def test_google_paginated_window_is_never_marked_complete_early():
    from family_calendar.providers.google.adapter import GoogleAdapter

    def handle(request):
        if "pageToken" not in request.url.params:
            return httpx.Response(200, json={"items": [{"id": "first"}], "nextPageToken": "page2"})
        return httpx.Response(503, json={"error": {"message": "private canary"}})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            adapter = GoogleAdapter("conn", client, lambda: "token")
            with pytest.raises(CalendarError, match="provider_unavailable"):
                await adapter.window("calendar", "2026-10-19T08:00:00Z", "2026-10-19T10:00:00Z")

    asyncio.run(run())


def test_icloud_recurring_window_preserves_moved_exception_identity():
    from family_calendar.service.providers import normalize_icloud

    raw = b"""BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:weekly\r\nDTSTART:20261019T080000Z\r\nDTEND:20261019T090000Z\r\nRRULE:FREQ=WEEKLY;COUNT=3\r\nSUMMARY:Lesson\r\nEND:VEVENT\r\nBEGIN:VEVENT\r\nUID:weekly\r\nRECURRENCE-ID:20261026T080000Z\r\nDTSTART:20261026T100000Z\r\nDTEND:20261026T110000Z\r\nSUMMARY:Moved\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"""
    a = datetime(2026, 10, 19, tzinfo=timezone.utc)
    b = datetime(2026, 11, 3, tzinfo=timezone.utc)
    result = normalize_icloud(
        "cal",
        {"href": "https://p01-caldav.icloud.com/test/event.ics", "etag": '"v1"', "data": raw},
        a,
        b,
        "Asia/Makassar",
    )
    assert len(result["occurrences"]) == 3
    moved = next(x for x in result["occurrences"] if x["details"]["title"] == "Moved")
    assert moved["start"].hour == 10
    assert moved["details"]["recurring"] is True


def test_floating_event_degrades_availability():
    from family_calendar.service.providers import normalize_icloud

    raw = b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:x\r\nDTSTART:20261019T160000\r\nDTEND:20261019T170000\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    with pytest.raises(CalendarError, match="coverage_incomplete"):
        normalize_icloud(
            "cal",
            {"href": "https://p01-caldav.icloud.com/test/x.ics", "etag": '"v1"', "data": raw},
            datetime(2026, 10, 19, tzinfo=timezone.utc),
            datetime(2026, 10, 20, tzinfo=timezone.utc),
            "Asia/Makassar",
        )


def test_google_nonrecurring_event_ref_survives_time_move():
    from family_calendar.service.providers import normalize_google

    event = {
        "id": "stable",
        "etag": '"v1"',
        "start": {"dateTime": "2026-10-19T08:00:00Z"},
        "end": {"dateTime": "2026-10-19T09:00:00Z"},
    }
    first = normalize_google("cal", event, "Asia/Makassar")["occurrences"][0]["ref"]
    event["start"]["dateTime"] = "2026-10-19T10:00:00Z"
    event["end"]["dateTime"] = "2026-10-19T11:00:00Z"
    assert normalize_google("cal", event, "Asia/Makassar")["occurrences"][0]["ref"] == first


def test_icloud_nonrecurring_event_ref_survives_time_move():
    from family_calendar.service.providers import normalize_icloud

    a = datetime(2026, 10, 19, tzinfo=timezone.utc)
    b = datetime(2026, 10, 20, tzinfo=timezone.utc)
    raw = b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:stable\r\nDTSTART:20261019T080000Z\r\nDTEND:20261019T090000Z\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    resource = {"href": "https://p01-caldav.icloud.com/test/stable.ics", "etag": '"v1"', "data": raw}
    first = normalize_icloud("cal", resource, a, b, "Asia/Makassar")["occurrences"][0]["ref"]
    resource["data"] = raw.replace(b"T080000", b"T100000").replace(b"T090000", b"T110000")
    assert normalize_icloud("cal", resource, a, b, "Asia/Makassar")["occurrences"][0]["ref"] == first
