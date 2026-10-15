import asyncio
import base64
from datetime import date, datetime, time, timedelta, timezone
import json
from zoneinfo import ZoneInfo

import httpx
from icalendar import Calendar
import recurring_ical_events
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google.auth.exceptions import RefreshError
from google.oauth2 import id_token

from family_calendar.errors import CalendarError
from family_calendar.providers.caldav.adapter import CalDAVAdapter
from family_calendar.providers.caldav.transport import DAVTransport, within_collection
from family_calendar.providers.caldav.codec import create_event, parse, master, _set, _touch, semantic_hash
from family_calendar.providers.google.adapter import GoogleAdapter
from .store import digest, instant

# Occurrences published for one calendar window; the same limit as agenda/slots responses.
EVENT_LIMIT = 500
# dateutil (behind recurring_ical_events) walks and caches every rule instance from
# DTSTART up to the window end, even for a narrow window. Bound that work before
# expansion: per resource for memory, per calendar window for CPU.
RULE_INSTANCE_LIMIT = 100_000
CALENDAR_RULE_INSTANCE_LIMIT = 1_000_000
_FREQ_SECONDS = {
    "SECONDLY": 1,
    "MINUTELY": 60,
    "HOURLY": 3600,
    "DAILY": 86400,
    "WEEKLY": 7 * 86400,
    "MONTHLY": 28 * 86400,
    "YEARLY": 365 * 86400,
}
_DAYS_PER_PERIOD = {"WEEKLY": 7, "MONTHLY": 31, "YEARLY": 366}


def source_ref(prefix, *parts):
    return prefix + "-" + digest(parts)[:32]


def ical_time(value, zone):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise CalendarError("coverage_incomplete")
        return value.astimezone(timezone.utc)
    if isinstance(value, date):
        return datetime.combine(value, time.min, ZoneInfo(zone)).astimezone(timezone.utc)
    raise CalendarError("coverage_incomplete")


def provider_text(value, limit):
    # PostgreSQL text/jsonb rejects NUL and unpaired surrogates.
    return str(value).encode("utf-8", "replace").decode().replace("\x00", "")[:limit]


def ends_before_start(event, start, zone):
    # recurring_ical_events silently swaps a reversed DTEND or negative DURATION.
    if "DTEND" not in event:
        return "DURATION" in event and event.decoded("DURATION") < timedelta(0)
    finish = event.decoded("DTEND")
    floating = isinstance(finish, datetime) and finish.tzinfo is None
    return not floating and ical_time(finish, zone) < start


def expansion_budget():
    """Shared by all resources of one calendar window."""
    return {"occurrences": EVENT_LIMIT, "rule_instances": CALENDAR_RULE_INSTANCE_LIMIT}


