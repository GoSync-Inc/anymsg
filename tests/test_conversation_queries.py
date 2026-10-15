"""Synthetic conversation views preserve source text and qualify chronology."""
from datetime import datetime, timezone

import pytest
from psycopg.types.json import Jsonb


def test_normalized_message_kind_keeps_source_and_media_phases():
    from messaging_service.conversation import present_message
    original = {'id':'voice','body':'','media_kind':'voice','message_kind':'voice',
        'direction':'incoming','media_duration_seconds':12,
        'attachments':[{'download_status':'queued','extraction_status':'awaiting_download',
            'enrichment_status':'awaiting_download'}]}
    result=present_message(dict(original),1)
    assert result['kind']=='voice' and result['body']==''
    assert 'Голосовое' in result['display_text'] and '0:12' in result['display_text']
    assert 'ожидает загрузки' in result['display_text']
    assert result['attachments']==original['attachments']
    unknown=present_message({'id':'u','body':'','direction':'outgoing'},2)
    assert unknown['kind']=='unknown' and 'неизвестного типа' in unknown['display_text']
    system=present_message({'id':'s','body':'','is_service':True},3)
    assert system['kind']=='system'
    photo=present_message({'id':'p','body':'caption','message_kind':'photo'},4)
    assert photo['display_text']=='caption\n[Фото]' and photo['body']=='caption'


def test_transcript_is_derived_from_the_presented_message_and_warning():
    from messaging_service.conversation import present_message,presentation
    rows=[present_message({'id':'one','body':'Need oats','direction':'outgoing',
        'sent_at':datetime(2026,1,1,tzinfo=timezone.utc),'order_status':'ambiguous'},1)]
    result=presentation(rows,order='asc',include_system=False)
    assert result['view']=='conversation' and result['contract_version']==1
    assert 'Вы' in result['transcript'] and 'Need oats' in result['transcript']
    assert result['warnings'] and 'неизвестен' in result['transcript']
    assert rows[0]['id']=='one' and rows[0]['position']==1


def add(c,mid,provider,*,account='telegram-demo',body='message',direction='incoming',service=False,kind=None):
    chat='telegram-demo-chat' if account=='telegram-demo' else 'whatsapp-demo-1-chat'
    c.execute('''INSERT INTO messages(id,account,chat,provider_id,sender,body,sent_at,direction)
        VALUES(%s,%s,%s,%s,'synthetic-peer',%s,'2026-12-01T12:00:00Z',%s)''',
        (mid,account,chat,provider,body,direction))
    metadata={'is_service':service,'timestamp_resolution':'second'}
    if kind:metadata['message_kind']=kind
    c.execute('INSERT INTO message_metadata(message,revision,metadata) VALUES(%s,1,%s)',(mid,Jsonb(metadata)))


def test_numeric_telegram_ties_and_service_filter_before_limit_and_neighbors(seeded):
    s,a,b=seeded
    with s.pool.connection() as c:
        add(c,'z-first','9')
        add(c,'a-middle','10',body='')
        add(c,'b-system','11',body='',service=True)
        add(c,'c-last','12',body='',kind='photo')
    result=s.get_messages(b,'telegram-demo-chat',view='conversation',from_time='2026-12-01T00:00:00Z',limit=2)
    assert [r['id'] for r in result['items']]==['z-first','a-middle']
    assert [r['position'] for r in result['items']]==[1,2]
    assert result['items'][0]['order_basis']=='provider_message_id'
    page=s.get_messages(b,'telegram-demo-chat',view='conversation',from_time='2026-12-01T00:00:00Z',
        after=result['next_cursor'],limit=2)
    assert [r['id'] for r in page['items']]==['c-last']
    context=s.get_context(b,'a-middle',view='conversation',before_count=1,after_count=1)
    assert [r['id'] for r in context['context']]==['z-first','a-middle','c-last']
    assert context['context'][1]['kind']=='unknown'
    raw=s.get_context(b,'a-middle',view='conversation',include_system=True,before_count=1,after_count=1)
    assert raw['context'][-1]['kind']=='system'
    # Archived REST/Store callers retain the historical internal-ID ordering.
    legacy=s.get_messages(b,'telegram-demo-chat',from_time='2026-12-01T00:00:00Z')
    assert [r['id'] for r in legacy['items']]==['a-middle','b-system','c-last','z-first']
    assert 'kind' not in legacy['items'][0]


def test_whatsapp_tie_is_ambiguous_even_when_other_actor_is_outside_filtered_page(seeded):
    s,a,b=seeded
    with s.pool.connection() as c:
        add(c,'wa-one','false_remote_A',account='whatsapp-demo-1',body='Hello')
        add(c,'wa-two','true_remote_B',account='whatsapp-demo-1',body='Order oats',direction='outgoing')
    result=s.search(a,'Order oats',view='conversation',chat='whatsapp-demo-1-chat',limit=1)
    row=result['items'][0]
    assert row['order_status']=='ambiguous' and row['order_basis']=='timestamp_only'
    assert row['timestamp_precision']=='second'
    assert result['conversation_view']['warnings']
    assert 'millisecond' not in row
    assert 'Order oats' in result['conversation_view']['transcript']
    from messaging_service.store import APIError
    with pytest.raises(APIError,match='not_found'):
        s.get_context(b,'wa-two',view='conversation')


