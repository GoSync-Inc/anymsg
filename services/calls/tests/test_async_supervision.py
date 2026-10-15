"""Owner attention is asynchronous; a browser continuation is a separate attempt."""
import pytest
from fastapi.testclient import TestClient

from app import create_app
from core import BriefInput, ServiceError, VoiceService
from test_tasks import Archive, HEADERS
from test_decision_workflow import delivery, answer


def defer(store, identity):
    answer(store,identity,'delivery_window','12–13','time')
    decision=answer(store,identity,'additional_cost','50000 IDR','fee')['owner_decision']
    result=store.tool(identity,'end_conversation',{'reason':'needs_owner',
        'summary':'Условия записаны, решение уточним позже'},'defer')
    assert result['end_requested'] and result['outcome']=='needs_owner'
    assert result['callback_available'] is False
    store.stop(identity,'awaiting_owner')
    return decision


def test_delayed_owner_answer_survives_restart_and_followup_is_frozen_and_idempotent(tmp_path):
    store,identity=delivery(tmp_path)
    decision=defer(store,identity)
    original=store.get_call(identity)
    store.close();store=VoiceService(tmp_path)
    assert store.decision_inbox()[0]['decision']['id']==decision['decision_id']
    response=store.decide(identity,decision['decision_id'],'Отклонить')
    assert store.decide(identity,decision['decision_id'],'Отклонить')==response
    with pytest.raises(ServiceError,match='decision_already_answered'):
        store.decide(identity,decision['decision_id'],'Разрешить')
    continuation=store.prepare_followup(identity,decision['decision_id'])
    assert store.prepare_followup(identity,decision['decision_id'])['id']==continuation['id']
    assert continuation['followup']['owner_answer']=='Отклонить'
    assert continuation['followup']['conditions']=={'delivery_window':'12–13','additional_cost':'50000 IDR'}
    assert continuation['channel']=='browser' and continuation['success_fields']==['counterparty_response']
    assert 'Отклонить' in continuation['prompt']
    child=store.start(continuation['id'])
    assert child['id']!=identity and child['brief']['followup']['parent_call_id']==identity
    saved=store.get_call(identity)
    assert saved['status']=='finished' and saved['ended_at']==original['ended_at']
    assert saved['outcome']=='followup_ready'
    assert saved['brief']==original['brief'] and saved['answers']==original['answers']
    assert child['answers']=={}  # Historical answers are context, never new-call observations.
    store.close()


def test_pending_or_superseded_or_refused_decisions_cannot_create_callback(tmp_path):
    store,identity=delivery(tmp_path)
    first=answer(store,identity,'additional_cost','50000 IDR','fee')['owner_decision']
    store.decide(identity,first['decision_id'],'Нет')
    answer(store,identity,'additional_cost','25000 IDR','changed')
    store.stop(identity)
    with pytest.raises(ServiceError,match='decision_superseded'):
        store.prepare_followup(identity,first['decision_id'])
    pending=store.get_call(identity)['decisions'][-1]
    with pytest.raises(ServiceError,match='decision_not_answered'):
        store.prepare_followup(identity,pending['id'])
    store.close()
    store,identity=delivery(tmp_path/'refused')
    pending=answer(store,identity,'additional_cost','50000 IDR','fee')['owner_decision']
    store.transcript(identity,'user','Больше не звоните.')
    store.tool(identity,'end_conversation',{'reason':'refused','summary':'Отказ',
        'evidence':'Больше не звоните.'},'bye')
    store.stop(identity,'counterparty_refused')
    store.decide(identity,pending['decision_id'],'Нет')
    with pytest.raises(ServiceError,match='counterparty_refused'):
        store.prepare_followup(identity,pending['decision_id'])
    assert store.get_call(identity)['outcome']=='refused'
    store.close()


