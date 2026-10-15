from family_calendar.service.core import FamilyService
from tests.service.test_policy import SCOPES

START = "2026-10-19T08:00:00Z"
END = "2026-10-19T12:00:00Z"


def setup(household):
    household.grant("family", "adult-a", "cal-a", SCOPES, "details")
    household.write_opt_in("cal-a", True)
    return FamilyService(household), household.agent("reliability", "family", "adult-a", SCOPES, ["cal-a"])


def publish(
    store, ref="cal-a", *, busy=True, start=START, end="2026-10-19T09:00:00Z", title="Private canary"
):
    store.publish_window(
        ref,
        START,
        END,
        [
            {
                "resource_ref": "res-" + ref,
                "target": "provider-private-" + ref,
                "etag": '"v1"',
                "payload": {},
                "occurrences": [
                    {
                        "ref": "event-" + ref,
                        "start": start,
                        "end": end,
                        "details": {"title": title, "timezone": "UTC"},
                        "busy": busy,
                    }
                ],
            }
        ],
        "complete",
    )


def create(start=START, end="2026-10-19T09:00:00Z"):
    return {
        "action": "create",
        "calendar_ref": "cal-a",
        "event": {"title": "Proposed", "start": start, "end": end, "timezone": "UTC"},
    }


def test_member_slots_declare_checked_bindings_without_claiming_whole_person(household):
    service, key = setup(household)
    household.binding("family", "adult-a", "cal-a")
    publish(household)
    value = service.slots(key, ["adult-a"], START, END, 30)
    assert value["availability_scope"] == "configured_bindings"
    assert value["member_sources_complete"] == "unknown"
    assert value["checked_member_sources"] == [{"member_id": "adult-a", "calendar_refs": ["cal-a"]}]
    assert value["availability"] == "unknown"
    assert value["configured_bindings_availability"] == "verified"
    assert value["verified_slots"] == []
    assert value["candidate_slots"] == [
        {"start": "2026-10-19T09:00:00+00:00", "end": "2026-10-19T12:00:00+00:00"}
    ]


def test_plan_reports_overlaps_and_explicit_diff(household):
    service, key = setup(household)
    publish(household)
    result = service.plan(key, [create()])
    step = result["steps"][0]
    assert step["diff"]["start"]["before"] is None
    assert step["diff"]["title"]["after"] == "Proposed"
    assert result["overlap_check"]["status"] == "complete_within_visible_calendars"
    assert result["overlap_check"]["overlaps"][0]["event_ref"] == "event-cal-a"


def test_plan_half_open_intervals_and_transparent_events(household):
    service, key = setup(household)
    publish(household)
    assert (
        service.plan(key, [create("2026-10-19T09:00:00Z", "2026-10-19T10:00:00Z")])["overlap_check"][
            "overlaps"
        ]
        == []
    )
    publish(household, busy=False)
    assert service.plan(key, [create()])["overlap_check"]["overlaps"] == []


def test_plan_update_excludes_own_occurrence_and_shows_changed_fields(household):
    service, key = setup(household)
    publish(household)
    action = dict(create(), action="update", event_ref="event-cal-a")
    result = service.plan(key, [action])
    assert result["overlap_check"]["overlaps"] == []
    assert result["steps"][0]["diff"] == {"title": {"before": "Private canary", "after": "Proposed"}}


def test_plan_multi_action_removes_deleted_and_reports_proposed_overlap(household):
    service, key = setup(household)
    publish(household)
    actions = [{"action": "delete", "calendar_ref": "cal-a", "event_ref": "event-cal-a"}, create(), create()]
    result = service.plan(key, actions)
    assert all("event_ref" not in row for row in result["overlap_check"]["overlaps"])
    assert result["overlap_check"]["overlaps"] == [{"step": 1, "other_step": 2, "kind": "proposed_overlap"}]


def test_plan_missing_coverage_is_explicitly_incomplete(household):
    service, key = setup(household)
    result = service.plan(key, [create()])
    assert result["overlap_check"]["status"] == "incomplete"
    assert result["overlap_check"]["coverage"][0]["status"] == "unknown"


def test_plan_busy_only_overlap_never_reveals_event_identity(household):
    service, _ = setup(household)
    household.calendar("cal-b", "google-a", "private-b", "Hidden title", "UTC", "write")
    household.grant("family", "adult-a", "cal-b", ["availability:read"], "busy_only")
    key = household.agent("busy-overlap", "family", "adult-a", SCOPES, ["cal-a", "cal-b"])
    publish(household)
    publish(household, "cal-b")
    result = service.plan(key, [create()])
    hidden = [row for row in result["overlap_check"]["overlaps"] if row.get("calendar_ref") == "cal-b"]
    assert hidden and hidden[0]["visibility"] == "busy_only"
    assert "Private canary" not in str(hidden)
    assert "event-cal-b" not in str(hidden) and "provider-private-cal-b" not in str(result)


def test_calendar_identity_does_not_infer_duplicates_from_labels(household):
    service, key = setup(household)
    row = service.calendars(key)["calendars"][0]
    assert row["identity_basis"] == "provider_resource"
    assert row["primary"] is None and row["duplicate_of"] is None


def test_large_cached_calendar_keeps_plan_valid_but_overlap_check_incomplete(household):
    service, key = setup(household)
    records = [
        {
            "resource_ref": "res-" + str(i),
            "target": "provider-" + str(i),
            "etag": '"v1"',
            "payload": {},
            "occurrences": [
                {
                    "ref": "event-" + str(i),
                    "start": START,
                    "end": "2026-10-19T09:00:00Z",
                    "details": {"title": "Cached", "timezone": "UTC"},
                    "busy": True,
                }
            ],
        }
        for i in range(501)
    ]
    household.publish_window("cal-a", START, END, records, "complete")
    result = service.plan(key, [create()])
    assert result["plan_id"]
    assert result["overlap_check"]["status"] == "incomplete"
    assert result["overlap_check"]["reason"] == "event_limit"
