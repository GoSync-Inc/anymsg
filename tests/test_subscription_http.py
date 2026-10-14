"""Real adapters plus isolated PostgreSQL, using only synthetic archive events."""
from starlette.testclient import TestClient

from messaging_service.app import create_app
from messaging_service.archive import Archive
from messaging_service.subscriptions import match_subscriptions
from test_archive import event


def rpc(client,key,name,arguments):
    return client.post('/mcp',headers={'Authorization':'Bearer '+key,
        'Accept':'application/json, text/event-stream'},json={'jsonrpc':'2.0','id':1,
        'method':'tools/call','params':{'name':name,'arguments':arguments}})


def source(seeded):
    store,legacy,_=seeded
    archive=Archive(store)
    archive.register('subscription-api','telegram','Synthetic subscription API',discover=True)
    first=archive.ingest(event(account='subscription-api'))['message']
    key=store.issue_key('subscription-api-reader',['subscription-api'],
        permissions=['read','subscription-write'],key_permissions=['read','subscription-write'])
    chat=store.get_context(key,first)['message']['chat']
    return store,archive,key,legacy,chat


def test_subscription_real_rest_mcp_matching_is_passive_and_revision_bound(seeded):
    store,archive,key,_,chat=source(seeded)
    with TestClient(create_app(store,['testserver'])) as client:
        created=rpc(client,key,'create_subscription',{'chat_ids':[chat],'sender_ids':['42']})
        assert created.json()['result']['isError'] is False
        definition=created.json()['result']['structuredContent']
        sid=definition['subscription_id']
        mid=archive.ingest(event(account='subscription-api',provider_id='18'))['message']
        assert match_subscriptions(store)['matched']==1
        args={'subscription_id':sid}
        rest=client.post('/v1/get_subscription_events',headers={'Authorization':'Bearer '+key},json=args)
        mcp=rpc(client,key,'get_subscription_events',args)
        assert rest.status_code==200
        assert rest.json()==mcp.json()['result']['structuredContent']
        page=rest.json()
        assert page['subscription']['matcher_caught_up'] is True
        assert page['consumption']=='passive_read_no_ack'
        assert page['items'][0]['message']==mid and page['items'][0]['revision']==1
        assert page['items'][0]['origin']=='live' and page['items'][0]['historical'] is False
        assert 'body' not in page['items'][0]
        repeat=rpc(client,key,'get_subscription_events',args).json()['result']['structuredContent']
        assert repeat==page
    with store.pool.connection() as c:
        assert c.execute('SELECT count(*) AS n FROM consumers').fetchone()['n']==0
        assert c.execute('SELECT count(*) AS n FROM deliveries').fetchone()['n']==0


def test_subscription_http_requires_optin_and_originating_key(seeded):
    store,_,key,legacy,chat=source(seeded)
    with TestClient(create_app(store,['testserver'])) as client:
        denied=client.post('/v1/create_subscription',headers={'Authorization':'Bearer '+legacy},
            json={'chat_ids':['telegram-demo-chat']})
        assert denied.status_code==403 and denied.json()['error']=='forbidden'
        created=client.post('/v1/create_subscription',headers={'Authorization':'Bearer '+key},
            json={'chat_ids':[chat]}).json()
        sid=created['subscription_id']
        other=store.issue_key('subscription-api-reader',['subscription-api'],
            permissions=['read','subscription-write'],key_permissions=['read','subscription-write'])
        for operation in ('get_subscription','get_subscription_events'):
            denied=client.post('/v1/'+operation,headers={'Authorization':'Bearer '+other},
                json={'subscription_id':sid})
            assert denied.status_code==404 and denied.json()['error']=='not_found'
        listed=rpc(client,other,'list_subscriptions',{}).json()['result']['structuredContent']
        assert listed['items']==[]
        action=dict(subscription_id=sid,version=created['version'],action='pause')
        paused=client.post('/v1/update_subscription',headers={'Authorization':'Bearer '+key},json=action)
        assert paused.status_code==200 and paused.json()['state']=='paused'
        conflict=client.post('/v1/update_subscription',headers={'Authorization':'Bearer '+key},json=action)
        assert conflict.status_code==409 and conflict.json()['error']=='subscription_conflict'
