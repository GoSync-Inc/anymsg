"""A bounded live matrix; every object is synthetic and journal-owned."""

from datetime import date, datetime, timedelta, timezone
import base64
import json
from importlib.metadata import version
from uuid import uuid4, uuid5, UUID
from zoneinfo import ZoneInfo

from icalendar import Calendar

from family_calendar.errors import CalendarError
from family_calendar.operations.executor import Executor
from family_calendar.private_files import read_private, write_private
from family_calendar.providers.capabilities import capabilities
from family_calendar.providers.caldav.codec import (
    create_event,
    shift_series,
    move_single,
    cancel_single,
    split_future,
    native_future,
    semantic_hash,
)


class Pilot:
    def __init__(self, adapter, journal, collection, *, environment="icloud_live", check_write=None):
        self.adapter, self.journal, self.collection = adapter, journal, collection
        self.environment = environment
        self.case_steps = set()
        self.executor = Executor(adapter, journal, collection, check_write=check_write)
        path = journal.root / "manifest.json"
        if path.exists():
            self.manifest = json.loads(read_private(path))
            if self.manifest["collection"] != collection or self.manifest["environment"] != environment:
                raise CalendarError("run_scope_mismatch")
        else:
            today = datetime.now(ZoneInfo("Asia/Makassar")).date()
            monday = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
            self.manifest = {
                "schema_version": 1,
                "run_id": str(uuid4()),
                "collection": collection,
                "environment": environment,
                "start_date": monday.isoformat(),
                "max_objects": 16,
            }
            write_private(path, json.dumps(self.manifest).encode())
        self.start = datetime.combine(
            date.fromisoformat(self.manifest["start_date"]),
            datetime.min.time().replace(hour=16),
            ZoneInfo("Asia/Makassar"),
        )

    def _uid(self, name):
        return str(uuid5(UUID(self.manifest["run_id"]), name)) + "@family.invalid"

    def _href(self, name):
        return (
            self.collection.rstrip("/")
            + "/family-pilot-"
            + str(uuid5(UUID(self.manifest["run_id"]), name))
            + ".ics"
        )

    async def _step(self, name, kind, href, *, data=None, etag=None, before=None):
        existing = self.journal.find(name)
        if existing is None:
            if (
                kind == "create"
                and sum(s["intent"]["kind"] == "create" for s in self.journal.steps())
                >= self.manifest["max_objects"]
            ):
                raise CalendarError("pilot_object_limit")
            step_id = self.journal.prepare(
                name, {"kind": kind, "href": href, "desired": data, "etag": etag, "before": before}
            )
        else:
            step_id = existing["id"]
        self.case_steps.add(step_id)
        return await self.executor.execute(step_id)

    async def _write(self, name, kind, href, *, data=None, etag=None, before=None):
        step = await self._step(name, kind, href, data=data, etag=etag, before=before)
        if step["state"] != "verified":
            raise CalendarError(step["error_code"] or "step_unconfirmed")
        return {"href": href, "data": step["intent"]["desired"], "etag": step["etag"]}

    async def _create(self, name, data):
        return await self._write(name + ":create", "create", self._href(name), data=data)

    async def _update(self, name, resource, data):
        return await self._write(
            name, "update", resource["href"], data=data, etag=resource["etag"], before=resource["data"]
        )

    def _weekly(self, name, *, count=6, start=None, until=None):
        start = start or self.start
        rule = {"FREQ": "WEEKLY"}
        if count is not None:
            rule["COUNT"] = count
        if until:
            rule["UNTIL"] = until
        return create_event(
            self._uid(name),
            start,
            start + timedelta(hours=1),
            f"[Family Pilot] {name}: synthetic weekly lesson",
            rrule=rule,
        )

    async def _single(self):
        r = await self._create(
            "single",
            create_event(
                self._uid("single"),
                self.start,
                self.start + timedelta(hours=1),
                "[Family Pilot] Synthetic single",
            ),
        )
        r = await self._update("single:move", r, shift_series(r["data"], timedelta(minutes=30)))
        await self._write("single:delete", "delete", r["href"], etag=r["etag"], before=r["data"])

    async def _conditional(self):
        r = await self._create("conditional", self._weekly("conditional"))
        await self._update("conditional:new-version", r, shift_series(r["data"], timedelta(minutes=5)))
        step = await self._step(
            "conditional:stale-version",
            "update",
            r["href"],
            data=shift_series(r["data"], timedelta(minutes=10)),
            etag=r["etag"],
            before=r["data"],
        )
        if step["state"] != "conflict":
            raise CalendarError("conditional_write_not_enforced")

    async def _exceptions(self):
        r = await self._create("exceptions", self._weekly("exceptions"))
        rid = self.start + timedelta(weeks=1)
        r = await self._update(
            "exceptions:move-one",
            r,
            move_single(r["data"], rid, rid + timedelta(hours=2), rid + timedelta(hours=3)),
        )
        r = await self._update(
            "exceptions:cancel-one", r, cancel_single(r["data"], self.start + timedelta(weeks=2))
        )
        await self._update("exceptions:series", r, shift_series(r["data"], timedelta(hours=1)))

    async def _future(self, name, *, count=6, until=False):
        source = self._weekly(
            name,
            count=None if until else count,
            until=(self.start + timedelta(weeks=5)).astimezone(timezone.utc) if until else None,
        )
        rid = self.start + timedelta(weeks=4)
        source = move_single(source, rid, rid + timedelta(hours=2), rid + timedelta(hours=3))
        source = cancel_single(source, self.start + timedelta(weeks=2))
        r = await self._create(name, source)
        past, future, mapping = split_future(
            r["data"], self.start + timedelta(weeks=3), timedelta(hours=1), self._uid(name + "-next")
        )
        write_private(self.journal.root / (name + "-mapping.json"), json.dumps(mapping).encode())
        plan_path = self.journal.root / (name + "-plan.json")
        if not plan_path.exists():
            write_private(
                plan_path,
                json.dumps(
                    {
                        "schema_version": 1,
                        "old_href": r["href"],
                        "old_etag": r["etag"],
                        "new_href": self._href(name + "-next"),
                        "mapping": mapping,
                        "before": base64.b64encode(r["data"]).decode(),
                        "past": base64.b64encode(past).decode() if past else None,
                        "future": base64.b64encode(future).decode(),
                    }
                ).encode(),
            )
        # New part first, then trim old part: a partial failure remains visible.
        new_part = await self._create(name + "-next", future)
        try:
            if past:
                await self._update(name + ":trim-past", r, past)
            else:
                await self._write(
                    name + ":delete-past", "delete", r["href"], etag=r["etag"], before=r["data"]
                )
        except CalendarError:
            last = self.journal.find(name + (":trim-past" if past else ":delete-past"))
            # Unknown trim may already have succeeded; deleting new part could lose the future.
            if last and last["state"] in {"conflict", "failed"}:
                current = await self.adapter.read(new_part["href"])
                if (
                    current is not None
                    and current["etag"] == new_part["etag"]
                    and semantic_hash(current["data"]) == semantic_hash(new_part["data"])
                ):
                    await self._step(
                        name + ":compensate-new",
                        "delete",
                        new_part["href"],
                        etag=current["etag"],
                        before=current["data"],
                    )
            raise

    async def _native(self):
        r = await self._create("native-range", self._weekly("native-range"))
        await self._update(
            "native-range:future",
            r,
            native_future(r["data"], self.start + timedelta(weeks=1), timedelta(hours=1)),
        )

    async def _all_day(self):
        start = self.start.date()
        r = await self._create(
            "all-day",
            create_event(self._uid("all-day"), start, start + timedelta(days=1), "[Family Pilot] All day"),
        )
        await self._update("all-day:move", r, shift_series(r["data"], timedelta(days=1)))

    async def _floating(self):
        start = self.start.replace(tzinfo=None)
        await self._create(
            "floating",
            create_event(
                self._uid("floating"),
                start,
                start + timedelta(hours=1),
                "[Family Pilot] Floating (interpretation unverified)",
            ),
        )

    async def _properties(self):
        start = datetime(2026, 10, 19, 16, tzinfo=ZoneInfo("Europe/Berlin"))
        data = self._weekly("properties", start=start).replace(
            b"END:VEVENT",
            "X-FAMILY-PILOT;X-PARAM=keep:Привет 🌴\r\nBEGIN:VALARM\r\nACTION:DISPLAY\r\n"
            "TRIGGER:-PT15M\r\nDESCRIPTION:Synthetic alarm\r\nEND:VALARM\r\nEND:VEVENT".encode(),
        )
        cal = Calendar.from_ical(data)
        root = cal.walk("VEVENT")[0]
        root.add("EXDATE", [start + timedelta(weeks=1)])
        root.add("RDATE", [start + timedelta(weeks=1, days=1)])
        r = await self._create("properties", cal.to_ical())
        await self._update("properties:roundtrip", r, shift_series(r["data"], timedelta(hours=1)))

    def _report(self, checks):
        visual_path = self.journal.root / "visual-check.json"
        visual = json.loads(read_private(visual_path)) if visual_path.exists() else {"result": "pending"}
        core = ["single_crud", "conditional_write", "single_cancel_series", "properties_tz_dst", "all_day"]
        future = ["future_split_count", "future_split_until", "future_split_infinite"]
        core_ok = all(checks.get(x, {}).get("status") == "server_verified" for x in core)
        future_ok = (
            all(checks.get(x, {}).get("status") == "server_verified" for x in future)
            and visual.get("future_result") == "pass"
        )
        live_accepted = self.environment == "icloud_live" and visual["result"] == "pass"
        unresolved = [
            s["id"]
            for s in self.journal.steps()
            if s["state"] in {"prepared", "sending", "unknown", "needs_attention"}
        ]
        accepted = core_ok and live_accepted and not unresolved
        gate = (
            "accepted_core_and_future"
            if accepted and future_ok
            else "accepted_core"
            if accepted
            else "not_accepted"
        )
        return {
            "schema_version": 1,
            "run_id": self.manifest["run_id"],
            "environment": self.environment,
            "scope": {
                "connection_id": self.adapter.connection_id,
                "calendar_ref": json.loads(read_private(self.journal.root / "scope.json"))["calendar_ref"],
            },
            "runtime_versions": {
                name: version(name)
                for name in (
                    "family-calendar-service",
                    "httpx",
                    "icalendar",
                    "python-dateutil",
                    "defusedxml",
                    "cryptography",
                )
            },
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "evidence_as_of": max((s["updated_at"] for s in self.journal.steps()), default=None),
            "checks": checks,
            "client_visual_check": visual["result"],
            "future_visual_check": visual.get("future_result", "not-checked"),
            "native_range_visual_check": visual.get("native_range_result", "not-checked"),
            "visual_evidence_source": "owner_reported",
            "gate_a": gate,
            "capabilities": capabilities(
                checks,
                gate,
                live=self.environment == "icloud_live",
                native_visual_pass=visual.get("native_range_result") == "pass",
            ),
            "multiaccount_live": "unverified",
            "shared_calendar_live": "unverified",
            "invitations": "unsupported",
            "unresolved_steps": unresolved,
            "operations": [
                {
                    "step_id": s["id"],
                    "request_id": s["request_id"],
                    "kind": s["intent"]["kind"],
                    "state": s["state"],
                    "error_code": s["error_code"],
                    "updated_at": s["updated_at"],
                    "expected_test_conflict": s["request_id"] == "conditional:stale-version",
                }
                for s in self.journal.steps()
            ],
            "limits": {
                "future": "simple_weekly_only",
                "exception_policy": "preserve_absolute",
                "native_range": "requires_client_visual_verification",
                "timezone_offset_comparison": "Asia/Makassar and Europe/Berlin, 2020-01-01 to 2035-01-01",
                "fault_tests": "synthetic_mock_transport",
                "max_objects": self.manifest["max_objects"],
            },
        }

    async def run(self):
        if (self.journal.root / "cleanup.json").exists():
            raise CalendarError("run_already_cleaned")
        checks = {}
        blocked = False
        cases = [
            ("single_crud", self._single),
            ("conditional_write", self._conditional),
            ("single_cancel_series", self._exceptions),
            ("future_split_count", lambda: self._future("future-count")),
            ("future_split_until", lambda: self._future("future-until", until=True)),
            ("future_split_infinite", lambda: self._future("future-infinite", count=None)),
            ("native_range_probe", self._native),
            ("all_day", self._all_day),
            ("floating_roundtrip", self._floating),
            ("properties_tz_dst", self._properties),
        ]
        for name, action in cases:
            self.case_steps = set()
            try:
                if blocked:
                    raise CalendarError("blocked_after_access_error")
                await action()
                checks[name] = {
                    "status": "server_verified",
                    "evidence": "journal_conditional_write_and_readback",
                    "step_ids": sorted(self.case_steps),
                }
            except CalendarError as exc:
                if exc.code in {
                    "reauth_required",
                    "provider_forbidden",
                    "write_not_enabled",
                    "provider_readonly",
                }:
                    blocked = True
                checks[name] = {
                    "status": "unverified",
                    "error_code": exc.code,
                    "step_ids": sorted(self.case_steps),
                }
            report = self._report(checks)
            write_private(self.journal.root / "report.json", json.dumps(report, indent=2).encode())
        return report

    async def cleanup(self):
        conflicts = 0
        for index, item in enumerate(self.journal.owned_objects()):
            current = await self.adapter.read(item["href"])
            if current is None:
                # A deletion observed without a deletion intent is recorded for explicit reconciliation.
                conflicts += 1
                continue
            if current["etag"] != item["etag"] or semantic_hash(current["data"]) != semantic_hash(
                item["desired"]
            ):
                conflicts += 1
                continue
            name = "cleanup:" + item["href"].rsplit("/", 1)[-1]
            step = await self._step(
                name, "delete", item["href"], etag=current["etag"], before=current["data"]
            )
            if step["state"] != "verified":
                conflicts += 1
        result = {"remaining": len(self.journal.owned_objects()), "conflicts": conflicts}
        write_private(self.journal.root / "cleanup.json", json.dumps(result).encode())
        return result
