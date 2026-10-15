"""Bound alias resolution by candidate identities, not archive size squared."""
from time import monotonic

import pytest

from messaging_service.store import APIError
from messaging_service.whatsapp_identity import resolve_alias
from test_whatsapp_identity import identity_archive, observed


def _unrelated_archive(c, account, count=10000):
    # Owner identity is observed in every group, as it is for outgoing messages.
    # Each group also supplies unrelated identity evidence. Neither the repeated
    # owner pair nor unrelated evidence may multiply normalization of all chats.
    c.execute('''INSERT INTO chats(id,account,title,provider_chat)
        SELECT 'alias-noise-chat-'||n,%s,'Synthetic group',n||'@g.us'
        FROM generate_series(1,%s) n''', (account, count))
    c.execute('''INSERT INTO messages(id,account,chat,provider_id,sender,body,sent_at)
        SELECT 'alias-noise-owner-'||n,%s,'alias-noise-chat-'||n,'owner-'||n,
            '900@lid','Synthetic owner message',now()
        FROM generate_series(1,%s) n''', (account, count))
    c.execute('''INSERT INTO messages(id,account,chat,provider_id,sender,body,sent_at)
        SELECT 'alias-noise-other-'||n,%s,'alias-noise-chat-'||n,'other-'||n,
            (100000+n)||'@lid','Synthetic unrelated message',now()
        FROM generate_series(1,%s) n''', (account, count))
    c.execute('''INSERT INTO whatsapp_identity_observations
        (account,source_message,identity_id,pn,lid,provenance,first_observed_at,observed_at)
        SELECT %s,'alias-noise-owner-'||n,'900@lid','1234567890@c.us','900@lid',
            'whatsmeow_message_alt_v1',now(),now() FROM generate_series(1,%s) n
        UNION ALL SELECT %s,'alias-noise-other-'||n,(100000+n)||'@lid',
            (200000+n)||'@c.us',(100000+n)||'@lid','whatsmeow_lid_store_v1',now(),now()
        FROM generate_series(1,%s) n''', (account, count, account, count))
    for table in ('chats', 'messages', 'whatsapp_identity_observations'):
        c.execute('ANALYZE '+table)


def test_alias_resolution_with_repeated_owner_evidence_is_not_quadratic(identity_archive):
    s, _, state, key = identity_archive
    first = state.apply(observed(chat='900:2@lid'))
    s.set_access('identity-reader', accounts=['wa-identities', 'telegram-demo', 'whatsapp-demo-1'])
    with s.authorized(key) as (c, p):
        c.execute("SET LOCAL statement_timeout='60s'")
        _unrelated_archive(c, 'wa-identities')
        # The production statement budget, not a microbenchmark wall-clock
        # threshold. The old evidence×chats join exhausts it on this archive.
        c.execute("SET LOCAL statement_timeout='5s'")
        started = monotonic()
        row = resolve_alias(c, p, 'wa-identities', '1234567890:4@c.us')
        elapsed = monotonic()-started
        assert row['provider_chat'] == '900:2@lid'
        assert elapsed < 5
        assert resolve_alias(c, p, 'wa-identities', '9999999999@c.us') is None
    assert s.get_context(key, first['message'])['message']['provider_id'] == 'false_900:2@lid_000001'


@pytest.mark.parametrize('hidden_deleted', [False, True])
@pytest.mark.parametrize('same_phone', [False, True])
def test_hidden_historic_conflicts_still_block_alias(identity_archive, hidden_deleted, same_phone):
    s, _, state, key = identity_archive
    first = state.apply(observed())
    conflict = state.apply(observed(mid=2, chat='999@g.us',
        sender='901@lid' if same_phone else '900@lid',
        pn='1234567890@c.us' if same_phone else '1111111111@c.us'))
    visible_chat = s.get_context(key, first['message'])['message']['chat']
    if hidden_deleted:
        with s.pool.connection() as c:
            c.execute('UPDATE messages SET deleted=true WHERE id=%s', (conflict['message'],))
    s.set_access('identity-reader', chats=[visible_chat])
    with pytest.raises(APIError, match='not_found'):
        s.resolve_reference(key, 'wa-identities', '1234567890@c.us')
    assert s.resolve_reference(key, 'wa-identities', '900@lid')['chat']['id'] == visible_chat


def test_alias_requires_visible_source_even_when_original_target_is_visible(identity_archive):
    s, _, state, key = identity_archive
    source = state.apply(observed(chat='999@g.us'))
    target = observed(mid=2)
    del target['event']['identityObservations']
    result = state.apply(target)
    target_chat = s.get_context(key, result['message'])['message']['chat']
    s.set_access('identity-reader', chats=[target_chat])
    with pytest.raises(APIError, match='not_found'):
        s.resolve_reference(key, 'wa-identities', '1234567890@c.us')
    assert s.resolve_reference(key, 'wa-identities', '900@lid')['chat']['id'] == target_chat
    s.set_access('identity-reader', chats=None)
    assert s.resolve_reference(key, 'wa-identities', '1234567890@c.us')['chat']['id'] == target_chat
    with s.pool.connection() as c:
        c.execute('UPDATE messages SET deleted=true WHERE id=%s', (source['message'],))
    with pytest.raises(APIError, match='not_found'):
        s.resolve_reference(key, 'wa-identities', '1234567890@c.us')


def test_alias_ambiguity_only_counts_authorized_original_chats(identity_archive):
    s, _, state, key = identity_archive
    first = state.apply(observed())
    state.apply(observed(mid=2, chat='1234567890@c.us'))
    chat = s.get_context(key, first['message'])['message']['chat']
    s.set_access('identity-reader', chats=[chat])
    assert s.resolve_reference(key, 'wa-identities', '1234567890:2@c.us')['chat']['id'] == chat
    s.set_access('identity-reader', accounts=['telegram-demo'], chats=None)
    with pytest.raises(APIError, match='not_found'):
        s.resolve_reference(key, 'wa-identities', '1234567890:2@c.us')
