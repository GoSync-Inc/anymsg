from types import SimpleNamespace as NS
import logging
import asyncio

import pytest
from starlette.testclient import TestClient

from messaging_service.adapters import telegram_message,whatsapp_message
from messaging_service.app import create_app
from messaging_service.archive import Archive
from messaging_service.store import APIError
from messaging_service.security_logging import redact_library_logs
from test_archive import event
from test_collectors import worker,message


def directory(seeded):
    s,a,b=seeded
    ar=Archive(s);ar.register('people-test','telegram','Synthetic people',discover=True)
    key=s.issue_key('people-reader',['people-test'])
    mid=ar.ingest(event(account='people-test',sender_profile={'display_name':'Анна Иванова','username':'anna_test'}))['message']
    return s,ar,key,mid


def test_names_usernames_and_sender_ids_do_not_merge_accounts_or_chats(seeded):
    s,ar,key,mid=directory(seeded)
    first=s.search_people(key,'АННА')['items'][0]
    assert first['sender_id']=='42' and first['source_message']==mid
    assert s.search_people(key,'@Anna_Test')['items']==[first]
    assert s.search_people(key,'42')['items']==[first]
    assert s.search(key,account=first['account'],sender=first['sender_id'])['items'][0]['id']==mid
    assert s.search_people(key,accounts=[])['items']==[]
    ar.ingest(event(account='people-test',chat='private',sender_profile={'display_name':'HiddenAlias'}))
    assert len(s.search_people(key)['items'])==2
    s.set_access('people-reader',chats=[first['chat']])
    assert s.search_people(key,'HiddenAlias')['items']==[]
    assert s.search_people(key)['items']==[first]
    assert s.search_people(seeded[1])['items']==[]


def test_metadata_refresh_does_not_edit_message_or_reveal_deleted_source(seeded):
    s,ar,key,mid=directory(seeded)
    assert ar.ingest(event(account='people-test',sender_profile={'display_name':'New Name','username':'new'}))['outcome']=='unchanged'
    assert s.get_context(key,mid)['message']['revision']==1
    assert s.search_people(key,'Анна')['items']==[]
    assert s.search_people(key,'new')['items'][0]['source_message']==mid
    ar.ingest(event(account='people-test',provider_id='1',sent_at='2026-09-01T00:00:00Z',
                    updated_at='2026-09-01T00:00:00Z',sender_profile={'display_name':'Old Name'}))
    assert s.search_people(key,'Old')['items']==[]
    ar.ingest(event(account='people-test',deleted=True,updated_at='2026-10-02T00:00:00Z'))
    assert s.search_people(key)['items']==[]


def test_people_pagination_and_literal_patterns(seeded):
    s,ar,key,_=directory(seeded)
    ar.ingest(event(account='people-test',sender='43',provider_id='19',sender_profile={'display_name':'name%_literal'}))
    assert len(s.search_people(key,'%_')['items'])==1
    first=s.search_people(key,limit=1)
    second=s.search_people(key,after=first['next_cursor'],limit=1)
    assert len(second['items'])==1 and first['items'][0]['id']!=second['items'][0]['id']
    assert second['next_cursor'] is None
    with pytest.raises(APIError,match='not_found'):s.search_people(seeded[1],after=first['next_cursor'])


def test_people_rest_mcp_permission_and_audit(seeded):
    s,_,key,_=directory(seeded)
    with TestClient(create_app(s,['testserver'])) as client:
        headers={'Authorization':'Bearer '+key,'Accept':'application/json, text/event-stream'}
        rest=client.post('/v1/search_people',json={'query':'@anna'},headers=headers)
        rpc=client.post('/mcp',headers=headers,json={'jsonrpc':'2.0','id':1,'method':'tools/call',
            'params':{'name':'search_people','arguments':{'query':'@anna'}}}).json()['result']
        assert rest.status_code==200 and rpc['structuredContent']==rest.json()
        s.set_access('people-reader',permissions=[])
        assert client.post('/v1/search_people',json={},headers=headers).status_code==403
    assert s.audit_tail('people-reader',1)[0]['outcome']=='denied'


@pytest.mark.parametrize('kwargs',[{'query':None},{'query':'x\x00'},{'accounts':'oops'},
    {'chats':[True]},{'after':True},{'limit':True}])
def test_people_invalid_parameters(seeded,kwargs):
    with pytest.raises(APIError):seeded[0].search_people(seeded[1],**kwargs)


def test_telegram_cached_sender_has_no_network_lookup():
    m=message(1);m.sender=NS(first_name='Anna',last_name='Example',username='anna_example')
    value=telegram_message('fixture',m)
    assert value['sender_profile']=={'display_name':'Anna Example','username':'anna_example'}


def test_telegram_child_logs_cannot_include_login_data_or_exception(caplog):
    redact_library_logs()
    for prefix in ['personal_os.telegram.transport','telethon']:
        log=logging.getLogger(prefix+'.network.mtprotosender')
        with caplog.at_level(logging.ERROR):
            try:raise ValueError('SYNTHETIC_OTP_SECRET')
            except ValueError:log.error('request=%r',{'phone':'SYNTHETIC_PHONE_SECRET'},exc_info=True,stack_info=True)
    assert 'SYNTHETIC_' not in caplog.text
    assert 'exception_type=ValueError' in caplog.text


def test_private_telegram_peer_does_not_starve_other_chats(worker):
    from telethon.errors import ChannelPrivateError
    w,s,key=worker; original=w.sync_chat; visited=[]
    async def dialogs(limit):return [('-100','channel','Private'),('100','user','Visible')]
    async def sync(chat,kind,title):
        visited.append(chat)
        if chat=='-100':raise ChannelPrivateError(None)
        await original(chat,kind,title)
    w.provider.dialogs=dialogs;w.sync_chat=sync
    asyncio.run(w.sync_once())
    assert visited==['-100','100'] and s.search(key)['items']
    assert s.capabilities(key)['sources'][0]['detail_code']=='configured_chat_unavailable'


def test_backup_discovers_extra_collectors_by_explicit_label(monkeypatch):
    from messaging_service.backup_runner import Docker
    docker=Docker()
    monkeypatch.setattr(docker,'call',lambda *a:'a b c d e')
    def inspect(cid):
        service={'a':'telegram','b':'telegram2','c':'whatsapp3','d':'api','e':'telegram'}[cid]
        labels={'com.docker.compose.service':service}
        if cid in ('b','c'):labels['org.messaging-archive.collector']='true'
        if cid=='e':labels['com.docker.compose.oneoff']='True'
        return {'Id':cid,'Config':{'Labels':labels}}
    monkeypatch.setattr(docker,'inspect',inspect)
    assert [c['service'] for c in docker.running_collectors('fixture')]==['telegram','telegram2','whatsapp3']
