import pytest
from family_calendar.errors import CalendarError

SCOPES = [
    "agenda:read",
    "availability:read",
    "plan:create",
    "plan:execute",
    "event:create",
    "event:update",
    "event:delete",
]


def test_group_admin_cannot_see_ungranted_private_calendar(household):
    from family_calendar.service.core import FamilyService

    key = household.agent("admin-agent", "family", "adult-a", SCOPES, [])
    service = FamilyService(household)
    assert service.calendars(key)["calendars"] == []
    with pytest.raises(CalendarError, match="not_found_or_forbidden"):
        service.authorize(key, "cal-a", "agenda:read")


def test_busy_only_hides_titles_in_all_agenda_rows(household):
    from family_calendar.service.core import FamilyService

    household.grant("family", "adult-a", "cal-a", SCOPES, "details")
    key = household.agent("busy-agent", "family", "adult-a", SCOPES, ["cal-a"], "busy_only")
    household.publish_window(
        "cal-a",
        "2026-10-19T08:00:00Z",
        "2026-10-19T10:00:00Z",
        [
            {
                "resource_ref": "res-a",
                "target": "secret-id",
                "etag": '"v1"',
                "payload": {},
                "occurrences": [
                    {
                        "ref": "event-a",
                        "start": "2026-10-19T08:00:00Z",
                        "end": "2026-10-19T09:00:00Z",
                        "details": {"title": "PRIVATE CANARY", "location": "private-place"},
                        "busy": True,
                    }
                ],
            }
        ],
        "complete",
    )
    result = FamilyService(household).agenda(key, ["cal-a"], "2026-10-19T08:00:00Z", "2026-10-19T10:00:00Z")
    assert len(result["events"]) == 1
    assert "PRIVATE CANARY" not in str(result)
    assert "secret-id" not in str(result)
    assert "title" not in result["events"][0]


def test_unknown_required_source_never_yields_verified_free_slot(household):
    from family_calendar.service.core import FamilyService

    household.grant("family", "adult-a", "cal-a", SCOPES, "details")
    household.binding("family", "child", "cal-a")
    key = household.agent("agent", "family", "adult-a", SCOPES, ["cal-a"])
    result = FamilyService(household).slots(
        key, ["child"], "2026-10-19T08:00:00Z", "2026-10-19T10:00:00Z", 60
    )
    assert result["availability"] == "unknown"
    assert result["verified_slots"] == []


def test_grant_revocation_invalidates_prepared_plan(household):
    from family_calendar.service.core import FamilyService

    household.grant("family", "adult-a", "cal-a", SCOPES, "details")
    household.write_opt_in("cal-a", True)
    key = household.agent("agent", "family", "adult-a", SCOPES, ["cal-a"])
    service = FamilyService(household)
    plan = service.plan(
        key,
        [
            {
                "action": "create",
                "calendar_ref": "cal-a",
                "event": {
                    "title": "Test",
                    "start": "2026-10-19T08:00:00Z",
                    "end": "2026-10-19T09:00:00Z",
                    "timezone": "Asia/Makassar",
                },
            }
        ],
    )
    household.grant("family", "adult-a", "cal-a", [], "details")
    with pytest.raises(CalendarError, match="scope_changed|not_found_or_forbidden"):
        service.apply(key, plan["plan_id"], "request-1")


def test_same_key_cannot_enqueue_two_payloads(household):
    from family_calendar.service.core import FamilyService

    household.grant("family", "adult-a", "cal-a", SCOPES, "details")
    household.write_opt_in("cal-a", True)
    key = household.agent("agent", "family", "adult-a", SCOPES, ["cal-a"])
    service = FamilyService(household)
    action = {
        "action": "create",
        "calendar_ref": "cal-a",
        "event": {
            "title": "Test",
            "start": "2026-10-19T08:00:00Z",
            "end": "2026-10-19T09:00:00Z",
            "timezone": "Asia/Makassar",
        },
    }
    a = service.plan(key, [action])
    b = service.plan(key, [action])
    first = service.apply(key, a["plan_id"], "same-key")
    assert service.apply(key, a["plan_id"], "same-key")["operation_id"] == first["operation_id"]
    with pytest.raises(CalendarError, match="idempotency_conflict"):
        service.apply(key, b["plan_id"], "same-key")


