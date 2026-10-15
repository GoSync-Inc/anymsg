"""Single-owner outbound pacing; the provider's required pause lives in PostgreSQL."""
import asyncio
import time


class ProviderRateLimited(Exception):
    def __init__(self,seconds):
        self.seconds=max(1,int(seconds))
        super().__init__('rate_limited')


class RequestGovernor:
    def __init__(self,state,*,interval=1.0,clock=time.monotonic,sleep=asyncio.sleep):
        self.state,self.interval,self.clock,self.sleep=state,interval,clock,sleep
        self.lock=asyncio.Lock(); self.next_call=0

    async def wait(self):
        # Re-read after waking: another operation/operator may extend the pause.
        # Cancellation interrupts sleep immediately; no provider/lease is opened here.
        while True:
            remaining=await asyncio.to_thread(self.state.cooldown_remaining)
            if remaining<=0:return
            await self.sleep(min(remaining,30))

    async def call(self,operation,phase):
        from telethon.errors import FloodWaitError
        async with self.lock:
            await self.wait()
            delay=self.next_call-self.clock()
            if delay>0:await self.sleep(delay)
            # A concurrent operator can extend the persisted cooldown while pacing.
            await self.wait()
            await asyncio.to_thread(self.state.status,'receiving',phase)
            self.next_call=self.clock()+self.interval
            try:return await operation()
            except (FloodWaitError,ProviderRateLimited) as exc:
                # Commit BEFORE releasing the shared gate or propagating the error.
                limited=ProviderRateLimited(exc.seconds)
                recording=asyncio.create_task(asyncio.to_thread(self.state.defer,limited.seconds))
                try:await asyncio.shield(recording)
                except asyncio.CancelledError:
                    # to_thread cancellation does not stop its SQL. Wait for the
                    # bounded transaction before shutdown releases account ownership.
                    await recording
                    raise
                raise limited from None
