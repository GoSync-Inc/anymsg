from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

import pytest
from icalendar import Calendar

from family_calendar.providers.caldav.codec import (
    create_event,
    semantic_snapshot,
    shift_series,
    move_single,
    cancel_single,
    split_future,
    occurrences,
    native_future,
)
from family_calendar.errors import CalendarError


START = datetime(2026, 10, 19, 16, tzinfo=ZoneInfo("Europe/Berlin"))


def weekly(count=6):
    return create_event(
        "test@family.invalid",
        START,
        START + timedelta(hours=1),
        "Synthetic lesson",
        rrule={"FREQ": "WEEKLY", "COUNT": count},
    )


def test_roundtrip_preserves_alarms_unknown_properties_and_timezone():
    data = weekly().replace(
        b"END:VEVENT",
        b"X-FAMILY-OPAQUE;X-MODE=keep:opaque-value\r\n"
        b"BEGIN:VALARM\r\nACTION:DISPLAY\r\nTRIGGER:-PT15M\r\n"
        b"DESCRIPTION:Synthetic alarm\r\nEND:VALARM\r\nEND:VEVENT",
    )
    changed = shift_series(data, timedelta(hours=1))
    before, after = Calendar.from_ical(data), Calendar.from_ical(changed)
    old, new = before.walk("VEVENT")[0], after.walk("VEVENT")[0]
    assert new.decoded("X-FAMILY-OPAQUE") == old.decoded("X-FAMILY-OPAQUE")
    assert new["X-FAMILY-OPAQUE"].params == old["X-FAMILY-OPAQUE"].params
    assert new.subcomponents[0].to_ical() == old.subcomponents[0].to_ical()
    assert new["DTSTART"].params["TZID"] == "Europe/Berlin"


def test_single_move_retains_original_identity_and_rest_of_series():
    rid = START + timedelta(weeks=1)
    data = move_single(weekly(), rid, rid + timedelta(hours=2), rid + timedelta(hours=3))
    values = occurrences(data, START - timedelta(days=1), START + timedelta(weeks=7))
    assert len(values) == 6
    assert values[1]["start"] == rid + timedelta(hours=2)
    assert values[1]["identity"] == rid
    assert values[2]["start"].hour == 16


def test_cancelled_instance_stays_cancelled_after_series_shift():
    rid = START + timedelta(weeks=1)
    moved = START + timedelta(weeks=2)
    data = cancel_single(weekly(), rid)
    data = move_single(data, moved, moved + timedelta(hours=2), moved + timedelta(hours=3))
    changed = shift_series(data, timedelta(hours=1))
    values = occurrences(changed, START - timedelta(days=1), START + timedelta(weeks=7))
    assert len(values) == 5
    assert all(x["start"] != rid + timedelta(hours=1) for x in values)
    assert next(x for x in values if x["start"].hour == 18)["start"] == moved + timedelta(hours=2)


@pytest.mark.parametrize("bound_index", [0, 1, 3, 5])
def test_future_split_preserves_count_and_absolute_exception(bound_index):
    data = weekly()
    rid = START + timedelta(weeks=4)
    data = move_single(data, rid, rid + timedelta(hours=2), rid + timedelta(hours=3))
    data = cancel_single(data, START + timedelta(weeks=2))
    boundary = START + timedelta(weeks=bound_index)
    past, future, mapping = split_future(data, boundary, timedelta(hours=1), "new@family.invalid")
    lower, upper = START - timedelta(days=1), START + timedelta(weeks=8)
    values = (occurrences(past, lower, upper) if past else []) + occurrences(future, lower, upper)
    assert len(values) == 5
    assert any(x["start"] == rid + timedelta(hours=2) for x in values)
    assert len({x["start"] for x in values}) == 5
    assert mapping


