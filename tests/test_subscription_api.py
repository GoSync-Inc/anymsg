"""Synthetic subscription transport/SDK contracts, without a database or providers."""
import json

import httpx
import pytest
from starlette.testclient import TestClient

from anymsg import AnyMsgError, Client
from messaging_service.app import create_app
from messaging_service.store import APIError


METHODS=('create_subscription','list_subscriptions','get_subscription',
    'update_subscription','get_subscription_events')


class RecordingStore:
    public_url='http://testserver'

    def __init__(self,error=None):self.calls=[];self.error=error
    def authenticate(self,token):return 'synthetic-principal'
    def audit_boundary(self,code):pass
    def _record(self,method,token,params):
        assert token=='synthetic-token'
        self.calls.append((method,params))
        if self.error:raise APIError(self.error,409)
        return {'synthetic':True,'method':method,'parameters':params}
    def create_subscription(self,token,**params):return self._record(METHODS[0],token,params)
    def list_subscriptions(self,token,**params):return self._record(METHODS[1],token,params)
    def get_subscription(self,token,**params):return self._record(METHODS[2],token,params)
    def update_subscription(self,token,**params):return self._record(METHODS[3],token,params)
    def get_subscription_events(self,token,**params):return self._record(METHODS[4],token,params)


def arguments(method):
    if method==METHODS[0]:return {'chat_ids':['chat_synthetic'], 'sender_ids':['sender_synthetic'],
        'kinds':['message.created','file.transcript.ready'],'direction':'incoming',
        'origins':['live','reconcile'],'label':'synthetic source set',
        'expires_at':'2030-01-01T00:00:00Z'}
    if method==METHODS[1]:return {'limit':4}
    params={'subscription_id':'sub_synthetic'}
    if method==METHODS[3]:params.update(version=2,action='resume',resume_policy='catch_up')
    if method==METHODS[4]:params.update(after=17,limit=4)
    return params


def rpc(client,method,params):
    return client.post('/mcp',headers={'Authorization':'Bearer synthetic-token',
        'Accept':'application/json, text/event-stream'},json={'jsonrpc':'2.0','id':1,
        'method':'tools/call','params':{'name':method,'arguments':params}})


@pytest.mark.parametrize('method',METHODS)
def test_subscription_rest_and_mcp_preserve_exact_payload(method):
    store=RecordingStore();params=arguments(method)
    with TestClient(create_app(store,['testserver'])) as client:
        rest=client.post('/v1/'+method,headers={'Authorization':'Bearer synthetic-token'},json=params)
        mcp=rpc(client,method,params)
    assert rest.status_code==200
    assert not mcp.json().get('error') and not mcp.json()['result'].get('isError')
    assert rest.json()==mcp.json()['result']['structuredContent']
    assert store.calls==[(method,params),(method,params)]


@pytest.mark.parametrize('method',METHODS)
def test_subscription_unknown_arguments_rejected_before_backend(method):
    store=RecordingStore();params={**arguments(method),'task_id':'private-unverified-task'}
    with TestClient(create_app(store,['testserver'])) as client:
        rest=client.post('/v1/'+method,headers={'Authorization':'Bearer synthetic-token'},json=params)
        mcp=rpc(client,method,params)
    assert rest.status_code==400 and rest.json()['fields']==['task_id']
    assert mcp.json()['error']['code']==-32602 and mcp.json()['error']['data']['fields']==['task_id']
    assert 'private-unverified-task' not in rest.text+mcp.text
    assert store.calls==[]


@pytest.mark.parametrize('method,field',[
    (METHODS[0],'chat_ids'),(METHODS[2],'subscription_id'),
    (METHODS[3],'subscription_id'),(METHODS[3],'version'),(METHODS[3],'action'),
    (METHODS[4],'subscription_id'),
])
def test_subscription_required_fields_rejected_before_backend(method,field):
    store=RecordingStore();params=arguments(method);del params[field]
    with TestClient(create_app(store,['testserver'])) as client:
        rest=client.post('/v1/'+method,headers={'Authorization':'Bearer synthetic-token'},json=params)
        mcp=rpc(client,method,params)
    assert rest.status_code==400 and rest.json()['fields']==[field]
    assert mcp.json()['error']['data']['fields']==[field]
    assert store.calls==[]


INVALID=[
    (METHODS[0],{'chat_ids':None}),(METHODS[0],{'chat_ids':[]}),(METHODS[0],{'chat_ids':['x']*65}),
    (METHODS[0],{'chat_ids':[1]}),(METHODS[0],{'chat_ids':['bad\nreference']}),
    (METHODS[0],{'sender_ids':[]}),(METHODS[0],{'sender_ids':True}),
    (METHODS[0],{'kinds':['private-kind']}),(METHODS[0],{'kinds':[]}),
    (METHODS[0],{'origins':['unknown']}),(METHODS[0],{'origins':[]}),
    (METHODS[0],{'direction':False}),(METHODS[0],{'label':'x'*129}),
    (METHODS[0],{'label':'private\ncontrol'}),
    (METHODS[0],{'expires_at':'2030-01-01T00:00:00'}),
    (METHODS[0],{'expires_at':'2030-02-30T00:00:00Z'}),
    (METHODS[0],{'expires_at':True}),
    (METHODS[1],{'limit':0}),(METHODS[1],{'limit':101}),(METHODS[1],{'limit':True}),
    (METHODS[2],{'subscription_id':''}),(METHODS[2],{'subscription_id':1}),
    (METHODS[3],{'version':True}),(METHODS[3],{'version':'2'}),(METHODS[3],{'version':0}),
    (METHODS[3],{'action':'change_sources'}),(METHODS[3],{'resume_policy':'unbounded'}),
    (METHODS[4],{'after':True}),(METHODS[4],{'after':-1}),
    (METHODS[4],{'after':9007199254740992}),(METHODS[4],{'limit':101}),
]


