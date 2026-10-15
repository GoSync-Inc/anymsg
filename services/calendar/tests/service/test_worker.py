import pytest
import asyncio
from family_calendar.errors import CalendarError
from family_calendar.service.core import FamilyService
from tests.service.test_policy import SCOPES


@pytest.mark.asyncio
async def test_only_one_worker_can_recover_jobs_and_run_for_a_database(store):
    from family_calendar.service.store import Store
    from family_calendar.service.worker import Worker

    active = asyncio.Event()

    class Providers:
        closed = False

        async def close(self):
            self.closed = True

    first_store, second_store = Store(store.dsn, store.schema), Store(store.dsn, store.schema)
    first_provider, second_provider = Providers(), Providers()
    first, second = Worker(first_store, first_provider), Worker(second_store, second_provider)

    async def tick():
        active.set()
        await asyncio.Future()

    first.tick = tick
    running = asyncio.create_task(first.run())
    try:
        await asyncio.wait_for(active.wait(), 2)
        with pytest.raises(CalendarError, match="worker_already_running"):
            await second.run()
        assert second_provider.closed
    finally:
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
    assert first_provider.closed
    with store._connect() as connection:
        assert connection.execute(
            "SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS ok",
            (store.lock_key("worker", "singleton"),),
        ).fetchone()["ok"]


@pytest.mark.asyncio
async def test_interrupted_sync_job_fails_once_and_does_not_block_a_fresh_refresh(household):
    from family_calendar.service.store import Store
    from family_calendar.service.worker import Worker

    household.grant("family", "adult-a", "cal-a", SCOPES, "details")
    key = household.agent("agent", "family", "adult-a", SCOPES, ["cal-a"])
    window = ("2026-10-19T00:00:00Z", "2026-10-20T00:00:00Z")
    # A job that killed the previous worker process mid-read.
    household.query(
        "INSERT INTO sync_jobs(id,principal_id,calendar_ref,start_at,end_at,state) "
        "VALUES ('sync-crashed','agent','cal-a',%s,%s,'sending')",
        window,
    )
    reads = []

    class Providers:
        async def read_window(self, cal, start, end):
            reads.append(cal["ref"])
            return []

        async def close(self):
            pass

    recovering = Worker(Store(household.dsn, household.schema), Providers())
    ticked = asyncio.Event()

    async def tick():
        ticked.set()
        await asyncio.Future()

    recovering.tick = tick
    running = asyncio.create_task(recovering.run())
    try:
        await asyncio.wait_for(ticked.wait(), 5)
    finally:
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
    assert household.one("SELECT state FROM sync_jobs WHERE id='sync-crashed'")["state"] == "failed"
    coverage = FamilyService(household).agenda(key, ["cal-a"], *window)["coverage"][0]
    assert (coverage["status"], coverage["error"]) == ("unknown", "sync_interrupted")

    worker = Worker(household, Providers())
    household.query(
        "INSERT INTO sync_jobs(id,principal_id,calendar_ref,start_at,end_at) "
        "VALUES ('sync-fresh','agent','cal-a',%s,%s)",
        window,
    )
    assert await worker.sync_one()
    assert not await worker.sync_one()
    assert reads == ["cal-a"]
    assert household.one("SELECT state FROM sync_jobs WHERE id='sync-fresh'")["state"] == "verified"
    coverage = FamilyService(household).agenda(key, ["cal-a"], *window)["coverage"][0]
    assert (coverage["status"], coverage["error"]) == ("complete", None)


class WindowProvider:
    def __init__(self, rows):
        self.rows = rows

    async def read_window(self, cal, start, end):
        return self.rows

    async def close(self):
        pass


WINDOW = ("2026-10-19T00:00:00Z", "2026-10-20T00:00:00Z")


async def _sync(household, rows):
    from family_calendar.service.worker import Worker

    household.grant("family", "adult-a", "cal-a", SCOPES, "details")
    key = household.agent("agent", "family", "adult-a", SCOPES, ["cal-a"])
    household.query(
        "INSERT INTO sync_jobs(id,principal_id,calendar_ref,start_at,end_at) VALUES ('sync','agent','cal-a',%s,%s)",
        WINDOW,
    )
    assert await Worker(household, WindowProvider(rows)).sync_one()
    return key, household.one("SELECT state FROM sync_jobs WHERE id='sync'")["state"]


