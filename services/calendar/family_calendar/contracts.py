"""Version 1 pilot contract; independent of the later HTTP/MCP gateway."""

from dataclasses import dataclass
from typing import Literal
import re

from .errors import CalendarError

SCHEMA_VERSION = 1
Scope = Literal["single", "future", "series"]
ReceiptState = Literal["verified", "conflict", "unknown", "failed", "needs_attention"]


@dataclass(frozen=True)
class EventRef:
    connection_id: str
    calendar_id: str
    resource_id: str
    occurrence_key: str | None = None

    def __post_init__(self):
        if any(
            not isinstance(x, str) or not x or len(x) > 2048
            for x in (self.connection_id, self.calendar_id, self.resource_id)
        ):
            raise CalendarError("invalid_event_ref")


@dataclass(frozen=True)
class WriteReceipt:
    state: ReceiptState
    ref: EventRef
    version: str | None = None
    semantic_hash: str | None = None
    error_code: str | None = None

    def __post_init__(self):
        if self.state not in {"verified", "conflict", "unknown", "failed", "needs_attention"}:
            raise CalendarError("invalid_operation_state")
        # For a verified deletion semantic_hash is "absent" and version may be None.
        if self.state == "verified" and not self.semantic_hash:
            raise CalendarError("unverified_receipt")


def validate_intent(intent):
    kind = intent.get("kind")
    if kind not in {"create", "update", "delete"}:
        raise CalendarError("invalid_operation_kind")
    if not isinstance(intent.get("href"), str) or not intent["href"]:
        raise CalendarError("invalid_event_ref")
    if kind != "delete" and (
        not isinstance(intent.get("desired"), bytes) or not 0 < len(intent["desired"]) <= 1_048_576
    ):
        raise CalendarError("invalid_calendar_data")
    if kind != "create" and not re.fullmatch(r'"[^"\r\n]+"', intent.get("etag") or ""):
        raise CalendarError("conditional_write_unavailable")
