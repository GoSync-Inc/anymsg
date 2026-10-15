"""Ambiguous WhatsApp receipts remain terminal unknown; synthetic private IPC."""
import asyncio
import json
from types import SimpleNamespace

from messaging_service.outgoing import OutgoingWorker
from messaging_service.whatsapp_collector import NodeProvider


def test_whatsapp_unverified_receipt_frame_is_terminal_unknown_and_never_resent(tmp_path):
    async def run():
        provider = NodeProvider({}, tmp_path / 'unused-config', 3)
        provider.send_reader = asyncio.StreamReader()
        provider.send_reader.feed_data(json.dumps({'id': 1, 'error': 'send_receipt_mismatch'}).encode() + b'\n')
        sends, finished, preflights, disconnects = [], [], [], []
        pending = [True]

        async def request(method, params, *, sending=False):
            sends.append((method, params, sending))
            return 1

        async def disconnect():
            disconnects.append(True)

        provider.request, provider.disconnect = request, disconnect

        class Worker(OutgoingWorker):
            def claim(self):
                if not pending:
                    return None
                pending.pop()
                return {'id': 'synthetic-send', 'provider_chat': '123@c.us',
                        'body': 'Synthetic message', 'expires_at': 2000000000}
            def preflight(self, job):
                preflights.append(job['id'])
                return job
            def finish(self, job, status, provider_id=None, code=None):
                finished.append((status, provider_id, code))

        worker = Worker(SimpleNamespace(cooldown_remaining=lambda: 0), provider.send_text,
                        gate=provider.send_gate)
        assert await worker.once()
        assert not await worker.once()
        assert finished == [('unknown', None, 'send_result_unknown')]
        assert len(sends) == 1 and sends[0][0] == 'send_text' and sends[0][2] is True
        assert sends[0][1]['chat'] == '123@c.us'
        assert preflights == ['synthetic-send', 'synthetic-send']
        assert disconnects == []  # A complete error frame leaves IPC synchronized.
    asyncio.run(run())
