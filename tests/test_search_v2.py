"""Morphology, ranked pages, scoped metadata, centred windows and durable views."""
from datetime import datetime,timezone

import pytest
from psycopg.types.json import Jsonb

from messaging_service.search_query import compile_query,cursor_binding,decode_cursor,encode_cursor
from messaging_service.store import APIError


def add(s,mid,body,*,account='telegram-demo',chat='telegram-demo-chat',sender='person',day=5,direction='incoming'):
    with s.pool.connection() as c:
        c.execute('''INSERT INTO messages(id,account,chat,provider_id,sender,body,sent_at,direction)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s)''',
            (mid,account,chat,mid,sender,body,datetime(2026,10,day,12,tzinfo=timezone.utc),direction))


def test_query_helper_bounds_and_no_query_interpolation():
    needle="x'); DELETE FROM messages; --"
    plan=compile_query(needle,'exact')
    assert needle not in plan.cte and needle in plan.params
    assert 'LIKE' in plan.predicate('m')
    assert 'literal_pattern' in plan.cte
    for kwargs in [{'search_language':'missing'},{'query_mode':'sql'},{'query':'x\x00'}]:
        with pytest.raises(APIError):compile_query(**kwargs)
    p={'id':'agent','scope_version':1}
    binding=cursor_binding(p,'search',{'q':'passport'})
    token=encode_cursor(binding,{'id':'msg'})
    assert decode_cursor(token,binding)=={'id':'msg'}
    with pytest.raises(APIError):decode_cursor(token,cursor_binding(p,'search',{'q':'other'}))
    with pytest.raises(APIError):decode_cursor('q1.'+'x'*5000,binding)
    import base64
    nested='q1.'+base64.urlsafe_b64encode(('['*1200+']'*1200).encode()).decode()
    with pytest.raises(APIError,match='invalid_cursor'):decode_cursor(nested,binding)


def test_native_morphology_simple_exact_and_mixed_queries(seeded):
    s,a,b=seeded
    add(s,'morph-ru','После терапии проверили копию паспорта и подпись по договору. Running quickly.')
    add(s,'morph-id','Saya membaca buku tentang pembayaran.')
    for query in ('терапия','паспорт','договор'):
        result=s.search(b,query,search_language='russian')
        assert [r['id'] for r in result['items']]==['morph-ru']
        assert result['items'][0]['match_type']=='morphology_russian'
        assert s.search(b,query,search_language='simple')['items']==[]
    assert s.search(b,'run',search_language='english')['items'][0]['id']=='morph-ru'
    assert s.search(b,'паспорт run')['items'][0]['id']=='morph-ru'
    assert s.search(b,'baca',search_language='indonesian')['items'][0]['id']=='morph-id'
    assert s.search(b,'терапия',query_mode='exact')['items']==[]
    assert s.search(b,'ТЕРАПИИ',query_mode='exact')['items'][0]['id']=='morph-ru'
    add(s,'literal','Code 100%_literal')
    add(s,'wildcard-decoy','Code 100XXliteral')
    assert [r['id'] for r in s.search(b,'100%_',query_mode='exact')['items']]==['literal']


def test_relevance_cursor_bound_to_query_scope_and_revision(seeded):
    s,a,b=seeded
    add(s,'rank-1','passport')
    add(s,'rank-2','passport passport passport')
    add(s,'rank-hidden','passport '*50,account='whatsapp-demo-2',chat='whatsapp-demo-2-chat')
    first=s.search(b,'passport',order='relevance',limit=1)
    assert first['items'][0]['id']=='rank-2'
    second=s.search(b,'passport',order='relevance',after=first['next_cursor'],limit=1)
    assert second['items'][0]['id']=='rank-1' and second['next_cursor'] is None
    with pytest.raises(APIError):s.search(b,'different',order='relevance',after=first['next_cursor'])
    s.set_access('agent-b',chats=['telegram-demo-chat'])
    with pytest.raises(APIError):s.search(b,'passport',order='relevance',after=first['next_cursor'])
    first=s.search(b,'passport',order='relevance',limit=1)
    with s.pool.connection() as c:c.execute("UPDATE messages SET revision=revision+1 WHERE id='rank-2'")
    with pytest.raises(APIError,match='cursor_changed'):
        s.search(b,'passport',order='relevance',after=first['next_cursor'])


def test_fallback_is_explicit_and_does_not_union_approximate_matches(seeded):
    s,a,b=seeded
    add(s,'fuzzy','The passport is here')
    assert s.search(b,'passporrt')['items']==[]
    result=s.search(b,'passporrt',fuzzy=True)
    assert result['search']['fuzzy_used'] and result['items'][0]['id']=='fuzzy'
    assert result['items'][0]['match_type']=='trigram_fallback'
    exact=s.search(b,'passport',fuzzy=True)
    assert not exact['search']['fuzzy_used']
    for query in ('pa','passport file','x'*65):
        with pytest.raises(APIError,match='invalid_fuzzy_query'):s.search(b,query,fuzzy=True)


def test_snippet_near_match_and_compact_output_keeps_coverage(seeded):
    s,a,b=seeded
    add(s,'long-body','irrelevant '*200+'копия паспорта находится здесь '+'tail '*100)
    result=s.search(b,'паспорт',compact=True,body_chars=10,include_coverage=False)
    row=result['items'][0]
    assert 'паспорт' in row['snippet'] and '[[' in row['snippet']
    assert len(row['snippet'])<=800 and 'body' not in row
    assert 'coverage' in result and 'sources' not in result
    full=s.get_messages(b,'telegram-demo-chat',after='demo-tg-2',body_chars=10)
    assert full['items'][0]['body_truncated'] and len(full['items'][0]['body'])==10


