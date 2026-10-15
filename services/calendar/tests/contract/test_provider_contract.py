import pytest

from family_calendar.contracts import EventRef, WriteReceipt, validate_intent
from family_calendar.errors import CalendarError
from family_calendar.providers.base import CalendarProvider


def test_receipt_requires_observation_and_preserves_account_scope():
    ref = EventRef("parent-a", "calendar-a", "opaque-resource")
    with pytest.raises(CalendarError, match="unverified_receipt"):
        WriteReceipt("verified", ref)
    receipt = WriteReceipt("verified", ref, version='"v1"', semantic_hash="abc")
    assert receipt.ref.connection_id == "parent-a"
    assert receipt.ref.calendar_id == "calendar-a"
    assert receipt.state == "verified"
    with pytest.raises(CalendarError, match="invalid_event_ref"):
        EventRef("", "calendar-a", "resource")


def test_mutation_contract_rejects_unconditional_update_before_network():
    with pytest.raises(CalendarError, match="conditional_write_unavailable"):
        validate_intent(
            {
                "kind": "update",
                "href": "https://caldav.icloud.com/a/c/e.ics",
                "desired": b"event",
                "etag": None,
            }
        )
    with pytest.raises(CalendarError, match="invalid_operation_kind"):
        validate_intent({"kind": "invite", "href": "https://caldav.icloud.com/a/c/e.ics"})


def test_adapter_is_account_bound_and_implements_pilot_provider_contract():
    from family_calendar.providers.caldav.adapter import CalDAVAdapter

    adapter = CalDAVAdapter("parent-b", object())
    assert isinstance(adapter, CalendarProvider)
    assert adapter.connection_id == "parent-b"