def rule_instances(event, end, zone):
    """Upper bound of RRULE instances from DTSTART until end, computed without generating them."""
    start = ical_time(event.decoded("DTSTART"), zone)
    rules = event.get("RRULE", [])
    total = 0
    for rule in rules if isinstance(rules, list) else [rules]:
        freq = rule.get("FREQ", [None])[0]
        if freq not in _FREQ_SECONDS:
            raise CalendarError("coverage_incomplete")
        stop = end
        if rule.get("UNTIL"):
            until = rule["UNTIL"][0]
            if not isinstance(until, datetime):
                until += timedelta(days=1)  # a DATE includes its whole day
            elif until.tzinfo is None:
                until = until.replace(tzinfo=ZoneInfo(zone))
            stop = min(stop, ical_time(until, zone))
        step = _FREQ_SECONDS[freq] * max(int(rule.get("INTERVAL", [1])[0]), 1)
        count = max(int((stop - start).total_seconds() // step), 0) + 1
        # BYxxx parts finer than FREQ expand every period; coarser parts only filter.
        for part, seconds in (("BYSECOND", 1), ("BYMINUTE", 60), ("BYHOUR", 3600)):
            if rule.get(part) and _FREQ_SECONDS[freq] > seconds:
                count *= len(rule[part])
        if freq == "WEEKLY" and rule.get("BYDAY"):
            count *= min(len(rule["BYDAY"]), 7)
        elif freq in _DAYS_PER_PERIOD and any(
            rule.get(part) for part in ("BYDAY", "BYMONTHDAY", "BYYEARDAY", "BYWEEKNO", "BYMONTH")
        ):
            count *= _DAYS_PER_PERIOD[freq]
        if rule.get("COUNT"):
            count = min(count, int(rule["COUNT"][0]))
        total += count
    return total


def expand(cal, start, end):
    # CalendarQuery.between() copies a component for every occurrence before the caller
    # can count them; Series.between() is a generator, so the caller's limit stops it.
    for series in recurring_ical_events.of(cal).series:
        for occurrence in series.between(start, end):
            yield occurrence.as_component(False)


def normalize_icloud(calendar_ref, resource, start, end, zone, budget=None):
    budget = expansion_budget() if budget is None else budget
    try:
        cal = Calendar.from_ical(resource["data"])
        original = cal.walk("VEVENT")
        recurring = any("RRULE" in e or "RECURRENCE-ID" in e or "RDATE" in e for e in original)
        occurrences = []
        resource_ref = source_ref("res", calendar_ref, resource["href"])
        # Refuse unspecified floating time rather than assume server/user timezone.
        for e in original:
            if "DTSTART" in e and ends_before_start(e, ical_time(e.decoded("DTSTART"), zone), zone):
                raise CalendarError("invalid_provider_event_time")
        # Dense (MINUTELY/SECONDLY) or long-running rules are incomplete, never expanded.
        instances = sum(rule_instances(e, end, zone) for e in original if "RRULE" in e)
        budget["rule_instances"] -= instances
        if instances > RULE_INSTANCE_LIMIT or budget["rule_instances"] < 0:
            raise CalendarError("coverage_incomplete")
        for e in expand(cal, start, end):
            if str(e.get("STATUS", "")).upper() == "CANCELLED":
                continue
            a = ical_time(e.decoded("DTSTART"), zone)
            finish = (
                e.decoded("DTEND")
                if "DTEND" in e
                else e.decoded("DTSTART") + e.decoded("DURATION", timedelta(days=1))
            )
            b = ical_time(finish, zone)
            if b < a:
                raise CalendarError("invalid_provider_event_time")
            # A zero-length event (e.g. DTSTART without DTEND) is a non-blocking instant.
            if a >= end or (b <= start if b > a else a < start):
                continue
            identity = (e.get("RECURRENCE-ID") or e["DTSTART"]).to_ical().decode() if recurring else "master"
            occurrences.append(
                {
                    "ref": source_ref("event", resource_ref, str(e["UID"]), identity),
                    "start": a,
                    "end": b,
                    "busy": b > a and str(e.get("TRANSP", "OPAQUE")).upper() != "TRANSPARENT",
                    "details": {
                        "title": provider_text(e.get("SUMMARY", ""), 256),
                        "location": provider_text(e.get("LOCATION", ""), 512),
                        "description": provider_text(e.get("DESCRIPTION", ""), 4000),
                        "recurring": recurring,
                        "guests": "ATTENDEE" in e or "ORGANIZER" in e,
                        "all_day": not isinstance(e.decoded("DTSTART"), datetime),
                        "instant": a == b,
                    },
                }
            )
            if len(occurrences) > budget["occurrences"]:
                raise CalendarError("coverage_incomplete")
        budget["occurrences"] -= len(occurrences)
        return {
            "resource_ref": resource_ref,
            "target": resource["href"],
            "etag": resource["etag"],
            "payload": {"ical": base64.b64encode(resource["data"]).decode()},
            "occurrences": occurrences,
        }
    except CalendarError:
        raise
    except Exception:
        raise CalendarError("coverage_incomplete") from None


def normalize_google(calendar_ref, e, zone):
    resource_ref = source_ref("res", calendar_ref, e["id"])
    rows = []
    if e.get("status") != "cancelled" and "start" in e and "end" in e:

        def point(value):
            return (
                instant(value["dateTime"])
                if "dateTime" in value
                else ical_time(date.fromisoformat(value["date"]), zone)
            )

        a, b = point(e["start"]), point(e["end"])
        if b < a:
            raise CalendarError("invalid_provider_event_time")
        identity = e.get("originalStartTime", "master")
        rows.append(
            {
                "ref": source_ref("event", resource_ref, identity),
                "start": a,
                "end": b,
                # A zero-length event is a non-blocking instant.
                "busy": b > a and e.get("transparency") != "transparent",
                "details": {
                    "title": provider_text(e.get("summary", ""), 256),
                    "location": provider_text(e.get("location", ""), 512),
                    "description": provider_text(e.get("description", ""), 4000),
                    "recurring": bool(e.get("recurringEventId") or e.get("recurrence")),
                    "guests": bool(e.get("attendees")),
                    "special": e.get("eventType", "default") != "default",
                    "all_day": "date" in e["start"],
                    "instant": a == b,
                },
            }
        )
    return {
        "resource_ref": resource_ref,
        "target": e["id"],
        "etag": e.get("etag"),
        "payload": e,
        "occurrences": rows,
    }


class Providers:
    """Only the worker has this object and access to provider secrets."""

    def __init__(self, store, vault):
        self.store, self.vault = store, vault
        self.adapters = {}
        self.transports = []

    def _token(self, connection_id):
        # A DB advisory lock serializes refresh across processes. Read the latest encrypted credentials under it.
        with self.store.transaction() as c:
            c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("oauth:" + connection_id,))
            saved = self.vault.load(connection_id)
            credentials = Credentials.from_authorized_user_info(saved["credentials"])
            if credentials.token_uri not in (
                "https://oauth2.googleapis.com/token",
                "https://accounts.google.com/o/oauth2/token",
            ):
                raise CalendarError("unsafe_token_endpoint")
            if not credentials.valid:
                try:
                    credentials.refresh(Request())
                    if credentials.id_token:
                        claims = id_token.verify_oauth2_token(
                            credentials.id_token, Request(), credentials.client_id
                        )
                        if claims["sub"] != saved["sub"]:
                            raise CalendarError("oauth_identity_mismatch")
                    saved["credentials"] = json.loads(credentials.to_json())
                    self.vault.save(connection_id, saved)
                except RefreshError:
                    raise CalendarError("reauth_required") from None
            return credentials.token

    async def adapter(self, cal):
        connection_id = cal["connection_id"]
        if connection_id not in self.adapters:
            if cal["provider"] == "icloud":
                secret = self.vault.load(connection_id)
                transport = DAVTransport(secret["username"], secret["password"])
                self.transports.append(transport)
                adapter = CalDAVAdapter(connection_id, transport)
            else:
                client = httpx.AsyncClient(trust_env=False, follow_redirects=False)
                self.transports.append(client)
                adapter = GoogleAdapter(
                    connection_id, client, lambda: asyncio.to_thread(self._token, connection_id)
                )
            self.adapters[connection_id] = adapter
        return self.adapters[connection_id]

    async def close(self):
        for transport in self.transports:
            await (transport.close() if isinstance(transport, DAVTransport) else transport.aclose())

    async def read_window(self, cal, start, end):
        adapter = await self.adapter(cal)
        if cal["provider"] == "icloud":
            result = await adapter.read_window(
                cal["target"], start.strftime("%Y%m%dT%H%M%SZ"), end.strftime("%Y%m%dT%H%M%SZ")
            )
            budget = expansion_budget()
            rows = [
                normalize_icloud(cal["ref"], r, start, end, cal["timezone"], budget)
                for r in result["resources"]
            ]
        else:
            rows = [
                normalize_google(cal["ref"], e, cal["timezone"])
                for e in await adapter.window(cal["target"], start.isoformat(), end.isoformat())
            ]
        if sum(len(r["occurrences"]) for r in rows) > EVENT_LIMIT:
            raise CalendarError("coverage_incomplete")
        for r in rows:
            r["payload"] = {
                "encrypted_v1": self.vault._cipher().encrypt(json.dumps(r["payload"]).encode()).decode()
            }
        return rows

    async def read(self, cal, target):
        adapter = await self.adapter(cal)
        if cal["provider"] == "icloud":
            if not within_collection(target, cal["target"]):
                raise CalendarError("provider_resource_outside_calendar")
            return await adapter.read(target)
        return await adapter.read(cal["target"], target)

    async def prepare(self, cal, step):
        target = step["target_id"]
        event = step.get("event", {})
        if step["action"] == "create" and cal["provider"] == "icloud":
            target = cal["target"].rstrip("/") + "/" + target + ".ics"
        old = await self.read(cal, target)
        if step["action"] == "create" and old:
            raise CalendarError("version_conflict")
        if step["action"] != "create":
            if not old or old.get("etag") != step["expected_etag"]:
                raise CalendarError("version_conflict")
            if cal["provider"] == "google":
                if (
                    old.get("recurrence")
                    or old.get("recurringEventId")
                    or old.get("attendees")
                    or old.get("eventType", "default") != "default"
                ):
                    raise CalendarError("capability_unverified")
            else:
                original = parse(old["data"])
                e = master(original)
                if len(original.walk("VEVENT")) != 1 or any(
                    k in e for k in ("RRULE", "RDATE", "ATTENDEE", "ORGANIZER", "RECURRENCE-ID")
                ):
                    raise CalendarError("capability_unverified")
        intent = {
            "action": step["action"],
            "target": target,
            "expected_etag": step.get("expected_etag"),
            "resource_ref": source_ref("res", cal["ref"], target),
        }
        if step["action"] == "delete":
            intent["desired"] = None
        elif cal["provider"] == "google":
            desired = {
                "start": {"dateTime": event["start"], "timeZone": event["timezone"]},
                "end": {"dateTime": event["end"], "timeZone": event["timezone"]},
            }
            desired.update(
                {
                    {"title": "summary", "location": "location", "description": "description"}[k]: event[k]
                    for k in ("title", "location", "description")
                    if k in event
                }
            )
            if step["action"] == "create":
                desired["id"] = target
            intent["desired"] = desired
        else:
            original = (
                parse(old["data"])
                if old
                else Calendar.from_ical(
                    create_event(
                        step["target_id"], instant(event["start"]), instant(event["end"]), event["title"]
                    )
                )
            )
            e = master(original)
            _set(e, "DTSTART", instant(event["start"]))
            _set(e, "DTEND", instant(event["end"]))
            for k, property_name in [
                ("title", "SUMMARY"),
                ("location", "LOCATION"),
                ("description", "DESCRIPTION"),
            ]:
                if k in event:
                    _set(e, property_name, event[k])
            _touch(e)
            intent["desired"] = base64.b64encode(original.to_ical()).decode()
            intent["semantic_hash"] = semantic_hash(original.to_ical())
        return intent

    async def mutate(self, cal, intent):
        adapter = await self.adapter(cal)
        if cal["provider"] == "icloud":
            if intent["action"] == "delete":
                await adapter.delete(intent["target"], intent["expected_etag"])
            else:
                await adapter.put(
                    intent["target"],
                    base64.b64decode(intent["desired"]),
                    etag=intent["expected_etag"],
                    create=intent["action"] == "create",
                )
        elif intent["action"] == "create":
            await adapter.create(cal["target"], intent["desired"])
        elif intent["action"] == "update":
            await adapter.patch(cal["target"], intent["target"], intent["desired"], intent["expected_etag"])
        else:
            await adapter.delete(cal["target"], intent["target"], intent["expected_etag"])

    async def verify(self, cal, intent):
        value = await self.read(cal, intent["target"])
        if intent["action"] == "delete":
            return (
                {"deleted": True, "resource_ref": intent["resource_ref"]}
                if value is None or value.get("status") == "cancelled"
                else None
            )
        if not value:
            return None
        if cal["provider"] == "icloud":
            match = semantic_hash(value["data"]) == intent["semantic_hash"]
        else:

            def equal(k, v):
                actual = value.get(k)
                if k in ("start", "end"):
                    return (
                        actual
                        and instant(actual.get("dateTime")) == instant(v["dateTime"])
                        and actual.get("timeZone") == v.get("timeZone")
                    )
                return actual == v

            match = all(equal(k, v) for k, v in intent["desired"].items())
        return (
            {"resource_ref": intent["resource_ref"], "verified": True, "etag": value.get("etag")}
            if match
            else None
        )
