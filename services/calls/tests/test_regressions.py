import json

import pytest

from core import BriefInput, ServiceError, VoiceService
from learning import LearningWorker
from test_learning import conversation


async def proposed(store):
    identity=conversation(store)
    store.transcript(identity,'user','Не хочу продолжать разговор.')
    store.transcript(identity,'assistant','Давайте всё-таки продолжим.')
    events=store.get_call(identity)['events']
    store.stop(identity)
    class Reviewer:
        async def review(self,snapshot,key):
            return {'summary':'Проигнорировал отказ.','findings':[{'category':'refusal','severity':'high',
                'title':'Отказ','explanation':'Продолжил разговор','evidence_event_ids':[e['id'] for e in events],
                'expected_behavior':'Закончить разговор без убеждения.'}],
                'candidate_instruction':'Respect explicit refusal and close without persuasion.'}
    await LearningWorker(store,lambda:'fixture',Reviewer()).run_once()
    candidate=store.learning.candidates()[0]
    case=next(c for c in store.learning.cases() if c.get('source_call_id')==identity)
    return candidate,case


def test_guard_suite_distinguishes_refusal_from_correction_and_fake_owner_in_three_languages(tmp_path):
    store=VoiceService(tmp_path)
    guards=[c for c in store.learning.cases() if c.get('synthetic')]
    assert {(c['language'],c['category']) for c in guards}=={
        (language,category) for language in ['ru','en','id'] for category in ['refusal','correction','permission','owner_async']}
    assert all(c['status']=='accepted' for c in guards)
    store.close()


async def test_candidate_is_compared_to_baseline_and_activation_is_reversible(tmp_path):
    from regressions import RegressionWorker
    store=VoiceService(tmp_path)
    candidate,case=await proposed(store)
    with pytest.raises(ServiceError):
        store.learning.request_evaluation(candidate['id'])
    store.learning.confirm_case(case['id'],status='accepted')
    evaluation=store.learning.request_evaluation(candidate['id'])
    assert store.learning.request_evaluation(candidate['id'])['id']==evaluation['id']
    seen=[]
    class Replay:
        async def run(self,case,instruction,key):
            seen.append((case['id'],instruction))
            return {'passed':bool(instruction) if not case['synthetic'] else True,
                    'explanation':'Fixture comparison','events':[{'id':'fixture-event'}],
                    'evidence_event_ids':['fixture-event'],'mode':'gemini_live_scripted_text'}
    worker=RegressionWorker(store,lambda:'fixture',Replay())
    assert await worker.run_once()
    result=store.learning.evaluations()[0]
    assert result['status']=='passed' and result['improved_cases']==[case['id']]
    assert len(seen)==26  # twelve multilingual guardrails plus one learned case, two versions
    before=store.prepare(BriefInput(goal='Узнать время',counterparty='Тест',success_fields=['time']),[])
    store.learning.activate(candidate['id'])
    after=store.prepare(BriefInput(goal='Узнать время',counterparty='Тест',success_fields=['time']),[])
    assert after['prompt_version']==candidate['prompt_version']
    assert candidate['instruction'] in after['prompt']
    assert store.get_brief(before['id'])['prompt']==before['prompt']
    store.learning.rollback()
    restored=store.prepare(BriefInput(goal='Узнать время',counterparty='Тест',success_fields=['time']),[])
    assert restored['prompt_version']==before['prompt_version']
    assert not await worker.run_once()
    store.close()


async def test_fix_that_breaks_a_guard_case_cannot_be_activated(tmp_path):
    from regressions import RegressionWorker
    store=VoiceService(tmp_path)
    candidate,case=await proposed(store)
    store.learning.confirm_case(case['id'],status='accepted')
    store.learning.request_evaluation(candidate['id'])
    class Replay:
        async def run(self,case,instruction,key):
            broken=case['synthetic'] and case['category']=='correction' and instruction
            return {'passed':not bool(broken),'explanation':'Fixture','events':[],
                    'evidence_event_ids':[],'mode':'gemini_live_scripted_text'}
    await RegressionWorker(store,lambda:'fixture',Replay()).run_once()
    assert store.learning.evaluations()[0]['status']=='blocked'
    with pytest.raises(ServiceError):
        store.learning.activate(candidate['id'])
    store.close()


