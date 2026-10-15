import asyncio
import base64
import json
from types import SimpleNamespace

import httpx
import pytest
from pipecat.frames.frames import (
    EndFrame, InterruptionFrame, LLMFullResponseStartFrame, LLMFullResponseEndFrame,
    LLMTextFrame, TTSAudioRawFrame, TTSStartedFrame, TTSStoppedFrame, BotStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.workers.runner import WorkerRunner
from pipecat.processors.frame_processor import FrameProcessor,FrameDirection

from core import BriefInput,VoiceService
from custom_tts import ReplicatedVoiceTTS,STYLES,audio_events,speech_text
from voice import create_model


async def test_custom_voice_never_forwards_native_audio_or_duplicate_text(tmp_path,monkeypatch):
    store=VoiceService(tmp_path)
    brief=store.prepare(BriefInput(goal='Поговорить',counterparty='Test',success_fields=['answer'],
        custom_voice_id='voice_fixture',voice_style='friendly'),[])
    llm=create_model(brief,'fixture')
    frames=[]
    async def push(self,frame,direction=FrameDirection.DOWNSTREAM):frames.append(frame)
    monkeypatch.setattr(type(llm).__mro__[1],'push_frame',push)
    await llm.push_frame(TTSAudioRawFrame(b'\0'*100,24000,1))
    await llm.push_frame(TTSStartedFrame());await llm.push_frame(TTSStoppedFrame())
    await llm._push_output_transcription_text_frames('Привет!')
    assert len(frames)==1 and isinstance(frames[0],LLMTextFrame)
    assert frames[0].text=='Привет!' and frames[0].append_to_context
    assert 'Disclose in your first spoken turn' in brief['prompt']
    store.close()


async def test_sse_pcm_format_done_and_invalid_format():
    audio=b'\x01\x00'*960
    event={'event_type':'step.delta','delta':{'type':'audio','data':base64.b64encode(audio).decode(),
                                          'sample_rate':24000,'channels':1}}
    response=httpx.Response(200,content='data: '+json.dumps(event)+'\n\ndata: [DONE]\n\n')
    assert [chunk async for chunk in audio_events(response)]==[audio]
    event['delta']['sample_rate']=48000
    response=httpx.Response(200,content='data: '+json.dumps(event)+'\n\n')
    with pytest.raises(ValueError,match='unsupported_speech_format'):
        [chunk async for chunk in audio_events(response)]


async def test_friendly_style_sent_to_synthesis_and_pcm_streamed():
    received=[];metrics=[]
    audio=b'\x01\x00'*960
    def handler(request):
        received.append(json.loads(request.content))
        return httpx.Response(200,content='data: '+json.dumps({'event_type':'step.delta',
            'delta':{'type':'audio','data':base64.b64encode(audio).decode()}})+'\n\ndata: [DONE]\n\n')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tts=ReplicatedVoiceTTS(api_key='fixture',voice_id='voice_fixture',style='friendly',
            client=client,metric_callback=metrics.append)
        frames=[f async for f in tts.run_tts('Привет!','ctx')]
    assert frames[0].audio==audio and frames[0].sample_rate==24000
    assert received[0]['input'][0]['content'][0]['annotations'][0]['style']==STYLES['friendly']
    assert received[0]['generation_config']['speech_config'][0]['voice']=='voice_fixture'
    assert received[0]['stream'] is True and metrics[0]['status']=='completed'
    assert speech_text('[laughs] да <short pause> <bad tag>')=='<chuckle> да <short pause> '


async def test_cancellation_closes_stream_without_native_voice_fallback():
    entered=asyncio.Event();closed=asyncio.Event();metrics=[]
    class Stream:
        async def __aenter__(self):entered.set();return self
        async def __aexit__(self,*args):closed.set()
        def raise_for_status(self):pass
        async def aiter_lines(self):
            await asyncio.Event().wait()
            yield ''
    client=SimpleNamespace(stream=lambda *args,**kwargs:Stream())
    tts=ReplicatedVoiceTTS(api_key='fixture',voice_id='voice_fixture',client=client,metric_callback=metrics.append)
    async def consume():return [f async for f in tts.run_tts('Не произнесено.','ctx')]
    task=asyncio.create_task(consume());await entered.wait();task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert closed.is_set() and metrics[0]['status']=='interrupted' and metrics[0]['audio_ms']==0


async def test_pipeline_interruption_cancels_old_speech_then_drains_goodbye():
    started=asyncio.Event();cancelled=asyncio.Event();seen=[]
    class TTS(ReplicatedVoiceTTS):
        async def run_tts(self,text,context_id):
            if 'old' in text:
                started.set()
                try:await asyncio.Event().wait()
                finally:cancelled.set()
            else:
                yield TTSAudioRawFrame(b'\x01\x00'*960,24000,1,context_id=context_id)
    class Sink(FrameProcessor):
        async def process_frame(self,frame,direction):
            await super().process_frame(frame,direction)
            seen.append(frame);await self.push_frame(frame,direction)
            if isinstance(frame,TTSStoppedFrame):
                await self.push_frame(BotStoppedSpeakingFrame(),FrameDirection.UPSTREAM)
    tts=TTS(api_key='fixture',voice_id='voice_fixture')
    worker=PipelineWorker(Pipeline([tts,Sink()]),idle_timeout_secs=None)
    runner=WorkerRunner(handle_sigint=False);await runner.add_workers(worker)
    @worker.event_handler('on_pipeline_started')
    async def begin(worker,frame):
        await worker.queue_frames([LLMFullResponseStartFrame(),LLMTextFrame('old sentence.'),LLMFullResponseEndFrame()])
    task=asyncio.create_task(runner.run())
    try:
        await asyncio.wait_for(started.wait(),3)
        await worker.queue_frames([InterruptionFrame()])
        await asyncio.wait_for(cancelled.wait(),3)
        await worker.queue_frames([LLMFullResponseStartFrame(),LLMTextFrame('goodbye.'),LLMFullResponseEndFrame(),EndFrame()])
        await asyncio.wait_for(task,5)
    finally:
        if not task.done():await runner.cancel();await asyncio.wait_for(task,3)
    assert any(isinstance(f,TTSAudioRawFrame) for f in seen)
    assert seen.index(next(f for f in seen if isinstance(f,TTSAudioRawFrame))) < seen.index(next(f for f in seen if isinstance(f,EndFrame)))
    assert not any(type(f).__name__=='TTSTextFrame' and getattr(f,'text','').startswith('old') for f in seen)