def test_cross_group_receipt_is_not_found(household):
    from family_calendar.service.core import FamilyService

    household.group("other", "Other")
    household.member("other", "adult-b", "group_admin")
    key = household.agent("other-agent", "other", "adult-b", SCOPES, [])
    with pytest.raises(CalendarError, match="not_found_or_forbidden"):
        FamilyService(household).operation(key, "guessed-operation")


def test_assignment_cannot_claim_someone_elses_acceptance(household):
    from family_calendar.service.core import FamilyService

    key = household.agent("agent", "family", "adult-a", ["assignment:write"], [])
    with pytest.raises(CalendarError, match="confirmation_actor_required"):
        FamilyService(household).assignment(
            key, "adult-b", "pickup", "2026-10-19T08:00:00Z", "2026-10-19T08:30:00Z", "accepted"
        )


def test_availability_only_agent_can_list_explicit_calendars(household):
    from family_calendar.service.core import FamilyService

    household.grant("family", "adult-a", "cal-a", ["availability:read"], "busy_only")
    key = household.agent("busy", "family", "adult-a", ["availability:read"], ["cal-a"], "busy_only")
    result = FamilyService(household).calendars(key)
    assert len(result["calendars"]) == 1
    assert result["calendars"][0]["visibility"] == "busy_only"


def test_calendar_list_intersects_connection_member_and_agent_scopes(household):
    from family_calendar.service.core import FamilyService

    household.connection("disabled", "adult-a", "google")
    household.query("UPDATE connections SET enabled=false WHERE id='disabled'")
    for ref, conn in [("disabled-cal", "disabled"), ("ungranted-cal", "google-a")]:
        household.calendar(ref, conn, ref, "Private", "UTC", "write")
    household.grant("family", "adult-a", "cal-a", ["availability:read"], "details")
    household.grant("family", "adult-a", "disabled-cal", ["availability:read"], "details")
    key = household.agent(
        "busy",
        "family",
        "adult-a",
        ["availability:read"],
        ["cal-a", "disabled-cal", "ungranted-cal"],
        "busy_only",
    )
    rows = FamilyService(household).calendars(key)["calendars"]
    assert len(rows) == 1 and rows[0]["calendar_ref"] == "cal-a" and rows[0]["label"] == "Calendar"


def test_assignment_can_be_listed_and_cancelled_without_creating_duplicate(household):
    from family_calendar.service.core import FamilyService

    key = household.agent("agent", "family", "adult-a", ["assignment:read", "assignment:write"], [])
    service = FamilyService(household)
    value = service.assignment(
        key, "adult-a", "pickup", "2026-10-19T08:00:00Z", "2026-10-19T08:30:00Z", "assigned"
    )
    assert len(service.assignments(key, "2026-10-19T00:00:00Z", "2026-10-20T00:00:00Z")["assignments"]) == 1
    service.assignment(
        key,
        "adult-a",
        "pickup",
        "2026-10-19T08:00:00Z",
        "2026-10-19T08:30:00Z",
        "cancelled",
        value["assignment_id"],
        1,
    )
    assert household.one("SELECT count(*) AS count FROM assignments")["count"] == 1
    assert household.one("SELECT status,version FROM assignments") == {"status": "cancelled", "version": 2}
    with pytest.raises(CalendarError, match="version_conflict"):
        service.assignment(
            key,
            "adult-a",
            "pickup",
            "2026-10-19T08:00:00Z",
            "2026-10-19T08:30:00Z",
            "assigned",
            value["assignment_id"],
            1,
        )


def test_assignment_from_another_group_cannot_be_cancelled(household):
    from family_calendar.service.core import FamilyService

    a = household.agent("a", "family", "adult-a", ["assignment:read", "assignment:write"], [])
    household.group("other", "Other")
    household.member("other", "adult-b", "group_admin")
    b = household.agent("b", "other", "adult-b", ["assignment:write"], [])
    service = FamilyService(household)
    assignment = service.assignment(b, "adult-b", "pickup", "2026-10-19T08:00:00Z", "2026-10-19T08:30:00Z")
    assert service.assignments(a, "2026-10-19T00:00:00Z", "2026-10-20T00:00:00Z")["assignments"] == []
    with pytest.raises(CalendarError, match="not_found_or_forbidden"):
        service.assignment(
            a,
            "adult-a",
            "pickup",
            "2026-10-19T08:00:00Z",
            "2026-10-19T08:30:00Z",
            "cancelled",
            assignment["assignment_id"],
            1,
        )