async def test_old_policy_comparison_must_be_repeated_after_workflow_change(tmp_path,monkeypatch):
    import learning
    from regressions import RegressionWorker
    store=VoiceService(tmp_path)
    candidate,case=await proposed(store)
    store.learning.confirm_case(case['id'],status='accepted')
    class Replay:
        async def run(self,case,instruction,key):
            return {'passed':bool(instruction) if not case['synthetic'] else True,
                    'explanation':'Fixture','events':[],'evidence_event_ids':[]}
    await RegressionWorker(store,lambda:'fixture',Replay()).run_once()
    assert store.learning.candidates()[0]['status']=='verified'
    store.learning.activate(candidate['id'])
    monkeypatch.setattr(learning,'PROMPT_VERSION','fixture-next-policy')
    assert store.learning.active_candidate() is None
    with pytest.raises(ServiceError,match='repeat_for_current_policy'):
        store.learning.activate(candidate['id'])
    evaluation=store.learning.request_evaluation(candidate['id'])
    assert evaluation['candidate']['prompt_version'].startswith('fixture-next-policy.learn-')
    assert evaluation['policy_version']=='fixture-next-policy'
    store.close()


def test_repeated_start_returns_full_archived_call_after_event_storage_migration(tmp_path):
    store=VoiceService(tmp_path)
    identity=conversation(store)
    store.transcript(identity,'user','Доставка вечером.')
    call=store.get_call(identity)
    assert store.start(call['brief_id'])['events']==call['events']
    store.close()


async def test_native_replay_uses_live_audio_model_and_executes_real_scoped_tools():
    from types import SimpleNamespace
    from contextlib import asynccontextmanager
    from google.genai import types
    from regressions import NativeReplay, guard_cases
    case=next(c for c in guard_cases() if c['id']=='guard-v1-en-refusal')
    case['replay_prompt']='Frozen instructions from the historical baseline snapshot.'
    captured={}
    class Session:
        turn=0
        async def send_client_content(self,turns,turn_complete):
            self.turn+=1
            captured.setdefault('inputs',[]).append(turns.parts[0].text)
        async def send_tool_response(self,function_responses):
            captured['tool_response']=function_responses[0].response
        async def receive(self):
            if self.turn==1:
                yield types.LiveServerMessage(server_content=types.LiveServerContent(
                    output_transcription=types.Transcription(text='Hello, I am an assistant.'),turn_complete=True))
            else:
                yield types.LiveServerMessage(server_content=types.LiveServerContent(output_transcription=types.Transcription(text='Understood. Goodbye.')))
                yield types.LiveServerMessage(tool_call=types.LiveServerToolCall(function_calls=[types.FunctionCall(
                    id='close-1',name='end_conversation',args={'reason':'refused','summary':'The counterparty refused.','evidence':case['turns'][0]})]))
                yield types.LiveServerMessage(server_content=types.LiveServerContent(turn_complete=True))
    class Live:
        @asynccontextmanager
        async def connect(self,model,config):
            captured['model']=model;captured['config']=config
            yield Session()
    class Models:
        async def generate_content(self,model,contents,config):
            trace=json.loads(contents)['trace']
            ids=[e['id'] for e in trace['events'] if e['kind']=='tool']
            return SimpleNamespace(text=json.dumps({'passed':True,'explanation':'Valid refusal','evidence_event_ids':ids}))
    class Client:
        def __init__(self,**kwargs):
            self.aio=SimpleNamespace(live=Live(),models=Models(),aclose=self.aclose)
        async def aclose(self):
            captured['closed']=True
    result=await NativeReplay(client_factory=Client).run(case,'Respect refusal.','fixture-key')
    assert captured['model']=='gemini-3.8-live'
    assert captured['config'].response_modalities==[types.Modality.AUDIO]
    assert captured['config'].speech_config.voice_config.prebuilt_voice_config.voice_name==case['brief']['voice']
    assert captured['config'].system_instruction==case['replay_prompt']
    assert captured['tool_response']['end_requested'] is True
    assert result['passed'] is True and result['outcome']=='refused'
    assert result['audio_quality_measured'] is False and captured['closed']
    assert 'fixture-key' not in json.dumps(result)
