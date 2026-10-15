import asyncio
from datetime import datetime, timedelta, timezone
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


def _series(rule, dtstart="20261001T000000Z", uid="series"):
    return (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\n"
        f"UID:{uid}\r\nDTSTART:{dtstart}\r\nDURATION:PT1S\r\nRRULE:{rule}\r\nSUMMARY:Synthetic\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n"
    ).encode()


def _resource(data, name="series"):
    return {"href": f"https://p01-caldav.icloud.com/test/{name}.ics", "etag": '"v1"', "data": data}


OCTOBER = (datetime(2026, 10, 1, tzinfo=timezone.utc), datetime(2026, 11, 1, tzinfo=timezone.utc))


@pytest.fixture
def expansions(monkeypatch):
    from recurring_ical_events.occurrence import Occurrence

    calls = []
    original = Occurrence.as_component

    def counted(self, keep):
        calls.append(self.start)
        return original(self, keep)

    monkeypatch.setattr(Occurrence, "as_component", counted)
    return calls


@pytest.mark.parametrize(
    "rule,dtstart",
    [
        ("FREQ=SECONDLY", "20261001T000000Z"),
        ("FREQ=MINUTELY", "20250101T000000Z"),
        ("FREQ=DAILY;BYHOUR=0,1,2,3,4,5,6,7,8,9;BYMINUTE=0,10,20,30,40,50;BYSECOND=0,30", "20240101T000000Z"),
    ],
)
def test_dense_rule_is_incomplete_without_expansion(monkeypatch, rule, dtstart):
    from family_calendar.service import providers

    def refuse(*args, **kwargs):
        raise AssertionError("dense rule must not be expanded")

    monkeypatch.setattr(providers.recurring_ical_events, "of", refuse)
    with pytest.raises(CalendarError, match="coverage_incomplete"):
        providers.normalize_icloud("cal", _resource(_series(rule, dtstart)), *OCTOBER, "Asia/Makassar")


@pytest.mark.parametrize(
    "rule",
    [
        "FREQ=HOURLY;INTERVAL=5",
        "FREQ=DAILY;BYHOUR=9,17;BYMINUTE=0,30",
        "FREQ=WEEKLY;BYDAY=MO,WE,FR",
        "FREQ=WEEKLY;INTERVAL=2;BYDAY=SA;BYMONTH=6,7",
        "FREQ=MONTHLY;BYDAY=1MO,-1FR",
        "FREQ=MONTHLY;BYMONTHDAY=1,15,31",
        "FREQ=MONTHLY;INTERVAL=2;COUNT=7",
        "FREQ=YEARLY;BYMONTH=11;BYDAY=4TH",
        "FREQ=YEARLY;BYWEEKNO=1,20;BYDAY=MO,TU",
        "FREQ=YEARLY;BYYEARDAY=1,100,-1",
        "FREQ=DAILY;UNTIL=20240229",
    ],
)
def test_rule_instance_bound_is_an_upper_bound(rule):
    from dateutil.rrule import rrulestr
    from icalendar import Calendar
    from family_calendar.service.providers import rule_instances

    start = datetime(2020, 1, 1, 9, tzinfo=timezone.utc)
    event = Calendar.from_ical(_series(rule, "20200101T090000Z")).walk("VEVENT")[0]
    text = rule.replace("UNTIL=20240229", "UNTIL=20240229T235959Z")
    actual = sum(1 for _ in rrulestr(text, dtstart=start).between(start, OCTOBER[1], inc=True))
    assert actual <= rule_instances(event, OCTOBER[1], "UTC")


def test_in_window_minutely_series_stops_at_event_limit(expansions):
    from family_calendar.service.providers import EVENT_LIMIT, normalize_icloud

    # 44 640 in-window instances fit the rule budget; expansion itself is cut off lazily.
    with pytest.raises(CalendarError, match="coverage_incomplete"):
        normalize_icloud("cal", _resource(_series("FREQ=MINUTELY")), *OCTOBER, "Asia/Makassar")
    assert len(expansions) == EVENT_LIMIT + 1


def test_bounded_dense_rules_and_long_running_series_still_expand():
    from family_calendar.service.providers import normalize_icloud

    def count(rule, dtstart):
        resource = _resource(_series(rule, dtstart))
        return len(normalize_icloud("cal", resource, *OCTOBER, "Asia/Makassar")["occurrences"])

    assert count("FREQ=SECONDLY;COUNT=10", "20261002T000000Z") == 10
    assert count("FREQ=MINUTELY;UNTIL=20250101T010000Z", "20250101T000000Z") == 0
    assert count("FREQ=WEEKLY;BYDAY=MO,WE", "20000103T080000Z") == 8


