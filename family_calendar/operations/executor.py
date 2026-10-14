from family_calendar.errors import CalendarError
from family_calendar.providers.caldav.codec import semantic_hash
from family_calendar.providers.caldav.transport import within_collection


class Executor:
    def __init__(self, adapter, journal, collection, *, check_write=None):
        self.adapter, self.journal, self.collection = adapter, journal, collection
        self.check_write = check_write

    async def reconcile(self, step_id):
        step = self.journal.get(step_id)
        intent = step["intent"]
        if not within_collection(intent["href"], self.collection):
            raise CalendarError("write_outside_allowed_calendar")
        observed = None
        observed_hash = None
        try:
            resource = await self.adapter.read(intent["href"])
            observed = resource["data"] if resource else None
            observed_hash = semantic_hash(observed) if resource else "absent"
            if intent["kind"] == "delete":
                matched = resource is None
            else:
                matched = resource is not None and observed_hash == semantic_hash(intent["desired"])
            if matched:
                self.journal.mark(
                    step_id,
                    "verified",
                    etag=resource["etag"] if resource else None,
                    observed=observed,
                    observed_hash=observed_hash,
                )
            else:
                # A missing immediate GET does not authorize replay of a timed-out mutation.
                self.journal.mark(
                    step_id,
                    "needs_attention",
                    error="desired_state_unconfirmed",
                    observed=observed,
                    observed_hash=observed_hash,
                )
        except CalendarError as exc:
            self.journal.mark(
                step_id, "unknown", error=exc.code, observed=observed, observed_hash=observed_hash
            )
        return self.journal.get(step_id)

    async def execute(self, step_id):
        step = self.journal.get(step_id)
        intent = step["intent"]
        if not within_collection(intent["href"], self.collection):
            raise CalendarError("write_outside_allowed_calendar")
        if step["state"] == "verified":
            return step
        if self.check_write:
            self.check_write()
        if step["state"] in {"sending", "unknown", "needs_attention"}:
            return await self.reconcile(step_id)
        if step["state"] != "prepared":
            return step
        if not self.journal.claim(step_id):
            return self.journal.get(step_id)
        try:
            if intent["kind"] == "create":
                await self.adapter.put(intent["href"], intent["desired"], create=True)
            elif intent["kind"] == "update":
                await self.adapter.put(intent["href"], intent["desired"], etag=intent["etag"])
            elif intent["kind"] == "delete":
                await self.adapter.delete(intent["href"], intent["etag"])
            else:
                raise CalendarError("invalid_operation_kind")
        except CalendarError as exc:
            state = (
                "unknown"
                if exc.code == "write_unknown"
                else "conflict"
                if exc.code == "version_conflict"
                else "failed"
            )
            self.journal.mark(step_id, state, error=exc.code)
            return self.journal.get(step_id)
        return await self.reconcile(step_id)
