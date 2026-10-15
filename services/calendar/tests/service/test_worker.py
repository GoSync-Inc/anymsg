import pytest
from family_calendar.errors import CalendarError
from family_calendar.service.core import FamilyService
from tests.service.test_policy import SCOPES


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