def test_decorations_same_chat_and_live_profile_source_only(seeded):
    s,a,b=seeded
    add(s,'named-source','named',sender='known')
    with s.pool.connection() as c:
        c.execute("UPDATE chats SET chat_type='private' WHERE id='telegram-demo-chat'")
        c.execute('''INSERT INTO sender_profiles(id,account,chat,sender,display_name,username,source_message,source_sent_at)
            VALUES('profile','telegram-demo','telegram-demo-chat','known','Visible Name','visible','named-source',now())''')
        c.execute('INSERT INTO message_metadata(message,revision,metadata) VALUES(%s,1,%s)',
            ('named-source',Jsonb({'reply_to_provider_id':'12','forwarded':False,'media_kind':'photo'})))
    row=s.search(b,'named')['items'][0]
    assert (row['sender_name'],row['username'],row['chat_type'],row['direction'])==('Visible Name','visible','private','incoming')
    assert row['reply_to_provider_id']=='12' and row['forwarded'] is False and row['media_kind']=='photo'
    context=s.get_context(b,'named-source')
    assert not any(k.startswith('document') for k in context['message'])
    add(s,'same-sender','visible text',sender='known')
    with s.pool.connection() as c:c.execute("UPDATE messages SET deleted=true WHERE id='named-source'")
    assert s.search(b,'visible text')['items'][0]['sender_name'] is None


def test_centered_context_separate_counts_and_equal_timestamp_neighbors(seeded):
    s,a,b=seeded
    for n in range(12):add(s,f'around-{n:02}','nearby')
    result=s.get_context(b,'around-06',before_count=2,after_count=3)
    assert [r['id'] for r in result['context']]==[f'around-{n:02}' for n in range(4,10)]
    assert result['context_window']['has_more_before'] and result['context_window']['has_more_after']
    assert all('direction' in r for r in result['context'])
    assert len(s.get_context(b,'around-06',before_count=0,after_count=0)['context'])==1


def test_activity_order_filters_unknown_provider_state_and_heuristic(seeded):
    s,a,b=seeded
    with s.pool.connection() as c:
        c.execute("UPDATE chats SET chat_type='private' WHERE account='telegram-demo'")
        c.execute("UPDATE chats SET chat_type='channel' WHERE account='whatsapp-demo-1'")
    first=s.list_chats(a,order='activity',limit=1)
    assert first['items'][0]['id']=='whatsapp-demo-1-chat'
    second=s.list_chats(a,order='activity',after=first['next_cursor'],limit=1)
    assert second['items'][0]['id']=='telegram-demo-chat'
    assert second['items'][0]['provider_unread'] is None and second['items'][0]['muted'] is None
    assert [r['id'] for r in s.list_chats(a,awaiting_reply=True,awaiting_reply_days=0)['items']]==['telegram-demo-chat']
    assert s.list_chats(b,accounts=['whatsapp-demo-1'])['items']==[]
    with pytest.raises(APIError):s.list_chats(a,order='activity',query='changed',after=first['next_cursor'])


def test_filtered_consumer_ack_does_not_skip_other_views_or_change_filter(seeded):
    s,a,b=seeded
    first=s.get_updates(a,'only-tg',chats=['telegram-demo-chat'],limit=1)
    assert [e['message'] for e in first['items']]==['demo-tg-1'] and first['has_more']
    with pytest.raises(APIError,match='consumer_filter_mismatch'):
        s.get_updates(a,'only-tg',chats=['whatsapp-demo-1-chat'])
    s.ack_updates(a,'only-tg',first['receipt'],first['ack_version'])
    second=s.get_updates(a,'only-tg',chats=['telegram-demo-chat'],limit=1)
    assert [e['message'] for e in second['items']]==['demo-tg-2']
    s.ack_updates(a,'only-tg',second['receipt'],second['ack_version'])
    assert s.get_updates(a,'only-tg',chats=['telegram-demo-chat'])['items']==[]
    assert len(s.get_updates(a,'all')['items'])==3
    assert s.get_updates(a,'live-only',origins=['live'])['items']==[]
    assert s.get_updates(a,'no-chats',chats=[])['items']==[]
    assert s.get_updates(b,'hidden',accounts=['whatsapp-demo-1'])['items']==[]


def test_people_transliteration_is_candidate_and_grouping_never_crosses_accounts(seeded):
    from messaging_service.archive import Archive
    from test_archive import event
    s,a,b=seeded;ar=Archive(s)
    for account in ('people-one','people-two'):
        ar.register(account,'telegram','Fixture',discover=True)
        for chat in ('100','200'):
            ar.ingest(event(account=account,chat=chat,sender_profile={'display_name':'Юлия Пример','username':''}))
    key=s.issue_key('candidate-reader',['people-one','people-two'])
    result=s.search_people(key,'Yuliya',transliterate=True,group_by_sender=True)
    assert len(result['items'])==2
    assert all(len(r['observations'])==2 for r in result['items'])
    assert all(r['match_type']=='transliteration_candidate' for r in result['items'])
    assert len(s.search_people(key,'Юля',transliterate=True)['items'])==4
    visible=result['items'][0]['observations'][0]['chat']
    s.set_access('candidate-reader',chats=[visible])
    limited=s.search_people(key,'Yuliya',transliterate=True,group_by_sender=True)
    assert len(limited['items'])==1 and len(limited['items'][0]['observations'])==1
