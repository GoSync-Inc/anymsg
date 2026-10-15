"""Streaming Gemini replicated voices; Pipecat owns buffering and interruptions."""
import asyncio
import base64
import json
import re
import time

import httpx
from pipecat.frames.frames import TTSAudioRawFrame
from pipecat.services.tts_service import TTSService
from pipecat.services.settings import TTSSettings

MODEL='gemini-3.8-flash-lite-tts'
STYLES={
    'neutral':'Natural conversational delivery, clear and calm, with natural pauses.',
    'friendly':('Friendly and warm, casual conversation rather than reading a script. '
                'Relaxed intonation, calm natural pace and short natural pauses. '
                'Use gentle expressive inflections appropriate to the meaning. '
                'Do not add forced laughter, exaggerated enthusiasm or unrelated sound effects.'),
}
TAGS={'short pause','chuckle','laugh','sigh'}


def speech_text(text):
    # Preserve only supported expressive tags, not arbitrary markup from sources.
    text=re.sub(r'\[(laughs?|chuckles?)\]', '<chuckle>', text, flags=re.I)
    text=re.sub(r'\[(short )?pause\]', '<short pause>', text, flags=re.I)
    return re.sub(r'<([^>]+)>',lambda m:m.group(0) if m.group(1).lower() in TAGS else '',text)


async def audio_events(response):
    """Parse SSE records, including multiline data and the terminal [DONE]."""
    data=[]
    async def decode(lines):
        raw='\n'.join(lines).strip()
        if not raw or raw=='[DONE]':return None
        event=json.loads(raw)
        if event.get('event_type') in {'error','interaction.error'} or event.get('error'):
            raise ValueError('speech_provider_error')
        delta=event.get('delta',{})
        if delta.get('type')!='audio':return None
        if delta.get('sample_rate',24000)!=24000 or delta.get('channels',1)!=1:
            raise ValueError('unsupported_speech_format')
        audio=base64.b64decode(delta['data'],validate=True)
        if len(audio)%2:raise ValueError('invalid_pcm')
        return audio
    async for line in response.aiter_lines():
        if line.startswith('data:'):data.append(line[5:].lstrip())
        elif not line and data:
            audio=await decode(data);data=[]
            if audio:yield audio
    if data:
        audio=await decode(data)
        if audio:yield audio


class ReplicatedVoiceTTS(TTSService):
    def __init__(self,*,api_key,voice_id,style='neutral',metric_callback=None,client=None):
        super().__init__(sample_rate=24000,push_start_frame=True,push_stop_frames=True,
                         pause_frame_processing=True,
                         settings=TTSSettings(model=MODEL,voice=voice_id,language=None))
        self._api_key=api_key
        self._style=STYLES[style]
        self._client=client or httpx.AsyncClient(timeout=httpx.Timeout(20,connect=10))
        self._own_client=client is None
        self._metric_callback=metric_callback

    async def cleanup(self):
        await super().cleanup()
        if self._own_client:await self._client.aclose()

    async def run_tts(self,text,context_id):
        started=time.monotonic();first=None;total=0;status='failed'
        body={'model':MODEL,'input':[{'type':'user_input','content':[{
            'type':'text','text':speech_text(text),'annotations':[{
                'type':'speech_metadata','style':self._style}]}]}],
            'response_format':{'type':'audio'},
            'generation_config':{'speech_config':[{'voice':self._settings.voice}]},'stream':True}
        try:
            # A bounded request, without retries. Cancellation closes the HTTP stream.
            async with asyncio.timeout(30):
                async with self._client.stream('POST','https://generativelanguage.googleapis.com/v1beta/interactions',
                        headers={'x-goog-api-key':self._api_key},json=body) as response:
                    response.raise_for_status()
                    async for audio in audio_events(response):
                        if first is None:
                            first=time.monotonic()-started
                            await self.stop_ttfb_metrics()
                        total+=len(audio)
                        yield TTSAudioRawFrame(audio,24000,1,context_id=context_id)
            if not total:raise ValueError('empty_speech')
            status='completed'
        except asyncio.CancelledError:
            status='interrupted';raise
        except Exception:
            # Provider exceptions can contain URLs/keys or private speech. Never relay them.
            await self.push_error('Replicated voice synthesis unavailable.')
        finally:
            if self._metric_callback:
                self._metric_callback({'model':MODEL,'status':status,'first_audio_ms':
                    round(first*1000) if first is not None else None,
                    'request_ms':round((time.monotonic()-started)*1000),'audio_ms':round(total/48)})
