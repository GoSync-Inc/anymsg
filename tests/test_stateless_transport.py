"""A JSON-only stateless server must decline the optional notification stream."""
import asyncio

import pytest
from starlette.testclient import TestClient

from messaging_service.app import create_app


@pytest.mark.parametrize('path',['/mcp','/mcp/'])
def test_get_declines_unused_stream_without_waiting_for_an_event(seeded,path):
    s,a,b=seeded
    app=create_app(s,['testserver'])
    messages=[]
    async def check():
        started=asyncio.Event()
        first=True
        async def receive():
            nonlocal first
            if first:
                first=False
                return {'type':'http.request','body':b'','more_body':False}
            await started.wait()
            return {'type':'http.disconnect'}
        async def send(message):
            messages.append(message)
            if message['type']=='http.response.start':started.set()
        scope={'type':'http','asgi':{'version':'3.0','spec_version':'2.4'},'http_version':'1.1',
            'method':'GET','scheme':'http','path':path,'raw_path':path.encode(),'root_path':'',
            'query_string':b'','client':('127.0.0.1',1234),'server':('testserver',80),
            'headers':[(b'host',b'testserver'),(b'authorization',('Bearer '+b).encode()),
                (b'accept',b'text/event-stream'),(b'mcp-protocol-version',b'2025-06-18')]}
        async with app.app.router.lifespan_context(app.app):
            await asyncio.wait_for(app(scope,receive,send),2)
    asyncio.run(check())
    start=next(m for m in messages if m['type']=='http.response.start')
    assert start['status']==405
    assert dict(start['headers'])[b'allow']==b'POST'
    assert b'text/event-stream' not in dict(start['headers']).get(b'content-type',b'')


def test_stream_rejection_preserves_auth_host_and_origin_checks(seeded):
    s,a,b=seeded
    with TestClient(create_app(s,['testserver'])) as client:
        assert client.get('/mcp').status_code==401
        auth={'Authorization':'Bearer '+b}
        assert client.get('/mcp',headers={**auth,'Host':'bad.invalid'}).status_code==403
        assert client.get('/mcp',headers={**auth,'Origin':'https://bad.invalid'}).status_code==403
