"""Conversation summaries keep observed content and uncertainty in both transports."""
from messaging_service.presentation import render


def message():
    return dict(id='synthetic-outgoing', account='synthetic-wa', chat='synthetic-chat',
        sender='owner', direction='outgoing', body='Original caption', revision=1,
        kind='photo', is_service=False, display_text='Original caption\n[Фото; файл загружается]',
        position=1, order_basis='timestamp_only', order_status='ambiguous',
        timestamp_resolution='second', first_observed_at='2026-01-01T00:00:01Z',
        first_observed_basis='archive_revision_observation', conversation_view_version=1,
        ordering_warning='same_timestamp_order_unknown')


def test_compact_conversation_preserves_original_and_order_uncertainty():
    row=message()
    result=render('get_messages', {'items':[row], 'conversation_view':{'contract_version':1,
        'transcript':'1. Вы → собеседник: '+row['display_text'],
        'warnings':['same_timestamp_order_unknown']}}, 'compact')
    output=result['items'][0]
    for field in ('body','kind','is_service','display_text','position','order_basis',
        'order_status','timestamp_resolution','first_observed_at','first_observed_basis','ordering_warning'):
        assert output.get(field)==row[field], field
    assert output['display_text'] in result['conversation_view']['transcript']
    assert result['conversation_view']['warnings']==['same_timestamp_order_unknown']


def test_context_compact_keeps_actual_chat_state_separate_from_middle_window():
    state={'last_turn':'unknown','suggested_action':'unclear','head_freshness':'unknown',
        'basis':{'code':'head_unverified','heuristic':True,'evidence':[]}}
    result=render('get_context', {'message':message(),'context':[message()],
        'attachments':[],'pending_attachments':[], 'conversation':state,
        'conversation_view':{'contract_version':1,'transcript':'synthetic transcript'},
        'chat_sync':{'state':'unknown'}},'compact')
    assert result['conversation']==state
    assert result['message'].get('body')=='Original caption'
    assert result['conversation_view']['transcript']=='synthetic transcript'


def test_legacy_compact_archive_does_not_gain_unbounded_original_body():
    row=message()
    row.pop('conversation_view_version')
    row['body']='x'*5000
    result=render('search',{'items':[row]},'compact')
    assert 'body' not in result['items'][0]
    assert len(result['items'][0]['snippet'])==96


def test_mcp_defaults_to_conversation_while_rest_preserves_archive(seeded):
    from starlette.testclient import TestClient
    from messaging_service.app import create_app
    store,key,_=seeded
    headers={'Authorization':'Bearer '+key,'Accept':'application/json, text/event-stream'}
    with TestClient(create_app(store,['testserver'])) as client:
        rest=client.post('/v1/get_messages',headers=headers,json={'chat_id':'telegram-demo-chat'})
        assert rest.status_code==200
        assert 'conversation_view' not in rest.json()
        rpc=client.post('/mcp',headers=headers,json={'jsonrpc':'2.0','id':1,
            'method':'tools/call','params':{'name':'get_messages',
                'arguments':{'chat_id':'telegram-demo-chat'}}})
        assert rpc.status_code==200
        result=rpc.json()['result']
        assert not result.get('isError'), result
        assert result['structuredContent'].get('conversation_view',{}).get('contract_version')==1
        assert all('body' in m and 'kind' in m for m in result['structuredContent']['items'])


def test_rest_explicit_conversation_and_strict_options(seeded):
    from starlette.testclient import TestClient
    from messaging_service.app import create_app
    store,key,_=seeded
    headers={'Authorization':'Bearer '+key}
    with TestClient(create_app(store,['testserver'])) as client:
        result=client.post('/v1/get_context',headers=headers,json={
            'message_id':'demo-tg-1','view':'conversation','include_system':False})
        assert result.status_code==200, result.json()
        data=result.json()
        assert data['conversation_view']['contract_version']==1
        assert data['conversation']['suggested_action']=='unclear'
        for extra in ({'view':'raw'},{'include_system':'yes'},{'include_system':1}):
            bad=client.post('/v1/get_context',headers=headers,json={'message_id':'demo-tg-1',**extra})
            assert bad.status_code==400


def test_conversation_compact_wire_is_bounded_with_large_source_text(seeded):
    from starlette.testclient import TestClient
    from messaging_service.app import create_app
    from test_client_contract import rpc
    store,key,_=seeded
    with store.pool.connection() as c:
        c.execute("UPDATE messages SET body=repeat('long text ',2000)")
    with TestClient(create_app(store,['testserver'])) as client:
        page=rpc(client,key,'search',{'limit':5})
        cards=rpc(client,key,'list_chats',{'limit':5})
    for response in (page,cards):
        assert response.status_code==200
        assert not response.json()['result'].get('isError')
        assert len(response.content)<65536
    result=page.json()['result']['structuredContent']
    assert result['conversation_view']['contract_version']==1
    assert all(len(m['body'])<=320 and m['body_truncated'] for m in result['items'])
    for card in cards.json()['result']['structuredContent']['items']:
        head=card['conversation']['latest_known_meaningful_message']
        if head:
            assert len(head['body'])<=1000
