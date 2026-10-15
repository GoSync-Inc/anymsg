"""Real local RTC transport and scoped tool path; only the model is replaced."""
import asyncio
import json
import contextlib

import httpx
import numpy as np
from aiortc import AudioStreamTrack, RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from pipecat.frames.frames import OutputAudioRawFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.workers.runner import WorkerRunner

import voice
from app import create_app
from test_tasks import Archive, BRIEF


async def test_cancelled_peer_cleanup_does_not_block_next_browser_call(monkeypatch,tmp_path):
    """A cancellation during peer close must not leave a terminal call owning RTC."""
    release=asyncio.Event()
    async def fixture_voice(connection,brief,key,state):
        await release.wait()
    class ClosingPeer:
        pc_id='cancelled-peer'
        async def disconnect(self):
            raise asyncio.CancelledError()
    async def handle(request,callback):
        await callback(ClosingPeer())
        return {'sdp':'fixture-answer','type':'answer','pc_id':'cancelled-peer'}
    monkeypatch.setattr(voice,'run_voice',fixture_voice)
    app=create_app(tmp_path,Archive())
    async with app.router.lifespan_context(app):
        monkeypatch.setattr(app.state.rtc,'handle_web_request',handle)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://testserver',
            headers={'Origin':'http://testserver','X-Voice-Service':'1'}) as c:
            await c.post('/api/credentials',json={'gemini':'fixture-not-an-api-key'})
            brief=(await c.post('/api/briefs',json=BRIEF)).json()
            first=(await c.post('/api/calls/start',json={'brief_id':brief['id']})).json()
            payload={'sdp':'fixture-offer','type':'offer','requestData':{'call_id':first['id']}}
            assert (await c.post('/api/offer',json=payload)).status_code==200
            task=app.state.session['task'];release.set()
            with contextlib.suppress(asyncio.CancelledError):await task
            assert (await c.get('/api/calls/'+first['id'])).json()['status']=='finished'
            assert not app.state.session
            release.clear()
            next_brief=(await c.post('/api/briefs',json=BRIEF)).json()
            next_call=(await c.post('/api/calls/start',json={'brief_id':next_brief['id']})).json()
            payload['requestData']['call_id']=next_call['id']
            assert (await c.post('/api/offer',json=payload)).status_code==200
            task=app.state.session['task'];release.set()
            with contextlib.suppress(asyncio.CancelledError):await task


async def test_rtc_audio_tool_result_and_stop_release_connection(monkeypatch,tmp_path):
    finished=asyncio.Event()
    async def fixture_voice(connection,brief,key,state):
        transport=SmallWebRTCTransport(connection,TransportParams(audio_in_enabled=True,audio_out_enabled=True))
        worker=PipelineWorker(Pipeline([transport.input(),transport.output()]))
        runner=WorkerRunner(handle_sigint=False);state['runner']=runner
        await runner.add_workers(worker)
        @worker.rtvi.event_handler('on_client_ready')
        async def ready(rtvi):
            svc,identity=state['service'],state['call_id'];svc.connected(identity)
            svc.transcript(identity,'user','Доставка с 12 до 13.')
            result=svc.tool(identity,'record_answer',{'field':'delivery_window','value':'12–13','evidence':'с 12 до 13'},'fixture-tool-1')
            assert result['evidence_found']
            await rtvi.send_server_message({'kind':'tool','result':result})
            tone=(np.sin(np.arange(24000)*2*np.pi*440/24000)*4000).astype(np.int16).tobytes()
            await worker.queue_frames([OutputAudioRawFrame(tone,24000,1)])
        try:
            await runner.run()
        finally:
            finished.set()
    monkeypatch.setattr(voice,'run_voice',fixture_voice)
    app=create_app(tmp_path,Archive())
    peer=RTCPeerConnection(RTCConfiguration(iceServers=[]));peer.addTrack(AudioStreamTrack())
    channel=peer.createDataChannel('rtvi-ai')
    ready_event,audio_event=asyncio.Event(),asyncio.Event();readers=[]
    @channel.on('open')
    def opened():
        channel.send(json.dumps({'label':'rtvi-ai','type':'client-ready','id':'fixture-ready','data':{'version':'1.0.0'}}))
    @channel.on('message')
    def message(value):
        if json.loads(value).get('type')=='bot-ready':ready_event.set()
    @peer.on('track')
    def track_started(track):
        async def read():
            while True:
                frame=await track.recv()
                if np.max(np.abs(frame.to_ndarray().astype(np.int32)))>100:
                    audio_event.set();return
        readers.append(asyncio.create_task(read()))
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://testserver',
                headers={'Origin':'http://testserver','X-Voice-Service':'1'}) as c:
                await c.post('/api/credentials',json={'gemini':'fixture-not-an-api-key'})
                brief=(await c.post('/api/briefs',json={**BRIEF,'language':'id'})).json()
                job=(await c.post('/api/calls/start',json={'brief_id':brief['id']})).json()
                await peer.setLocalDescription(await peer.createOffer())
                payload={'sdp':peer.localDescription.sdp,'type':'offer','requestData':{'call_id':job['id']}}
                response=await c.post('/api/offer',json=payload)
                assert response.status_code==200,response.text
                answer=response.json();await peer.setRemoteDescription(RTCSessionDescription(sdp=answer['sdp'],type=answer['type']))
                await asyncio.wait_for(ready_event.wait(),10);await asyncio.wait_for(audio_event.wait(),10)
                assert (await c.post('/api/offer',json=payload)).status_code==409
                assert (await c.post(f'/api/calls/{job["id"]}/stop')).status_code==200
                await asyncio.wait_for(finished.wait(),3)
                assert not app.state.rtc._pcs_map
                saved=(await c.get(f'/api/calls/{job["id"]}')).json()
                assert saved['status']=='finished' and saved['answers']['delivery_window']['value']=='12–13'
                assert saved['brief']['language']=='id' and 'fixture-not-an-api-key' not in json.dumps(saved)
    finally:
        for reader in readers:reader.cancel()
        await asyncio.gather(*readers,return_exceptions=True)
        await peer.close()
