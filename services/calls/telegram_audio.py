"""Experimental private Telegram calls; native audio lives outside AnyMsg's collector."""
import asyncio
import contextlib
from pathlib import Path

from core import ACTIVE, ServiceError
from audio_probe import PCMProbe
from telegram_broker import BrokerClient


def dispose_native(py):
    # start() stores C++ callbacks capturing PyTgCalls, forming a cycle across
    # Python/native ownership. Release them while the interpreter/loop are live.
    binding=py._binding
    for name in dir(binding):
        if name.startswith('on_'):getattr(binding,name)(lambda *args:None)
    py._is_running=False
    py.executor.shutdown(wait=False,cancel_futures=True)
    py._binding=None


async def broker_status(socket,token):
    import json
    writer=None
    try:
        reader,writer=await asyncio.open_unix_connection(str(socket),limit=65536)
        writer.write(json.dumps({'token':token,'probe':True}).encode()+b'\n');await writer.drain()
        value=json.loads(await asyncio.wait_for(reader.readline(),3))
        return {'connected':value.get('ready') is True,'account_id':value.get('account_id'),
                'protocol':value.get('protocol'),'cooldown_secs':value.get('cooldown_secs',0),'mode':'experimental_private_calls'}
    except Exception: return {'connected':False,'error':'telegram_broker_unavailable'}
    finally:
        if writer:
            writer.close()
            with contextlib.suppress(Exception): await writer.wait_closed()


def pcm_transport(send,ready_callback):
    from pipecat.transports.base_transport import BaseTransport,TransportParams
    from pipecat.transports.base_input import BaseInputTransport
    from pipecat.transports.base_output import BaseOutputTransport
    params=TransportParams(audio_in_enabled=True,audio_out_enabled=True,audio_in_sample_rate=48000,
                           audio_out_sample_rate=48000,audio_out_channels=1,audio_out_10ms_chunks=1,
                           audio_out_end_silence_secs=0)
    class Input(BaseInputTransport):
        async def start(self,frame):
            await super().start(frame);await self.set_transport_ready(frame)
            ready_callback(self)
    class Output(BaseOutputTransport):
        async def start(self,frame):
            await super().start(frame);await self.set_transport_ready(frame)
        async def write_audio_frame(self,frame):
            await send(frame.audio);return True
    class Transport(BaseTransport):
        def __init__(self):
            super().__init__();self.i=Input(params);self.o=Output(params)
        def input(self): return self.i
        def output(self): return self.o
    return Transport()


class TelegramCalls:
    def __init__(self,config,service,key,supervisor):
        self.config,self.service,self.key,self.supervisor=config,service,key,supervisor
        self.active={}

    def token(self):
        try: return Path(self.config['token_file']).read_text().strip()
        except OSError: raise ServiceError('telegram_broker_unavailable') from None

    async def probe(self):
        try: return await broker_status(self.config['socket'],self.token())
        except (KeyError,ServiceError): return {'connected':False,'error':'telegram_broker_not_configured'}

    async def preflight(self,b):
        result=await self.probe()
        if not result['connected']: raise ServiceError('telegram_broker_unavailable')
        if result.get('cooldown_secs',0)>0:raise ServiceError('telegram_rate_limited')
        return {**result,'configured':True,'destination':b['destination'],'channel':'telegram',
                'one_existing_session':True,'pilot_verified':False}

    async def dial(self,call):
        from pytgcalls import PyTgCalls
        from pytgcalls.pytgcalls_session import PyTgCallsSession
        from pytgcalls.types import MediaStream,ExternalMedia,RecordStream,Device,StreamFrames,Direction,CallConfig,ChatUpdate
        from pytgcalls.types.raw import AudioParameters
        cid=call['id'];user=int(call['brief']['destination'])
        client=BrokerClient(self.config['socket'],self.token(),user)
        await client.connect()
        py=PyTgCalls(client,workers=2)
        state={'service':self.service,'call_id':cid,'brief':call['brief'],'supervisor':self.supervisor,
               'client':client,'py':py,'user':user,'input':None,'dial_task':asyncio.current_task(),
               'audio_probe':PCMProbe()}
        self.active[cid]=state
        @py.on_update()
        async def updates(py,update):
            if getattr(update,'chat_id',None)!=user: return
            if isinstance(update,StreamFrames) and update.direction==Direction.INCOMING and update.device in {Device.MICROPHONE,Device.SPEAKER}:
                from pipecat.frames.frames import InputAudioRawFrame
                target=state.get('input')
                if target:
                    for frame in update.frames:
                        # PyTgCalls wraps NTgCalls' raw `data` as public `frame`.
                        audio=frame.frame
                        if len(audio)<=48000:
                            state['audio_probe'].input(audio)
                            await target.push_audio_frame(InputAudioRawFrame(audio=audio,sample_rate=48000,num_channels=1))
            elif isinstance(update,ChatUpdate) and update.status & ChatUpdate.Status.DISCARDED_CALL:
                state['end_reason']='counterparty_hangup'
                if state.get('runner'): await state['runner'].cancel()
        try:
            # Version is pinned in uv.lock; do not fetch GitHub during call setup.
            PyTgCallsSession.notice_displayed=True
            await py.start()
            await asyncio.wait_for(py.play(user,MediaStream(ExternalMedia.AUDIO,AudioParameters(48000,1)),
                                           config=CallConfig(timeout=30)),45)
            await py.record(user,RecordStream(audio=True,audio_parameters=AudioParameters(48000,1)))
            peer=await py._app.get_input_call(user)
            state['provider_id']=str(peer.id)
            async def send(audio):
                await py.send_frame(user,Device.MICROPHONE,audio)
                state['audio_probe'].output(audio)
            transport=pcm_transport(send,lambda value:state.update(input=value))
            state['task']=asyncio.create_task(self._run(cid,transport,state),name='telegram-audio-'+cid)
            return {'provider_call_id':str(peer.id),'provider_status':'connected'}
        except (Exception,asyncio.CancelledError):
            await self._release(cid);raise

    async def _run(self,cid,transport,state):
        from voice import run_transport
        reason='disconnected'
        try:
            await asyncio.wait_for(run_transport(transport,state['brief'],self.key(),state),state['brief']['max_duration_secs'])
            reason=state.get('end_reason',reason)
        except asyncio.CancelledError:
            reason=state.get('end_reason','owner_stop');raise
        except TimeoutError: reason='duration_limit'
        except Exception: reason='model_error'
        finally:
            await self._release(cid)
            value=self.service.get_call(cid)
            self.service.result(cid,metrics={**value['metrics'],'telegram_audio':state['audio_probe'].snapshot()})
            self.service.stop(cid,reason)

    async def _release(self,cid):
        state=self.active.pop(cid,None)
        if not state:return
        py=state['py']
        with contextlib.suppress(Exception): await asyncio.wait_for(py.leave_call(state['user']),10)
        with contextlib.suppress(Exception): await py._binding.stop(state['user'])
        await state['client'].close()
        dispose_native(py)

    async def hangup(self,sid):
        for cid,state in list(self.active.items()):
            if state.get('provider_id')==sid:
                await self.cancel_attempt(cid)

    async def cancel_attempt(self,cid):
        state=self.active.get(cid)
        if not state:return
        state['end_reason']='owner_stop'
        if state.get('runner'):await state['runner'].cancel()
        task=state.get('task') or state.get('dial_task')
        if task and task is not asyncio.current_task() and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):await task
        await self._release(cid)

    async def close(self):
        for cid,state in list(self.active.items()):
            await self.cancel_attempt(cid)
