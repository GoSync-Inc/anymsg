import asyncio
from datetime import datetime, timedelta, timezone

import httpx

from family_calendar.pilot.journal import Journal
from family_calendar.providers.caldav.adapter import CalDAVAdapter
from family_calendar.providers.caldav.codec import create_event, semantic_hash
from family_calendar.providers.caldav.transport import DAVTransport
from family_calendar.operations.executor import Executor


def test_verified_step_contains_actual_readback_evidence(tmp_path):
    now = datetime(2026, 10, 19, 16, tzinfo=timezone.utc)
    data = create_event("evidence@family.invalid", now, now + timedelta(hours=1), "Synthetic")

    async def exercise():
        t = DAVTransport(
            "a",
            "secret",
            client=httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda req: (
                        httpx.Response(201)
                        if req.method == "PUT"
                        else httpx.Response(200, content=data, headers={"etag": '"v1"'})
                    )
                )
            ),
        )
        j = Journal(tmp_path / "run", "a", "cal1")
        try:
            step = j.prepare(
                "create",
                {
                    "kind": "create",
                    "href": "https://caldav.icloud.com/a/c/e.ics",
                    "desired": data,
                    "etag": None,
                },
            )
            await Executor(CalDAVAdapter("a", t), j, "https://caldav.icloud.com/a/c/").execute(step)
            saved = j.get(step)
            assert saved["observed_data"] == data
            assert saved["observed_hash"] == semantic_hash(data)
        finally:
            j.close()
            await t.close()

    asyncio.run(exercise())


def test_unconfirmed_readback_is_preserved_for_diagnosis(tmp_path):
    now = datetime(2026, 10, 19, 16, tzinfo=timezone.utc)
    desired = create_event("different@family.invalid", now, now + timedelta(hours=1), "Wanted")
    actual = create_event("different@family.invalid", now, now + timedelta(hours=1), "Changed by server")

    async def exercise():
        t = DAVTransport(
            "a",
            "secret",
            client=httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda req: (
                        httpx.Response(201)
                        if req.method == "PUT"
                        else httpx.Response(200, content=actual, headers={"etag": '"v1"'})
                    )
                )
            ),
        )
        j = Journal(tmp_path / "run", "a", "cal1")
        try:
            step = j.prepare(
                "create",
                {
                    "kind": "create",
                    "href": "https://caldav.icloud.com/a/c/e.ics",
                    "desired": desired,
                    "etag": None,
                },
            )
            saved = await Executor(CalDAVAdapter("a", t), j, "https://caldav.icloud.com/a/c/").execute(step)
            assert saved["state"] == "needs_attention"
            assert saved["observed_data"] == actual
            assert saved["observed_hash"] == semantic_hash(actual)
        finally:
            j.close()
            await t.close()

    asyncio.run(exercise())