def test_infinite_future_split_remains_rule_not_many_events():
    data = create_event(
        "infinite@family.invalid", START, START + timedelta(hours=1), "Weekly", rrule={"FREQ": "WEEKLY"}
    )
    past, future, _ = split_future(
        data, START + timedelta(weeks=3), timedelta(hours=1), "next@family.invalid"
    )
    assert len(Calendar.from_ical(future).walk("VEVENT")) == 1
    assert "COUNT" not in Calendar.from_ical(future).walk("VEVENT")[0]["RRULE"]
    assert len(occurrences(past, START, START + timedelta(weeks=20))) == 3


def test_date_all_day_end_exclusive():
    from datetime import date

    data = create_event("date@family.invalid", date(2026, 10, 19), date(2026, 10, 20), "All day")
    snap = semantic_snapshot(data)
    assert snap["events"][0]["DTEND"]["value"] == "2026-10-20"


def test_dst_weekly_keeps_wall_clock_time():
    values = occurrences(weekly(), START - timedelta(days=1), START + timedelta(weeks=7))
    assert {x["start"].hour for x in values} == {16}
    assert values[0]["start"].utcoffset() != values[1]["start"].utcoffset()


def test_complex_future_rule_fails_explicitly():
    data = create_event(
        "complex@family.invalid",
        START,
        START + timedelta(hours=1),
        "Complex",
        rrule={"FREQ": "MONTHLY", "BYDAY": "MO", "BYSETPOS": -1},
    )
    with pytest.raises(CalendarError, match="unsupported_recurrence"):
        split_future(data, START, timedelta(hours=1), "new@family.invalid")


def test_native_future_keeps_recurrence_id_before_shift():
    rid = START + timedelta(weeks=1)
    data = native_future(weekly(), rid, timedelta(hours=1))
    exception = Calendar.from_ical(data).walk("VEVENT")[1]
    assert exception.decoded("RECURRENCE-ID") == rid
    assert exception["RECURRENCE-ID"].params["RANGE"] == "THISANDFUTURE"


def test_future_partition_preserves_rdate_exdate_and_until():
    until = START + timedelta(weeks=5)
    data = create_event(
        "until@family.invalid",
        START,
        START + timedelta(hours=1),
        "Until",
        rrule={"FREQ": "WEEKLY", "UNTIL": until.astimezone(ZoneInfo("UTC"))},
    )
    cal = Calendar.from_ical(data)
    event = cal.walk("VEVENT")[0]
    event.add("EXDATE", [START + timedelta(weeks=1)])
    event.add("RDATE", [START + timedelta(weeks=3, days=1)])
    data = cal.to_ical()
    past, future, _ = split_future(
        data, START + timedelta(weeks=3), timedelta(hours=1), "next@family.invalid"
    )
    lower, upper = START - timedelta(days=1), START + timedelta(weeks=8)
    vals = occurrences(past, lower, upper) + occurrences(future, lower, upper)
    assert len(vals) == 6
    assert max(x["start"] for x in vals) == until + timedelta(hours=1)


def test_nonexistent_dst_local_time_is_rejected():
    start = datetime(2026, 3, 29, 1, 30, tzinfo=ZoneInfo("Europe/Berlin"))
    data = create_event("gap@family.invalid", start, start + timedelta(minutes=20), "Gap")
    with pytest.raises(CalendarError, match="invalid_local_time"):
        shift_series(data, timedelta(hours=1))


def test_single_move_to_utc_does_not_keep_stale_tzid():
    rid = START + timedelta(weeks=1)
    changed = move_single(
        weekly(), rid, rid.astimezone(ZoneInfo("UTC")), (rid + timedelta(hours=1)).astimezone(ZoneInfo("UTC"))
    )
    event = Calendar.from_ical(changed).walk("VEVENT")[1]
    assert "TZID" not in event["DTSTART"].params
    assert event.decoded("DTSTART") == rid
    assert event["RECURRENCE-ID"].params["TZID"] == "Europe/Berlin"