def test_deferred_http_owner_inbox_and_continuation_are_local_only(tmp_path):
    app=create_app(tmp_path,Archive())
    with TestClient(app) as client:
        store=app.state.service
        brief=store.prepare(BriefInput(goal='Уточнить доставку',counterparty='Поставщик',
            success_fields=['delivery_window','additional_cost'],approval_fields=['additional_cost']),[])
        identity=store.start(brief['id'])['id'];store.connected(identity)
        decision=defer(store,identity)
        assert client.get('/api/decisions').json()['items'][0]['call_status']=='finished'
        endpoint=f'/api/calls/{identity}/decisions/{decision["decision_id"]}'
        assert client.post(endpoint,json={'answer':'Нет'}).status_code==403
        assert client.post(endpoint,json={'answer':'Нет'},headers=HEADERS).status_code==200
        assert client.post(endpoint+'/followup',headers=HEADERS).status_code==200
        assert client.get('/api/config').json()['channels']['sip']=='not_connected'


def test_supervisor_private_notes_are_not_in_voice_prompt(tmp_path):
    store=VoiceService(tmp_path)
    brief=store.prepare(BriefInput(goal='Уточнить доставку',counterparty='Поставщик',success_fields=['time'],
        supervisor_notes='Private supervisor note: suggest another time if needed.'),[])
    assert brief['supervisor_notes'] not in brief['prompt']
    assert 'consult_supervisor' in brief['prompt']
    assert 'wait' not in brief['prompt'].split('When needs_owner,')[1].split('\n')[0]
    store.close()


def test_new_terms_in_followup_need_new_owner_request_not_inherited_approval(tmp_path):
    store,identity=delivery(tmp_path)
    first=defer(store,identity)
    store.decide(identity,first['decision_id'],'Отклонить')
    child=store.start(store.prepare_followup(identity,first['decision_id'])['id'])
    store.connected(child['id'])
    store.transcript(child['id'],'user','Тогда доплата 25000 IDR.')
    store.tool(child['id'],'record_answer',{'field':'counterparty_response','value':'Доплата 25000 IDR',
        'evidence':'Тогда доплата 25000 IDR.'},'response')
    second=store.tool(child['id'],'request_owner_decision',{'question':'Согласовать новую доплату 25000 IDR?',
        'options':['Разрешить','Отклонить']},'reconsider')
    assert second['status']=='pending' and second['decision_id']!=first['decision_id']
    assert second['conditions']=={'counterparty_response':'Доплата 25000 IDR'}
    assert store.get_call(identity)['decisions'][0]['answer']=='Отклонить'
    assert store.tool(child['id'],'end_conversation',{'reason':'completed','summary':'Готово'},'bad-close').get('error')
    assert store.tool(child['id'],'end_conversation',{'reason':'needs_owner','summary':'Передано на новое решение'},'defer')['end_requested']
    store.close()


def test_legacy_unbound_owner_answer_is_visible_but_cannot_be_reused(tmp_path):
    store,identity=delivery(tmp_path)
    decision=defer(store,identity)
    store.decide(identity,decision['decision_id'],'Нет')
    legacy=store.get_call(identity)
    legacy['decisions'][0].pop('conditions_sha256')
    store._save_call(legacy)
    assert store.decision_inbox()[0]['followup_supported'] is False
    with pytest.raises(ServiceError,match='legacy_decision_unbound'):
        store.prepare_followup(identity,decision['decision_id'])
    store.close()


def test_answer_received_during_closing_becomes_followup_ready_and_late_tools_do_not_mutate(tmp_path):
    store,identity=delivery(tmp_path)
    first=answer(store,identity,'additional_cost','50000 IDR','fee')['owner_decision']
    store.tool(identity,'end_conversation',{'reason':'needs_owner','summary':'Решение уточним позже'},'defer')
    assert store.tool(identity,'record_answer',{'field':'additional_cost','value':'BAD','evidence':'BAD'},'late')['error']=='conversation_ending'
    store.decide(identity,first['decision_id'],'Нет');store.stop(identity,'awaiting_owner')
    assert store.get_call(identity)['outcome']=='followup_ready'
    assert store.get_call(identity)['answers']['additional_cost']['value']=='50000 IDR'
    store.close()
