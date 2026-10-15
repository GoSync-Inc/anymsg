"""Exact native wire receipts survive PN→LID routing without granting alias reads."""
import asyncio

import pytest

from messaging_service.archive import Archive
from messaging_service.collector_state import CollectorState
from messaging_service.envelope import stable_id
from messaging_service.outgoing import OutgoingWorker
from test_archive import event

ACCOUNT = 'native-reference'
PN, LID = '123456@c.us', '987654@lid'
PUBLIC, WIRE = 'true_' + PN + '_ABC123', 'true_' + LID + '_ABC123'
REFERENCE = {'provider_chat_id': LID, 'provider_message_id': WIRE}


@pytest.fixture
def native_sender(seeded):
    store, _, _ = seeded
    archive = Archive(store)
    archive.register(ACCOUNT, 'whatsapp', 'Synthetic native', discover=True)
    archive.ingest(event(account=ACCOUNT, platform='whatsapp', chat=PN,
        provider_id='false_' + PN + '_BEFORE', sender=PN))
    key = store.issue_key('native-sender', [ACCOUNT], permissions=['read', 'send'])
    epoch = archive.acquire(ACCOUNT, 'native-reference-worker')
    state = CollectorState(archive, ACCOUNT, ('native-reference-worker', epoch))
    state.phase('connection', 'connected', 'provider_ready')
    archive.set_sending(ACCOUNT, True)
    queued = store.send_message(key, account=ACCOUNT, chat_id=stable_id('chat', ACCOUNT, PN),
        text='Synthetic routed send', request_id='native-reference-0001')
    yield store, archive, key, state, queued
    archive.release(ACCOUNT, 'native-reference-worker', epoch)


def test_wire_reference_is_persisted_links_history_receipts_and_provenance(native_sender):
    store, archive, key, state, queued = native_sender
    calls = []
    async def send(job):
        calls.append(job)
        return {'provider_message_id': PUBLIC, 'archive_reference': REFERENCE}
    worker = OutgoingWorker(state, send)
    assert asyncio.run(worker.once()) and not asyncio.run(worker.once())
    mid = archive.ingest(event(account=ACCOUNT, platform='whatsapp', chat=LID,
        provider_id=WIRE, direction='outgoing', sender='111@lid', body='Synthetic routed send'))['message']
    from messaging_service.receipts import record
    with store.pool.connection() as c:
        record(c, {'kind':'message_receipt', 'account':ACCOUNT, 'platform':'whatsapp',
            'chat':LID, 'provider_id':WIRE, 'max_id':None, 'state':'delivered',
            'basis':'whatsapp_message_ack', 'audience':'private_peer',
            'observed_at':'2026-10-15T07:00:00Z', 'provider_at':None})
        row = c.execute('SELECT * FROM outgoing_messages WHERE id=%s', (queued['send_id'],)).fetchone()
        assert row['provider_chat'] == PN  # Request/idempotency remains immutable.
        assert row['archive_reference'] == REFERENCE
    result = store.get_send_status(key, queued['send_id'])
    assert result['status'] == 'sent' and result['provider_message_id'] == PUBLIC
    assert result['message_id'] == mid and result['archive_reference']['provider_message_id'] == WIRE
    assert result['recipient_delivery_confirmed'] is True
    assert store.get_context(key, mid)['message']['agent_send']['send_id'] == queued['send_id']
    assert store.send_message(key, account=ACCOUNT, chat_id=queued['chat_id'],
        text='Synthetic routed send', request_id='native-reference-0001')['send_id'] == queued['send_id']
    assert len(calls) == 1
    # Granting send/read on a PN chat never grants read of its LID alias.
    store.set_access('native-sender', chats=[queued['chat_id']])
    restricted = store.get_send_status(key, queued['send_id'])
    assert restricted['status'] == 'sent' and restricted['message_id'] is None
    assert restricted['archive_reference'] is None and restricted['archive_reference_state'] == 'scope_restricted'
    assert LID not in str(restricted)
    assert restricted['delivery_receipt']['state'] == 'unknown'


@pytest.mark.parametrize('reference', [
    {}, None, {'provider_chat_id':LID, 'provider_message_id':'true_' + LID + '_DIFFERENT'},
    {'provider_chat_id':'999@g.us', 'provider_message_id':'true_999@g.us_ABC123'},
    {'provider_chat_id':LID, 'provider_message_id':WIRE, 'extra':True},
    {'provider_chat_id':LID, 'provider_message_id':PUBLIC},
])
def test_malformed_wire_reference_is_terminal_unknown_without_retry(native_sender, reference):
    store, _, key, state, queued = native_sender
    calls = []
    async def send(job):
        calls.append(job)
        return {'provider_message_id':PUBLIC, 'archive_reference':reference}
    worker = OutgoingWorker(state, send)
    assert asyncio.run(worker.once()) and not asyncio.run(worker.once())
    result = store.get_send_status(key, queued['send_id'])
    assert result['status'] == 'unknown' and result['provider_message_id'] is None
    assert len(calls) == 1


def test_operation_wire_reference_is_scope_filtered(native_sender):
    from messaging_service.commands import CommandWorker
    store, _, key, state, queued = native_sender
    store.set_messenger_operations(ACCOUNT, ['send_poll'])
    args = {'account':ACCOUNT, 'operation':'send_poll', 'chat_id':queued['chat_id'],
        'parameters':{'question':'Synthetic?', 'options':['Yes','No'], 'multiple':False}}
    preview = store.preview_messenger_operation(key, **args)
    operation = store.execute_messenger_operation(key, **args,
        preview_digest=preview['preview_digest'], request_id='native-operation-reference')
    async def execute(job):
        return {'outcome':'confirmed', 'provider_message_id':PUBLIC,
                'data':{'archive_reference':REFERENCE}}
    assert asyncio.run(CommandWorker(state, execute).once())
    status = store.get_messenger_operation(key, operation['operation_id'])
    assert status['status'] == 'succeeded'
    assert status['result']['data']['archive_reference']['provider_message_id'] == WIRE
    store.set_access('native-sender', chats=[queued['chat_id']])
    restricted = store.get_messenger_operation(key, operation['operation_id'])
    assert restricted['result']['data']['archive_reference'] is None
    assert restricted['result']['data']['archive_reference_state'] == 'scope_restricted'
    assert LID not in str(restricted)


def test_group_reference_binds_the_entire_stanza_not_only_first_underscore():
    from messaging_service.outgoing import archive_reference
    chat = '123456@g.us'
    with pytest.raises(RuntimeError, match='send_receipt_mismatch'):
        archive_reference({'provider_chat':chat}, {
            'provider_message_id':'true_' + chat + '_A_B_111@c.us',
            'archive_reference':{'provider_chat_id':chat,
                'provider_message_id':'true_' + chat + '_A_C_222@lid'}})
    reference = {'provider_chat_id':chat, 'provider_message_id':'true_' + chat + '_A_B_222@lid'}
    assert archive_reference({'provider_chat':chat}, {
        'provider_message_id':'true_' + chat + '_A_B_111@c.us', 'archive_reference':reference}) == reference
