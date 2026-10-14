"""Real private Python/Node IPC with synthetic provider operations only."""
import asyncio
import json
import shutil
import time
from pathlib import Path

import pytest

from messaging_service.collector_state import session_lock
from messaging_service.whatsapp_collector import NodeProvider


@pytest.fixture
def lane_provider(tmp_path):
    script = tmp_path / 'synthetic.mjs'
    ipc=(Path(__file__).resolve().parents[1]/'whatsapp/ipc.mjs').as_uri()
    script.write_text('import {runLane,sendingSocket} from '+json.dumps(ipc)+';\n'+'''
let release;
const blocked=new Promise(resolve=>{release=resolve;});
async function handle(r,emit){
  if(r.method==='media'){
   await emit({id:r.id,chunk:Buffer.from('data').toString('base64')});
   if(r.params.message==='backpressure'){
    for(let i=0;i<64;i++)await emit({id:r.id,chunk:Buffer.alloc(65536).toString('base64')});
   }else await blocked;
   return {verified:true};
  }else if(r.method==='page'){
   return {pending:true};
  }else if(r.method==='send_text'){
   if(r.params.text==='reject')throw new Error('send_expired');
   if(r.params.text==='hang')await new Promise(()=>{});
   release();
   return {provider_message_id:'synthetic-receipt'};
  }else return {ready:true};
}
const socket=sendingSocket();
await Promise.all([
 runLane(process.stdin,process.stdout,{methods:['start','media','page','policy'],handle}),
 runLane(socket,socket,{methods:['send_text'],safe:new Set(['send_expired']),handle}),
]);
''')
    return tmp_path, script


@pytest.mark.parametrize('message', ['blocked', 'backpressure'])
def test_send_receipt_arrives_while_background_media_is_unfinished(lane_provider, message):
    directory, script = lane_provider
    async def run():
        with session_lock(directory / 'owner') as fd:
            provider = NodeProvider({'node_executable':shutil.which('node')},directory/'unused',fd,script=script)
            provider.policy = {}
            stream = None
            try:
                await provider.connect()
                stream = provider.chunks({'provider_chat':'123@c.us','provider_id':message},{})
                assert await anext(stream) == b'data'
                assert provider.lock.locked()  # Background stream intentionally not consumed.
                async def send():
                    async with provider.send_gate:
                        return await provider.send_text({'provider_chat':'123@c.us','body':'Synthetic',
                            'expires_at':time.time()+60})
                receipt = await asyncio.wait_for(send(), 1)
                assert receipt == {'provider_message_id':'synthetic-receipt'}
                assert provider.lock.locked()
                chunks = [chunk async for chunk in stream]
                assert len(chunks) == (64 if message=='backpressure' else 0)
                assert await provider.page('123@c.us',limit=1) == {'pending':True}
            finally:
                if stream is not None: await stream.aclose()
                await provider.disconnect()
            assert provider.process.returncode is not None
    asyncio.run(run())


def test_cancelled_send_closes_both_lanes_without_replaying(lane_provider):
    directory,script=lane_provider
    async def run():
        with session_lock(directory/'owner') as fd:
            provider=NodeProvider({'node_executable':shutil.which('node')},directory/'unused',fd,script=script)
            provider.policy={};stream=None
            try:
                await provider.connect()
                stream=provider.chunks({'provider_chat':'123@c.us','provider_id':'blocked'},{})
                assert await anext(stream)==b'data'
                async with provider.send_gate:
                    with pytest.raises(TimeoutError):
                        await asyncio.wait_for(provider.send_text({'provider_chat':'123@c.us','body':'hang',
                            'expires_at':time.time()+60}),.1)
                assert provider.process.returncode is not None
                assert provider.send_writer is None
                with pytest.raises(RuntimeError):await anext(stream)
            finally:
                if stream:await stream.aclose()
                await provider.disconnect()
    asyncio.run(run())


def test_rejected_send_keeps_background_stream_and_sending_lane_usable(lane_provider):
    from messaging_service.outgoing import SendRejected
    directory,script=lane_provider
    async def run():
        with session_lock(directory/'owner') as fd:
            provider=NodeProvider({'node_executable':shutil.which('node')},directory/'unused',fd,script=script)
            provider.policy={};stream=None
            try:
                await provider.connect()
                stream=provider.chunks({'provider_chat':'123@c.us','provider_id':'blocked'},{})
                assert await anext(stream)==b'data'
                async with provider.send_gate:
                    with pytest.raises(SendRejected,match='send_expired'):
                        await provider.send_text({'provider_chat':'123@c.us','body':'reject','expires_at':time.time()+60})
                    assert provider.process.returncode is None
                    receipt=await provider.send_text({'provider_chat':'123@c.us','body':'Synthetic','expires_at':time.time()+60})
                    assert receipt['provider_message_id']=='synthetic-receipt'
                assert [chunk async for chunk in stream]==[]
            finally:
                if stream:await stream.aclose()
                await provider.disconnect()
    asyncio.run(run())


def test_send_gate_serializes_sends_without_blocking_background_reads(lane_provider):
    directory, script = lane_provider
    async def run():
        with session_lock(directory / 'owner') as fd:
            provider = NodeProvider({'node_executable':shutil.which('node')},directory/'unused',fd,script=script)
            provider.policy = {}
            try:
                await provider.connect()
                async with provider.send_gate:
                    assert await asyncio.wait_for(provider.page('123@c.us',limit=1),1) == {'pending':True}
            finally: await provider.disconnect()
    asyncio.run(run())


def test_concurrent_lanes_keep_request_ids_when_background_write_is_blocked(tmp_path):
    from types import SimpleNamespace
    async def run():
        entered,released=asyncio.Event(),asyncio.Event()
        class Writer:
            def __init__(self,blocked=False):self.blocked,self.payload=blocked,None
            def write(self,payload):self.payload=json.loads(payload)
            async def drain(self):
                if self.blocked:entered.set();await released.wait()
        with session_lock(tmp_path/'owner') as fd:
            provider=NodeProvider({'node_executable':shutil.which('node')},tmp_path/'unused',fd)
            background,sending=Writer(True),Writer()
            provider.process=SimpleNamespace(returncode=None,stdin=background)
            provider.send_writer=sending
            first=asyncio.create_task(provider.request('page',{}));await entered.wait()
            second=await provider.request('send_text',{},sending=True)
            released.set();one=await first
            assert (one,second)==(background.payload['id'],sending.payload['id'])==(1,2)
    asyncio.run(run())