def test_weekly_byday_shift_across_weekday_is_explicitly_rejected():
    data = create_event(
        "byday@family.invalid",
        START,
        START + timedelta(hours=1),
        "Weekly",
        rrule={"FREQ": "WEEKLY", "BYDAY": "MO", "COUNT": 6},
    )
    with pytest.raises(CalendarError, match="unsupported_recurrence"):
        shift_series(data, timedelta(days=1))


@pytest.mark.parametrize("name", ["weekly-exceptions.ics", "all-day.ics", "floating.ics"])
def test_saved_synthetic_fixture_preserves_full_semantics_on_roundtrip(name):
    from family_calendar.providers.caldav.codec import semantic_hash

    data = (Path(__file__).parents[1] / "fixtures" / "ics" / name).read_bytes()
    assert semantic_hash(data) == semantic_hash(Calendar.from_ical(data).to_ical())


@pytest.mark.parametrize("zone", ["Asia/Makassar", "Europe/Berlin"])
def test_equivalent_timezone_history_does_not_make_readback_unknown(zone):
    from datetime import date
    from dateutil.tz import gettz
    from icalendar import Timezone
    from family_calendar.providers.caldav.codec import semantic_hash

    start = START.astimezone(ZoneInfo(zone))
    desired = create_event("zone@family.invalid", start, start + timedelta(hours=1), "Synthetic")
    observed = Calendar.from_ical(desired)
    observed.subcomponents = [c for c in observed.subcomponents if c.name != "VTIMEZONE"]
    full = Timezone.from_tzinfo(gettz(zone), tzid=zone, first_date=date(1900, 1, 1), last_date=date(2040, 1, 1))
    full.pop("COMMENT", None)
    full.add("X-LIC-LOCATION", zone)
    observed.add_component(full)
    assert semantic_hash(desired) == semantic_hash(observed.to_ical())


def test_timezone_same_id_with_changed_dst_is_not_equal():
    from family_calendar.providers.caldav.codec import semantic_hash

    desired = weekly()
    observed = Calendar.from_ical(desired)
    daylight = observed.walk("DAYLIGHT")[0]
    daylight["TZOFFSETTO"].td += timedelta(minutes=30)
    assert semantic_hash(desired) != semantic_hash(observed.to_ical())


def test_event_extension_and_alarm_loss_are_not_timezone_normalization():
    from family_calendar.providers.caldav.codec import semantic_hash

    desired = weekly().replace(b"END:VEVENT", b"X-PRIVATE:keep\r\nEND:VEVENT")
    assert semantic_hash(desired) != semantic_hash(weekly())


def test_timezone_normalization_has_an_explicit_bounded_scope():
    from family_calendar.providers.caldav.codec import semantic_snapshot

    snap = semantic_snapshot(weekly())["calendar"]
    tz = next(c for c in snap["components"] if c["type"] == "VTIMEZONE")
    assert tz["offset_scope"] == ["2020-01-01", "2035-01-01"]


def test_generated_timezone_switches_at_the_actual_utc_dst_boundary():
    from datetime import timezone

    tz = Calendar.from_ical(weekly()).walk("VTIMEZONE")[0].to_tz(lookup_tzid=False)
    for instant, offset in [
        (datetime(2026, 3, 29, 0, 30, tzinfo=timezone.utc), 1),
        (datetime(2026, 3, 29, 1, 30, tzinfo=timezone.utc), 2),
        (datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc), 2),
        (datetime(2026, 10, 25, 1, 30, tzinfo=timezone.utc), 1),
    ]:
        assert instant.astimezone(tz).utcoffset() == timedelta(hours=offset)


def test_unknown_timezone_extension_is_preserved_in_comparison():
    from family_calendar.providers.caldav.codec import semantic_hash

    desired = weekly()
    observed = Calendar.from_ical(desired)
    observed.walk("VTIMEZONE")[0].add("X-OPAQUE-ZONE", "keep")
    assert semantic_hash(desired) != semantic_hash(observed.to_ical())
