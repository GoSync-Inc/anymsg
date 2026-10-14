from starlette.testclient import TestClient

from messaging_service.app import create_app
from messaging_service.archive import Archive
from messaging_service.collector_state import validate_capture
from test_archive import event
from test_file_index import add_file,drain,worker_for


def test_file_tools_rest_mcp_parity_and_acl(seeded):
    s,a,b=seeded
    add_file(s,'api-index-file','demo-tg-1','Проверка telescope\n'.encode(),'indexed.txt')
    drain(worker_for(s))
    with TestClient(create_app(s,['testserver'])) as client:
        headers={'Authorization':'Bearer '+b,'Accept':'application/json, text/event-stream'}
        for operation,args in [('search_files',{'query':'telescope','chats':['telegram-demo-chat']}),
                               ('get_file_text',{'attachment_id':'api-index-file','limit':8})]:
            rest=client.post('/v1/'+operation,json=args,headers=headers)
            rpc=client.post('/mcp',headers=headers,json={'jsonrpc':'2.0','id':1,'method':'tools/call',
                'params':{'name':operation,'arguments':args}}).json()['result']
            assert rest.status_code==200 and not rpc.get('isError')
            assert rest.json()==rpc['structuredContent']
        s.set_access('agent-b',chats=[])
        assert client.post('/v1/search_files',headers=headers,json={'query':'telescope'}).json()['items']==[]
        assert client.post('/v1/get_file_text',headers=headers,json={'attachment_id':'api-index-file'}).status_code==404
        s.set_access('agent-b',permissions=[])
        assert client.post('/v1/search_files',headers=headers,json={}).status_code==403


def test_chat_name_enrichment_cannot_be_replaced_by_fallback_id(seeded):
    s,_,_=seeded;ar=Archive(s)
    ar.register('chat-label-test','telegram','Fixture',discover=True)
    key=s.issue_key('chat-label-reader',['chat-label-test'])
    ar.ingest(event(account='chat-label-test',chat_title='100'))
    ar.ingest(event(account='chat-label-test',chat_title='Named group',origin='backfill'))
    ar.ingest(event(account='chat-label-test',chat_title='100',provider_id='99'))
    assert s.list_chats(key)['items'][0]['title']=='Named group'


def test_same_sender_id_in_two_accounts_keeps_names_separate(seeded):
    s,_,_=seeded;ar=Archive(s)
    for account,name in [('person-one','Alice'),('person-two','Bob')]:
        ar.register(account,'telegram','Fixture',discover=True)
        ar.ingest(event(account=account,sender_profile={'display_name':name}))
    key=s.issue_key('person-one-reader',['person-one'])
    assert s.search_people(key,'Bob')['items']==[]
    assert s.search_people(key,'Alice')['items'][0]['sender_id']=='42'


def test_whatsapp_capture_accepts_only_bounded_reported_identity():
    capture={'kind':'whatsapp_event','account':'fixture','change':'message','origin':'live',
        'observed_at':'2026-10-01T00:00:00Z','event':{'chatId':'123@c.us','messageId':'wa1',
        'senderId':'123@c.us','senderName':'Fixture Person','body':'hello',
        'timestamp':1790812800,'fromMe':False,'hasMedia':False}}
    saved=validate_capture(capture)
    assert saved['envelope']['sender_profile']['display_name']=='Fixture Person'