def test_conversation_cursor_is_bound_to_view_and_invalidates_backfill(seeded):
    from messaging_service.store import APIError
    s,a,b=seeded
    with s.pool.connection() as c:
        add(c,'tie-10','10');add(c,'tie-20','20');add(c,'tie-30','30')
    first=s.get_messages(b,'telegram-demo-chat',view='conversation',limit=1,from_time='2026-12-01T00:00:00Z')
    cursor=first['next_cursor']
    assert cursor.startswith('q1.')
    with pytest.raises(APIError,match='invalid_cursor'):
        s.get_messages(b,'telegram-demo-chat',after=cursor,limit=1,from_time='2026-12-01T00:00:00Z')
    with s.pool.connection() as c:add(c,'late-middle','15')
    with pytest.raises(APIError,match='cursor_changed'):
        s.get_messages(b,'telegram-demo-chat',view='conversation',after=cursor,limit=1,from_time='2026-12-01T00:00:00Z')


def test_context_does_not_promote_its_middle_page_to_latest_turn(seeded):
    s,a,b=seeded
    with s.pool.connection() as c:
        add(c,'middle','10',direction='incoming')
        add(c,'head','20',direction='outgoing')
    result=s.get_context(b,'middle',view='conversation',before_count=0,after_count=0)
    assert result['context'][0]['id']=='middle'
    assert result['conversation']['latest_known_meaningful_message']['id']=='head'
    assert result['conversation']['last_turn']=='owner'
    assert result['conversation']['suggested_action']=='unclear'  # no verified provider head


def test_conversation_compact_caps_source_body_and_keeps_full_mode(seeded):
    s,a,b=seeded
    text='A synthetic long body. '*100
    with s.pool.connection() as c:
        add(c,'long-text','99',body=text)
    short=s.search(b,chat='telegram-demo-chat',view='conversation',body_chars=12,order='desc')
    row=short['items'][0]
    assert row['body']==text[:12] and row['body_truncated'] is True
    assert len(row['display_text'])<=12
    full=s.get_context(b,'long-text',view='conversation',before_count=0,after_count=0)
    assert full['message']['body']==text
    compact=s.get_context(b,'long-text',view='conversation',compact=True,before_count=0,after_count=0)
    assert compact['message']['body']==text[:320] and compact['message']['body_truncated'] is True
    assert len(compact['conversation_view']['transcript'])<1000


def test_conversation_context_boundaries_continue_without_duplicates(seeded):
    s,a,b=seeded
    with s.pool.connection() as c:
        add(c,'before-ref','8');add(c,'center-ref','9');add(c,'after-ref','10')
    context=s.get_context(b,'center-ref',view='conversation',before_count=0,after_count=0)
    window=context['context_window']
    older=s.get_messages(b,'telegram-demo-chat',view='conversation',order=window['older_order'],
        before=window['older_cursor'])
    newer=s.get_messages(b,'telegram-demo-chat',view='conversation',order=window['newer_order'],
        after=window['newer_cursor'])
    assert older['items'][0]['id']=='before-ref'
    assert [r['id'] for r in newer['items']]==['after-ref']
    assert 'center-ref' not in [r['id'] for r in older['items']+newer['items']]


def test_transcript_does_not_frame_group_chatter_or_system_events_as_a_personal_reply():
    from messaging_service.conversation import present_message,presentation
    group=present_message({'id':'g','body':'A group announcement','chat_type':'group','direction':'incoming'},1)
    system=present_message({'id':'s','body':'','is_service':True,'direction':'outgoing'},2)
    transcript=presentation([group,system],include_system=True)['transcript']
    assert 'Участник → группа' in transcript
    assert '2. Служебное событие' in transcript
    assert 'Собеседник → Вы' not in transcript and 'Вы → собеседник' not in transcript


@pytest.mark.parametrize('hidden_reason',['system','deleted'])
def test_empty_conversation_context_still_offers_bound_navigation(seeded,hidden_reason):
    s,a,b=seeded
    with s.pool.connection() as c:
        add(c,'hidden-before','8');add(c,'hidden-anchor','9',service=hidden_reason=='system')
        add(c,'hidden-after','10')
        if hidden_reason=='deleted':c.execute("UPDATE messages SET deleted=true WHERE id='hidden-anchor'")
    result=s.get_context(b,'hidden-anchor',view='conversation',before_count=0,after_count=0)
    assert result['context']==[] and result['context_window']['anchor_hidden'] is True
    window=result['context_window']
    assert window['older_cursor'].startswith('q1.') and window['newer_cursor'].startswith('q1.')
    older=s.get_messages(b,'telegram-demo-chat',view='conversation',order=window['older_order'],
        before=window['older_cursor'])
    newer=s.get_messages(b,'telegram-demo-chat',view='conversation',order=window['newer_order'],
        after=window['newer_cursor'])
    assert older['items'][0]['id']=='hidden-before'
    assert [r['id'] for r in newer['items']]==['hidden-after']