def test_hourly_series_fits_a_week_but_not_the_maximum_window():
    from family_calendar.service.providers import normalize_icloud

    resource = _resource(_series("FREQ=HOURLY", "20260101T000000Z"))
    week = (OCTOBER[0], OCTOBER[0] + timedelta(days=7))
    assert len(normalize_icloud("cal", resource, *week, "Asia/Makassar")["occurrences"]) == 168
    with pytest.raises(CalendarError, match="coverage_incomplete"):
        normalize_icloud("cal", resource, *OCTOBER, "Asia/Makassar")


def test_calendar_window_budget_is_shared_by_resources(expansions):
    from family_calendar.service.providers import EVENT_LIMIT, Providers

    class Adapter:
        async def read_window(self, collection, start, end):
            resources = [_resource(_series("FREQ=HOURLY;COUNT=300", uid=f"s{i}"), f"s{i}") for i in range(4)]
            return {"resources": resources}

    providers = Providers(None, None)
    providers.adapters["conn"] = Adapter()
    cal = {"connection_id": "conn", "provider": "icloud", "target": "t", "ref": "cal", "timezone": "UTC"}
    with pytest.raises(CalendarError, match="coverage_incomplete"):
        asyncio.run(providers.read_window(cal, *OCTOBER))
    assert len(expansions) == EVENT_LIMIT + 1


def test_caldav_window_is_sent_in_utc_whatever_the_session_zone():
    from zoneinfo import ZoneInfo
    from family_calendar.service.providers import Providers

    sent = []

    class Adapter:
        async def read_window(self, collection, start, end):
            sent.append((start, end))
            return {"resources": []}

    providers = Providers(None, None)
    providers.adapters["conn"] = Adapter()
    cal = {"connection_id": "conn", "provider": "icloud", "target": "t", "ref": "cal", "timezone": "UTC"}
    local = ZoneInfo("Asia/Makassar")
    window = (datetime(2026, 10, 19, 8, tzinfo=local), datetime(2026, 10, 20, 8, tzinfo=local))
    assert asyncio.run(providers.read_window(cal, *window)) == []
    assert sent == [("20261019T000000Z", "20261020T000000Z")]


def _event(times):
    return (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:point\r\n"
        + times
        + "SUMMARY:Reminder\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    ).encode()


DAY = (datetime(2026, 10, 19, tzinfo=timezone.utc), datetime(2026, 10, 20, tzinfo=timezone.utc))


@pytest.mark.parametrize(
    "times",
    [
        "DTSTART:20261019T080000Z\r\n",
        "DTSTART:20261019T080000Z\r\nDTEND:20261019T080000Z\r\nTRANSP:OPAQUE\r\n",
        "DTSTART:20261019T080000Z\r\nDURATION:PT0S\r\n",
    ],
)
def test_icloud_zero_length_event_is_a_non_blocking_instant(times):
    from family_calendar.service.providers import normalize_icloud

    [row] = normalize_icloud("cal", _resource(_event(times)), *DAY, "Asia/Makassar")["occurrences"]
    assert row["start"] == row["end"] == datetime(2026, 10, 19, 8, tzinfo=timezone.utc)
    assert row["busy"] is False
    assert row["details"]["instant"] is True


def test_instant_belongs_to_the_half_open_window_that_contains_it():
    from family_calendar.service.providers import normalize_icloud

    def rows(times):
        return normalize_icloud("cal", _resource(_event(times)), *DAY, "UTC")["occurrences"]

    assert len(rows("DTSTART:20261019T000000Z\r\n")) == 1
    assert rows("DTSTART:20261020T000000Z\r\n") == []
    assert rows("DTSTART:20261018T235959Z\r\n") == []


@pytest.mark.parametrize(
    "times",
    [
        "DTSTART:20261019T090000Z\r\nDTEND:20261019T080000Z\r\n",
        "DTSTART:20261019T090000Z\r\nDURATION:-PT1H\r\n",
        "DTSTART:20261019T090000Z\r\nDTEND:20261019T080000Z\r\nRRULE:FREQ=DAILY;COUNT=3\r\n",
    ],
)
def test_icloud_reversed_event_is_reported_as_provider_data(times):
    from family_calendar.service.providers import normalize_icloud

    with pytest.raises(CalendarError, match="invalid_provider_event_time"):
        normalize_icloud("cal", _resource(_event(times)), *DAY, "Asia/Makassar")


def test_google_zero_length_and_reversed_events():
    from family_calendar.service.providers import normalize_google

    event = {
        "id": "point",
        "etag": '"v1"',
        "summary": "Pay\x00ment \ud800due",
        "start": {"dateTime": "2026-10-19T08:00:00Z"},
        "end": {"dateTime": "2026-10-19T16:00:00+08:00"},
    }
    [row] = normalize_google("cal", event, "Asia/Makassar")["occurrences"]
    assert row["start"] == row["end"] and row["busy"] is False and row["details"]["instant"] is True
    assert row["details"]["title"] == "Payment ?due"
    event["end"] = {"dateTime": "2026-10-19T07:59:00Z"}
    with pytest.raises(CalendarError, match="invalid_provider_event_time"):
        normalize_google("cal", event, "Asia/Makassar")