@pytest.mark.parametrize('method,extra',INVALID)
def test_subscription_strict_bounds_before_backend(method,extra):
    store=RecordingStore();params={**arguments(method),**extra}
    with TestClient(create_app(store,['testserver'])) as client:
        rest=client.post('/v1/'+method,headers={'Authorization':'Bearer synthetic-token'},json=params)
        mcp=rpc(client,method,params)
    assert rest.status_code==400 and rest.json()['error']=='invalid_parameters'
    assert mcp.json()['error']['code']==-32602
    assert store.calls==[]


def test_subscription_defaults_do_not_enable_historical_replay_or_delivery():
    store=RecordingStore()
    with TestClient(create_app(store,['testserver'])) as client:
        assert not rpc(client,METHODS[0],{'chat_ids':['chat_synthetic']}).json()['result']['isError']
    assert store.calls==[(METHODS[0],dict(chat_ids=['chat_synthetic'],sender_ids=None,kinds=None,
        direction='incoming',origins=None,label='',expires_at=None))]


def test_subscription_manifest_has_strict_schemas_and_passive_event_read():
    store=RecordingStore()
    with TestClient(create_app(store,['testserver'])) as client:
        listed=client.post('/mcp',headers={'Authorization':'Bearer synthetic-token',
            'Accept':'application/json, text/event-stream'},json={'jsonrpc':'2.0','id':1,
            'method':'tools/list','params':{}}).json()['result']['tools']
    tools={tool['name']:tool for tool in listed}
    assert len(tools)==34
    for method in METHODS:
        tool=tools[method]
        assert tool['inputSchema']['additionalProperties'] is False
        mutation=method in (METHODS[0],METHODS[3])
        assert tool['annotations']['readOnlyHint'] is not mutation
        assert tool['annotations']['openWorldHint'] is False
        assert tool['annotations']['destructiveHint'] is False
    legacy=tools['get_updates']['inputSchema']['properties']['kinds']
    assert 'file.transcript.ready' not in json.dumps(legacy)
    assert 'file.transcript.ready' in json.dumps(tools[METHODS[0]]['inputSchema'])
    assert 'ack_subscription_events' not in tools


def test_subscription_backend_conflict_returns_safe_actionable_errors():
    store=RecordingStore('subscription_conflict')
    with TestClient(create_app(store,['testserver'])) as client:
        rest=client.post('/v1/'+METHODS[3],headers={'Authorization':'Bearer synthetic-token'},
            json=arguments(METHODS[3]))
        mcp=rpc(client,METHODS[3],arguments(METHODS[3]))
    assert rest.status_code==409 and rest.json()['error']=='subscription_conflict'
    assert mcp.json()['result']['isError'] is True
    assert 'subscription_conflict' in mcp.text


@pytest.mark.parametrize('method',METHODS)
def test_python_subscription_helpers_preserve_exact_parameters(method):
    seen=[]
    def transport(request):
        seen.append((request.url.path,json.loads(request.content)))
        return httpx.Response(200,json={'synthetic':True})
    with Client('https://archive.example/base','synthetic-token',transport=httpx.MockTransport(transport)) as client:
        assert getattr(client,method)(**arguments(method))=={'synthetic':True}
    assert seen==[('/base/v1/'+method,arguments(method))]


@pytest.mark.parametrize('method,extra',INVALID)
def test_python_subscription_bounds_prevent_transport(method,extra):
    seen=[]
    with Client('https://archive.example','synthetic-token',
            transport=httpx.MockTransport(lambda request:seen.append(request))) as client:
        with pytest.raises(ValueError,match='invalid_parameters'):
            client.call(method,{**arguments(method),**extra})
    assert seen==[]


@pytest.mark.parametrize('method',METHODS)
def test_python_subscription_unknown_fields_prevent_transport(method):
    seen=[]
    with Client('https://archive.example','synthetic-token',
            transport=httpx.MockTransport(lambda request:seen.append(request))) as client:
        with pytest.raises(ValueError,match='invalid_parameters'):
            client.call(method,{**arguments(method),'task_id':'unverified'})
    assert seen==[]


@pytest.mark.parametrize('code',['subscription_conflict','subscription_limit','subscription_closed',
    'subscription_access_changed','invalid_subscription_state','invalid_subscription'])
def test_python_subscription_errors_are_sanitized_without_retry(code):
    seen=[]
    def transport(request):
        seen.append(request)
        return httpx.Response(409,json={'error':code,'detail':'synthetic-private-source'})
    with Client('https://archive.example','synthetic-token',transport=httpx.MockTransport(transport)) as client:
        with pytest.raises(AnyMsgError) as error:client.create_subscription(chat_ids=['chat_synthetic'])
    assert error.value.code==code and 'synthetic-private-source' not in str(error.value)
    assert len(seen)==1


def test_python_uncertain_subscription_creation_never_retries():
    seen=[]
    def transport(request):
        seen.append(request);raise httpx.ReadTimeout('synthetic-private-diagnostic')
    with Client('https://archive.example','synthetic-token',transport=httpx.MockTransport(transport)) as client:
        with pytest.raises(AnyMsgError,match='request_failed'):
            client.create_subscription(chat_ids=['chat_synthetic'])
    assert len(seen)==1