@pytest.mark.asyncio
async def test_zero_length_provider_event_publishes_as_non_blocking_instant(household):
    from datetime import datetime, timezone
    from family_calendar.service.providers import normalize_google
    from family_calendar.service.store import instant

    point = {"dateTime": "2026-10-19T08:00:00Z"}
    rows = [
        normalize_google("cal-a", {"id": "point", "summary": "Reminder", "start": point, "end": point}, "UTC")
    ]
    key, state = await _sync(household, rows)
    agenda = FamilyService(household).agenda(key, ["cal-a"], *WINDOW)
    assert state == "verified" and agenda["coverage"][0]["status"] == "complete"
    [event] = agenda["events"]
    assert instant(event["start"]) == instant(event["end"]) == datetime(2026, 10, 19, 8, tzinfo=timezone.utc)
    assert event["busy"] is False and event["instant"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "occurrence,code",
    [
        ({"details": {"title": "nul\x00"}}, "invalid_provider_data"),
        ({"end": "2026-10-19T07:00:00Z"}, "invalid_provider_event_time"),
    ],
)
async def test_unstorable_provider_event_is_reported_as_data_not_unavailable_provider(
    household, occurrence, code
):
    event = {"ref": "event-x", "start": "2026-10-19T08:00:00Z", "end": "2026-10-19T09:00:00Z"}
    event.update({"busy": True, "details": {}}, **occurrence)
    rows = [{"resource_ref": "res-x", "target": "x", "etag": None, "payload": {}, "occurrences": [event]}]
    _, state = await _sync(household, rows)
    assert state == "failed"
    assert household.one("SELECT status,error FROM coverage WHERE calendar_ref='cal-a'") == {
        "status": "blocked",
        "error": code,
    }
    assert household.one("SELECT count(*) AS n FROM occurrences")["n"] == 0


@pytest.fixture
def operation(household):
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
    return household, key, service.apply(key, plan["plan_id"], "request-1")["operation_id"]


class AmbiguousProvider:
    mutations = 0

    async def prepare(self, cal, step):
        return {
            "target": "stable-target",
            "desired": {"title": "Test"},
            "expected_etag": None,
            "action": "create",
        }

    async def mutate(self, cal, intent):
        self.mutations += 1
        raise CalendarError("unknown_result")

    async def verify(self, cal, intent):
        return None


@pytest.mark.asyncio
async def test_unknown_create_is_not_sent_again(operation):
    from family_calendar.service.worker import Worker

    store, key, op_id = operation
    provider = AmbiguousProvider()
    worker = Worker(store, provider)
    await worker.run_operation(op_id)
    await worker.run_operation(op_id, reconcile=True)
    assert provider.mutations == 1
    assert FamilyService(store).operation(key, op_id)["state"] == "needs_attention", store.query(
        "SELECT state,error FROM operation_steps WHERE operation_id=%s", (op_id,)
    )


@pytest.mark.asyncio
async def test_expired_claim_reconciles_before_mutation(operation):
    from family_calendar.service.worker import Worker
    from psycopg.types.json import Jsonb

    store, key, op_id = operation
    provider = AmbiguousProvider()
    store.query(
        "UPDATE operations SET state='running',lease_until=now()-interval '1 second' WHERE id=%s", (op_id,)
    )
    store.query(
        "UPDATE operation_steps SET state='sending',intent=%s WHERE operation_id=%s",
        (Jsonb({"target": "stable-target", "desired": {}, "action": "create"}), op_id),
    )
    await Worker(store, provider).run_operation(op_id)
    assert provider.mutations == 0
    assert FamilyService(store).operation(key, op_id)["state"] == "needs_attention"


@pytest.mark.asyncio
async def test_revoked_write_blocks_worker_before_network(operation):
    from family_calendar.service.worker import Worker

    store, key, op_id = operation
    provider = AmbiguousProvider()
    store.write_opt_in("cal-a", False)
    await Worker(store, provider).run_operation(op_id)
    assert provider.mutations == 0
    assert store.one("SELECT state FROM operations WHERE id=%s", (op_id,))["state"] == "failed"


@pytest.mark.asyncio
async def test_readback_auth_failure_keeps_successful_mutation_unknown(operation):
    from family_calendar.service.worker import Worker

    class Provider(AmbiguousProvider):
        async def mutate(self, cal, intent):
            self.mutations += 1

        async def verify(self, cal, intent):
            raise CalendarError("reauth_required")

    store, key, op_id = operation
    provider = Provider()
    await Worker(store, provider).run_operation(op_id)
    assert FamilyService(store).operation(key, op_id)["state"] == "unknown"
    assert provider.mutations == 1


@pytest.mark.asyncio
async def test_two_workers_do_not_send_same_operation(operation):
    import asyncio
    from family_calendar.service.worker import Worker

    class Provider(AmbiguousProvider):
        async def mutate(self, cal, intent):
            self.mutations += 1
            await asyncio.sleep(0.05)

        async def verify(self, cal, intent):
            return {"verified": True}

    store, key, op_id = operation
    provider = Provider()
    await asyncio.gather(
        Worker(store, provider).run_operation(op_id), Worker(store, provider).run_operation(op_id)
    )
    assert provider.mutations == 1
    assert FamilyService(store).operation(key, op_id)["state"] == "verified"


@pytest.mark.asyncio
async def test_read_projection_cannot_race_a_mutation(operation):
    from family_calendar.service.worker import Worker

    store, key, op_id = operation
    provider = AmbiguousProvider()
    with store.transaction() as reader_lock:
        reader_lock.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (store.lock_key("calendar", "cal-a"),)
        )
        await Worker(store, provider).run_operation(op_id)
    assert provider.mutations == 0
    assert store.one("SELECT state FROM operations WHERE id=%s", (op_id,))["state"] == "failed"


def test_operation_receipt_remains_readable_after_write_opt_in_removed(operation):
    store, key, op_id = operation
    store.write_opt_in("cal-a", False)
    assert FamilyService(store).operation(key, op_id)["state"] == "queued"
