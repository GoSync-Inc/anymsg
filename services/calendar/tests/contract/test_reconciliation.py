import asyncio
from datetime import datetime, timedelta, timezone

import httpx

from family_calendar.providers.caldav.transport import DAVTransport
from family_calendar.providers.caldav.adapter import CalDAVAdapter
from family_calendar.providers.caldav.codec import create_event
from family_calendar.pilot.journal import Journal
from family_calendar.operations.executor import Executor


def test_timeout_after_accepted_create_is_reconciled_without_second_put(tmp_path):
    calls = []
    stored = {}

    def handler(req):
        calls.append(req.method)
        if req.method == "PUT":
            stored["data"] = req.content
            raise httpx.ReadTimeout("sensitive-provider-detail", request=req)
        return httpx.Response(200, content=stored["data"], headers={"etag": '"one"'})

    async def exercise():
        t = DAVTransport("a", "secret", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        j = Journal(tmp_path / "run", "a", "cal1")
        try:
            adapter = CalDAVAdapter("a", t)
            executor = Executor(adapter, j, "https://caldav.icloud.com/a/c/")
            now = datetime(2026, 10, 19, 16, tzinfo=timezone.utc)
            data = create_event("synthetic@family.invalid", now, now + timedelta(hours=1), "Synthetic")
            step = j.prepare(
                "stable",
                {
                    "kind": "create",
                    "href": "https://caldav.icloud.com/a/c/e.ics",
                    "desired": data,
                    "etag": None,
                },
            )
            await executor.execute(step)
            assert j.get(step)["state"] == "unknown"
            await executor.execute(step)
            assert j.get(step)["state"] == "verified"
            assert calls == ["PUT", "GET"]
        finally:
            j.close()
            await t.close()

    asyncio.run(exercise())


def test_timeout_after_accepted_delete_reconciles_absence_without_replay(tmp_path):
    calls = []

    def handler(req):
        calls.append(req.method)
        if req.method == "DELETE":
            raise httpx.ReadTimeout("accepted delete", request=req)
        return httpx.Response(404)

    async def exercise():
        t = DAVTransport("a", "secret", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        j = Journal(tmp_path / "run", "a", "cal1")
        try:
            executor = Executor(CalDAVAdapter("a", t), j, "https://caldav.icloud.com/a/c/")
            step = j.prepare(
                "delete",
                {
                    "kind": "delete",
                    "href": "https://caldav.icloud.com/a/c/e.ics",
                    "desired": None,
                    "etag": '"one"',
                },
            )
            assert (await executor.execute(step))["state"] == "unknown"
            saved = await executor.execute(step)
            assert saved["state"] == "verified" and saved["observed_hash"] == "absent"
            assert calls == ["DELETE", "GET"]
        finally:
            j.close()
            await t.close()

    asyncio.run(exercise())
