from datetime import datetime, timedelta, timezone
import hashlib
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from psycopg.types.json import Jsonb
from family_calendar.errors import CalendarError
from .store import digest, instant, ref

READ = ["agenda:read", "availability:read", "event:read", "assignment:read"]
WRITE = ["plan:create", "plan:execute", "event:create", "event:update", "event:delete", "assignment:write"]


def window(start, end):
    a, b = instant(start), instant(end)
    if not a < b or b - a > timedelta(days=31):
        raise CalendarError("invalid_window")
    return a, b


def validate_event(event):
    allowed = {"title", "start", "end", "timezone", "location", "description"}
    if not isinstance(event, dict) or set(event) - allowed or not {"start", "end", "timezone"} <= set(event):
        raise CalendarError("invalid_event")
    a, b = window(event["start"], event["end"])
    try:
        ZoneInfo(event["timezone"])
    except (ZoneInfoNotFoundError, TypeError):
        raise CalendarError("invalid_timezone") from None
    for k, limit in [("title", 256), ("location", 512), ("description", 4000)]:
        if k in event and (not isinstance(event[k], str) or len(event[k]) > limit):
            raise CalendarError("invalid_event")
    return dict(event, start=a.isoformat(), end=b.isoformat())


class FamilyService:
    def __init__(self, store):
        self.store = store

    def principal(self, key=None, principal_id=None):
        field, value = (
            ("id", principal_id)
            if principal_id
            else ("key_hash", hashlib.sha256((key or "").encode()).hexdigest())
        )
        # Column selection is fixed, never user input.
        p = self.store.one(
            f"SELECT * FROM principals WHERE {field}=%s AND enabled AND expires_at>now()", (value,)
        )
        if not p:
            raise CalendarError("not_found_or_forbidden")
        m = self.store.one(
            "SELECT * FROM memberships WHERE group_id=%s AND person_id=%s AND enabled",
            (p["group_id"], p["person_id"]),
        )
        if not m:
            raise CalendarError("not_found_or_forbidden")
        return dict(p, role=m["role"], member_version=m["version"])

    def authorize(self, key, calendar_ref, scope, *, principal_id=None):
        p = self.principal(key, principal_id)
        c = self.store.one(
            "SELECT c.*,cn.provider,cn.enabled AS connection_enabled,cn.version AS connection_version, "
            "g.scopes AS grant_scopes,g.visibility AS grant_visibility,g.version AS grant_version, "
            "a.visibility AS agent_visibility FROM calendars c JOIN connections cn ON cn.id=c.connection_id "
            "JOIN calendar_grants g ON g.calendar_ref=c.ref JOIN agent_calendars a ON a.calendar_ref=c.ref "
            "WHERE c.ref=%s AND g.group_id=%s AND g.person_id=%s AND a.principal_id=%s",
            (calendar_ref, p["group_id"], p["person_id"], p["id"]),
        )
        if not c or scope not in p["scopes"] or scope not in c["grant_scopes"] or not c["connection_enabled"]:
            raise CalendarError("not_found_or_forbidden")
        c["visibility"] = (
            "busy_only" if "busy_only" in (c["agent_visibility"], c["grant_visibility"]) else "details"
        )
        if scope in WRITE:
            if c["visibility"] == "busy_only" or p["role"] in ("dependent", "helper"):
                raise CalendarError("not_found_or_forbidden")
            if c["privilege"] != "write":
                raise CalendarError("provider_readonly")
            if not c["write_enabled"]:
                raise CalendarError("write_not_enabled")
        return p, c

    def _scope_hash(self, p, calendars):
        return digest(
            [
                p["id"],
                p["version"],
                p["member_version"],
                p["role"],
                p["scopes"],
                [
                    (c["ref"], c["version"], c["connection_version"], c["grant_version"], c["visibility"])
                    for c in calendars
                ],
            ]
        )

    def calendars(self, key):
        p = self.principal(key)
        out = []
        scope = "agenda:read" if "agenda:read" in p["scopes"] else "availability:read"
        for c in self.store.query(
            "SELECT c.*,cn.provider,cn.enabled AS connection_enabled,g.scopes AS grant_scopes,"
            "g.visibility AS grant_visibility,a.visibility AS agent_visibility FROM calendars c "
            "JOIN connections cn ON cn.id=c.connection_id JOIN calendar_grants g ON g.calendar_ref=c.ref "
            "JOIN agent_calendars a ON a.calendar_ref=c.ref "
            "WHERE g.group_id=%s AND g.person_id=%s AND a.principal_id=%s",
            (p["group_id"], p["person_id"], p["id"]),
        ):
            if scope not in p["scopes"] or scope not in c["grant_scopes"] or not c["connection_enabled"]:
                continue
            c["visibility"] = (
                "busy_only" if "busy_only" in (c["agent_visibility"], c["grant_visibility"]) else "details"
            )
            out.append(
                {
                    "calendar_ref": c["ref"],
                    "provider": c["provider"],
                    "label": c["label"] if c["visibility"] == "details" else "Calendar",
                    "timezone": c["timezone"],
                    "visibility": c["visibility"],
                    "write_enabled": c["write_enabled"],
                }
            )
        return {"calendars": out, "group_id": p["group_id"]}

    def family(self, key):
        p = self.principal(key)
        return {
            "group_id": p["group_id"],
            "members": self.store.query(
                "SELECT m.person_id,m.role,p.label FROM memberships m JOIN people p ON p.id=m.person_id "
                "WHERE m.group_id=%s AND m.enabled ORDER BY m.person_id",
                (p["group_id"],),
            ),
        }

    def capabilities(self, key):
        calendars = self.calendars(key)
        return dict(
            calendars,
            features={
                "agenda": True,
                "slots": True,
                "assignments": True,
                "event_create": True,
                "event_update": "single non-recurring guest-free event",
                "event_delete": "single non-recurring guest-free event",
                "recurrence_read": True,
                "recurrence_write": False,
                "future_split": False,
                "invitations": False,
                "refresh": "bounded on-demand polling",
                "window_limit_days": 31,
                "event_limit": 500,
                "atomic_cross_provider": False,
            },
        )

    def agenda(self, key, calendar_refs, start, end, scope="agenda:read"):
        a, b = window(start, end)
        if not calendar_refs or len(calendar_refs) > 32 or len(set(calendar_refs)) != len(calendar_refs):
            raise CalendarError("invalid_calendar_refs")
        coverage = []
        events = []
        for calendar_ref in calendar_refs:
            _, cal = self.authorize(key, calendar_ref, scope)
            cov = self.store.one("SELECT * FROM coverage WHERE calendar_ref=%s", (calendar_ref,))
            complete = bool(
                cov
                and cov["status"] == "complete"
                and cov["start_at"] <= a
                and cov["end_at"] >= b
                and cov["checked_at"] > datetime.now(timezone.utc) - timedelta(minutes=5)
            )
            coverage.append(
                {
                    "calendar_ref": calendar_ref,
                    "status": "complete" if complete else "unknown",
                    "checked_at": cov["checked_at"].isoformat() if cov else None,
                    "error": cov.get("error") if cov else None,
                }
            )
            for e in self.store.query(
                "SELECT * FROM occurrences WHERE calendar_ref=%s AND start_at<%s AND end_at>%s "
                "ORDER BY start_at LIMIT 501",
                (calendar_ref, b, a),
            ):
                event = {
                    "calendar_ref": calendar_ref,
                    "start": e["start_at"].isoformat(),
                    "end": e["end_at"].isoformat(),
                    "busy": e["busy"],
                }
                if cal["visibility"] == "details":
                    event.update(event_ref=e["ref"], **e["details"])
                events.append(event)
        if len(events) > 500:
            raise CalendarError("coverage_incomplete")
        return {
            "events": sorted(events, key=lambda x: x["start"]),
            "coverage": coverage,
            "required_sources_complete": all(c["status"] == "complete" for c in coverage),
        }

    def slots(self, key, member_ids, start, end, duration_minutes):
        p = self.principal(key)
        a, b = window(start, end)
        if "availability:read" not in p["scopes"] or not member_ids or len(member_ids) > 32:
            raise CalendarError("not_found_or_forbidden")
        if not isinstance(duration_minutes, int) or not 5 <= duration_minutes <= 1440:
            raise CalendarError("invalid_duration")
        refs = []
        missing = []
        for member in member_ids:
            if not self.store.one(
                "SELECT 1 FROM memberships WHERE group_id=%s AND person_id=%s AND enabled",
                (p["group_id"], member),
            ):
                raise CalendarError("not_found_or_forbidden")
            rows = self.store.query(
                "SELECT calendar_ref FROM bindings WHERE group_id=%s AND person_id=%s",
                (p["group_id"], member),
            )
            if not rows:
                missing.append(member)
            refs.extend(r["calendar_ref"] for r in rows)
        agenda = (
            self.agenda(key, list(dict.fromkeys(refs)), start, end, "availability:read")
            if refs
            else {"events": [], "coverage": [], "required_sources_complete": False}
        )
        result = {
            "availability": "unknown",
            "verified_slots": [],
            "coverage": agenda["coverage"],
            "required_sources_complete": agenda["required_sources_complete"] and not missing,
            "unbound_members": missing,
        }
        if not result["required_sources_complete"]:
            return result
        busy = [
            (max(a, instant(e["start"])), min(b, instant(e["end"]))) for e in agenda["events"] if e["busy"]
        ]
        for row in self.store.query(
            "SELECT start_at,end_at FROM assignments WHERE group_id=%s AND person_id=ANY(%s) "
            "AND status='assigned' AND start_at<%s AND end_at>%s",
            (p["group_id"], member_ids, b, a),
        ):
            busy.append((max(a, row["start_at"]), min(b, row["end_at"])))
        cursor = a
        for left, right in sorted(busy) + [(b, b)]:
            if left - cursor >= timedelta(minutes=duration_minutes):
                result["verified_slots"].append({"start": cursor.isoformat(), "end": left.isoformat()})
            cursor = max(cursor, right)
        result["availability"] = "verified"
        return result

    def plan(self, key, actions):
        p = self.principal(key)
        if not isinstance(actions, list) or not 1 <= len(actions) <= 8:
            raise CalendarError("invalid_actions")
        calendars = []
        prepared = []
        touched = set()
        for action in actions:
            if not isinstance(action, dict) or set(action) - {"action", "calendar_ref", "event_ref", "event"}:
                raise CalendarError("invalid_action")
            kind = action.get("action")
            if kind not in ("create", "update", "delete"):
                raise CalendarError("unsupported_action")
            _, cal = self.authorize(key, action.get("calendar_ref"), "plan:create")
            self.authorize(key, cal["ref"], "event:" + kind)
            calendars.append(cal)
            step = {
                "action": kind,
                "calendar_ref": cal["ref"],
                "provider": cal["provider"],
                "ordinal": len(prepared),
            }
            if kind != "delete":
                step["event"] = validate_event(action.get("event"))
            if kind == "create":
                if not step["event"].get("title"):
                    raise CalendarError("invalid_event")
                step["resource_ref"] = ref("res")
                step["target_id"] = uuid_token()
            else:
                row = self.store.one(
                    "SELECT o.*,r.etag,r.target FROM occurrences o JOIN resources r ON r.ref=o.resource_ref "
                    "WHERE o.ref=%s AND o.calendar_ref=%s",
                    (action.get("event_ref"), cal["ref"]),
                )
                if not row:
                    raise CalendarError("not_found_or_forbidden")
                if row["resource_ref"] in touched:
                    raise CalendarError("duplicate_resource_action")
                touched.add(row["resource_ref"])
                if (
                    row["details"].get("recurring")
                    or row["details"].get("guests")
                    or row["details"].get("special")
                ):
                    raise CalendarError("capability_unverified")
                if not row["etag"]:
                    raise CalendarError("conditional_write_unavailable")
                step.update(
                    resource_ref=row["resource_ref"],
                    target_id=row["target"],
                    expected_etag=row["etag"],
                    before={
                        "start": row["start_at"].isoformat(),
                        "end": row["end_at"].isoformat(),
                        **row["details"],
                    },
                )
            prepared.append(step)
        plan_id = ref("plan")
        expires = datetime.now(timezone.utc) + timedelta(minutes=10)
        payload = {"steps": prepared}
        self.store.query(
            "INSERT INTO plans(id,principal_id,payload,scope_hash,expires_at) VALUES (%s,%s,%s,%s,%s)",
            (plan_id, p["id"], Jsonb(payload), self._scope_hash(p, calendars), expires),
        )
        return {
            "plan_id": plan_id,
            "expires_at": expires.isoformat(),
            "steps": [self._preview(s) for s in prepared],
            "notifications": "none; events with guests are blocked",
            "atomic": False,
        }

    @staticmethod
    def _preview(step):
        return {k: v for k, v in step.items() if k not in ("target_id", "resource_ref", "expected_etag")}

    def validate_plan(self, plan, principal_id):
        p = self.principal(principal_id=principal_id)
        calendars = []
        for step in plan["payload"]["steps"]:
            self.authorize(None, step["calendar_ref"], "plan:execute", principal_id=principal_id)
            _, cal = self.authorize(
                None, step["calendar_ref"], "event:" + step["action"], principal_id=principal_id
            )
            calendars.append(cal)
        if self._scope_hash(p, calendars) != plan["scope_hash"]:
            raise CalendarError("scope_changed")

    def apply(self, key, plan_id, request_id):
        p = self.principal(key)
        if not isinstance(request_id, str) or not 8 <= len(request_id) <= 128:
            raise CalendarError("invalid_request_id")
        plan = self.store.one("SELECT * FROM plans WHERE id=%s AND principal_id=%s", (plan_id, p["id"]))
        if not plan:
            raise CalendarError("not_found_or_forbidden")
        with self.store.transaction() as c:
            c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (p["id"] + request_id,))
            old = c.execute(
                "SELECT * FROM operations WHERE principal_id=%s AND request_id=%s", (p["id"], request_id)
            ).fetchone()
            if old:
                if old["plan_id"] != plan_id:
                    raise CalendarError("idempotency_conflict")
                return {"operation_id": old["id"], "state": old["state"]}
            if plan["expires_at"] < datetime.now(timezone.utc):
                raise CalendarError("plan_expired")
            self.validate_plan(plan, p["id"])
            operation_id = ref("op")
            c.execute(
                "INSERT INTO operations(id,principal_id,plan_id,request_id) VALUES (%s,%s,%s,%s)",
                (operation_id, p["id"], plan_id, request_id),
            )
            for step in plan["payload"]["steps"]:
                c.execute(
                    "INSERT INTO operation_steps(operation_id,ordinal) VALUES (%s,%s)",
                    (operation_id, step["ordinal"]),
                )
        return {"operation_id": operation_id, "state": "queued"}

    def operation(self, key, operation_id):
        p = self.principal(key)
        op = self.store.one(
            "SELECT * FROM operations WHERE id=%s AND principal_id=%s", (operation_id, p["id"])
        )
        if not op:
            raise CalendarError("not_found_or_forbidden")
        plan = self.store.one("SELECT * FROM plans WHERE id=%s", (op["plan_id"],))
        # Historical read access does not require write opt-in to remain enabled.
        scope = "event:read" if "event:read" in p["scopes"] else "agenda:read"
        for step in plan["payload"]["steps"]:
            _, cal = self.authorize(key, step["calendar_ref"], scope)
            if cal["visibility"] != "details":
                raise CalendarError("not_found_or_forbidden")
        steps = self.store.query(
            "SELECT ordinal,state,receipt,error FROM operation_steps WHERE operation_id=%s ORDER BY ordinal",
            (operation_id,),
        )
        return {
            "operation_id": operation_id,
            "state": op["state"],
            "steps": steps,
            "error": op["error"],
            "next_action": "reconcile; do not create again"
            if op["state"] in ("unknown", "needs_attention", "partial")
            else None,
        }

    def assignments(self, key, start, end):
        p = self.principal(key)
        a, b = window(start, end)
        if "assignment:read" not in p["scopes"]:
            raise CalendarError("not_found_or_forbidden")
        rows = self.store.query(
            "SELECT id AS assignment_id,person_id AS member_id,duty,start_at,end_at,status,version "
            "FROM assignments WHERE group_id=%s AND start_at<%s AND end_at>%s ORDER BY start_at LIMIT 501",
            (p["group_id"], b, a),
        )
        if len(rows) > 500:
            raise CalendarError("coverage_incomplete")
        return {
            "assignments": [
                dict((k, v.isoformat() if isinstance(v, datetime) else v) for k, v in row.items())
                for row in rows
            ]
        }

    def assignment(
        self, key, member_id, duty, start, end, status="proposed", assignment_id=None, expected_version=None
    ):
        p = self.principal(key)
        a, b = window(start, end)
        if status == "accepted":
            raise CalendarError("confirmation_actor_required")
        if status not in ("proposed", "assigned", "declined", "cancelled") or duty not in (
            "pickup",
            "dropoff",
            "stay_with_child",
        ):
            raise CalendarError("invalid_assignment")
        if "assignment:write" not in p["scopes"] or p["role"] not in ("group_admin", "coordinator", "adult"):
            raise CalendarError("not_found_or_forbidden")
        if p["role"] == "adult" and member_id != p["person_id"]:
            raise CalendarError("not_found_or_forbidden")
        if not self.store.one(
            "SELECT 1 FROM memberships WHERE group_id=%s AND person_id=%s AND enabled",
            (p["group_id"], member_id),
        ):
            raise CalendarError("not_found_or_forbidden")
        if assignment_id:
            with self.store.transaction() as c:
                old = c.execute(
                    "SELECT * FROM assignments WHERE id=%s AND group_id=%s FOR UPDATE",
                    (assignment_id, p["group_id"]),
                ).fetchone()
                if not old or (p["role"] == "adult" and old["person_id"] != p["person_id"]):
                    raise CalendarError("not_found_or_forbidden")
                if expected_version != old["version"]:
                    raise CalendarError("version_conflict")
                c.execute(
                    "UPDATE assignments SET person_id=%s,duty=%s,start_at=%s,end_at=%s,status=%s,version=version+1 WHERE id=%s",
                    (member_id, duty, a, b, status, assignment_id),
                )
                version = old["version"] + 1
        else:
            if status in ("cancelled", "declined"):
                raise CalendarError("assignment_id_required")
            assignment_id = ref("assignment")
            self.store.query(
                "INSERT INTO assignments(id,group_id,person_id,principal_id,duty,start_at,end_at,status) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (assignment_id, p["group_id"], member_id, p["id"], duty, a, b, status),
            )
            version = 1
        return {
            "assignment_id": assignment_id,
            "version": version,
            "status": status,
            "member_id": member_id,
            "duty": duty,
            "start": a.isoformat(),
            "end": b.isoformat(),
        }


def uuid_token():
    # Google's client-generated IDs accept lowercase base32hex; hex is a valid subset.
    from uuid import uuid4

    return "fcs" + uuid4().hex
