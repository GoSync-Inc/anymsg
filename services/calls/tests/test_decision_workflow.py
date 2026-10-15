"""Agreement rounds are durable service state, rather than a verbal model promise."""
import pytest

from core import BriefInput, ServiceError, VoiceService


def delivery(tmp_path):
    store=VoiceService(tmp_path)
    brief=store.prepare(BriefInput(goal='Уточнить доставку и согласовать условия',counterparty='Поставщик',
        success_fields=['delivery_window','additional_cost'],
        approval_fields=['additional_cost']),[])
    call=store.start(brief['id']);store.connected(call['id'])
    return store,call['id']


def answer(store,identity,field,value,tool_id):
    store.transcript(identity,'user',value)
    return store.tool(identity,'record_answer',{'field':field,'value':value,'evidence':value},tool_id)


def test_marked_conditions_create_owner_request_without_model_request_tool(tmp_path):
    store,identity=delivery(tmp_path)
    answer(store,identity,'delivery_window','12–13','time')
    result=answer(store,identity,'additional_cost','50000 IDR','fee')
    request=result['owner_decision']
    call=store.get_call(identity)
    assert request['status']=='pending' and request['approved'] is False
    assert len(call['decisions'])==1
    assert call['decisions'][0]['conditions']=={'delivery_window':'12–13','additional_cost':'50000 IDR'}
    assert call['decisions'][0]['trigger']=='required_fields'
    assert call['outcome']=='needs_owner'
    assert any(e['kind']=='decision_requested' for e in call['events'])
    # Additional model requests for the same pending round do not duplicate it.
    manual=store.tool(identity,'request_owner_decision',{'question':'Разрешить доплату?',
        'options':['Разрешить','Отклонить']},'model-request')
    assert manual['decision_id']==request['decision_id']
    answer(store,identity,'additional_cost','50000 IDR','same-fee-new-tool-id')
    assert len(store.get_call(identity)['decisions'])==1
    store.close()


def test_changed_terms_invalidate_old_answer_and_create_new_round(tmp_path):
    store,identity=delivery(tmp_path)
    answer(store,identity,'delivery_window','12–13','time')
    first=answer(store,identity,'additional_cost','50000 IDR','fee')['owner_decision']
    store.decide(identity,first['decision_id'],'Разрешить')
    changed=answer(store,identity,'delivery_window','14–15','correct-time')['owner_decision']
    call=store.get_call(identity)
    assert changed['decision_id']!=first['decision_id']
    assert [d['status'] for d in call['decisions']]==['superseded','pending']
    assert call['decisions'][0]['answer']=='Разрешить'
    assert call['decisions'][1]['conditions']['delivery_window']=='14–15'
    assert call['decisions'][1]['round']==2
    with pytest.raises(ServiceError,match='decision_superseded'):
        store.decide(identity,first['decision_id'],'Отклонить')
    assert store.tool(identity,'finish_task',{'summary':'Новые условия получены'},'finish')['outcome']=='needs_owner'
    blocked=store.tool(identity,'end_conversation',{'reason':'completed','summary':'Готово'},'close')
    assert blocked['error']=='owner_decision_pending' and 'close_requested' not in store.get_call(identity)
    store.decide(identity,changed['decision_id'],'Отклонить')
    assert store.tool(identity,'finish_task',{'summary':'Владелец отклонил новые условия'},'finish-again')['outcome']=='collected_needs_review'
    assert store.tool(identity,'end_conversation',{'reason':'completed','summary':'Новые условия отклонены'},'close-again')['end_requested']
    store.close()


def test_reconsideration_creates_fresh_round_but_pending_retry_is_idempotent(tmp_path):
    store,identity=delivery(tmp_path)
    first=answer(store,identity,'additional_cost','50000 IDR','fee')['owner_decision']
    store.decide(identity,first['decision_id'],'Отклонить')
    args={'question':'Пересмотреть решение по доплате 50000 IDR?','options':['Разрешить','Отклонить']}
    second=store.tool(identity,'request_owner_decision',args,'reconsider')
    assert second['decision_id']!=first['decision_id']
    assert store.get_call(identity)['decisions'][-1]['round']==2
    assert store.tool(identity,'request_owner_decision',args,'retry-new-tool-id')['decision_id']==second['decision_id']
    assert len(store.get_call(identity)['decisions'])==2
    assert store.get_call(identity)['outcome']=='needs_owner'
    store.close()


def test_pending_old_terms_cannot_be_answered_after_correction(tmp_path):
    store,identity=delivery(tmp_path)
    first=answer(store,identity,'additional_cost','50000 IDR','fee')['owner_decision']
    new=answer(store,identity,'additional_cost','25000 IDR','fee-correction')['owner_decision']
    with pytest.raises(ServiceError,match='decision_superseded'):
        store.decide(identity,first['decision_id'],'Разрешить')
    assert store.get_call(identity)['decisions'][-1]['id']==new['decision_id']
    assert not any(e['kind']=='owner_decision' for e in store.get_call(identity)['events'])
    # An explicit refusal can end the call even though an owner request is pending.
    store.transcript(identity,'user','Не хочу продолжать разговор.')
    assert store.tool(identity,'end_conversation',{'reason':'refused','summary':'Собеседник отказался',
        'evidence':'Не хочу продолжать разговор.'},'refusal')['end_requested']
    store.close()


def test_approval_fields_must_belong_to_requested_results(tmp_path):
    store=VoiceService(tmp_path)
    with pytest.raises(ServiceError,match='invalid_approval_fields'):
        store.prepare(BriefInput(goal='Уточнить доставку',counterparty='Поставщик',success_fields=['time'],
            approval_fields=['fee']),[])
    store.close()


def test_exhausted_round_limit_preserves_correction_as_unresolved(tmp_path):
    store,identity=delivery(tmp_path)
    answer(store,identity,'delivery_window','12–13','time')
    result=answer(store,identity,'additional_cost','50000 IDR','fee')
    for n in range(10):
        store.decide(identity,result['owner_decision']['decision_id'],'Отклонить')
        if n<9:
            request=store.tool(identity,'request_owner_decision',{'question':'Пересмотреть решение?',
                'options':['Разрешить','Отклонить']},f'round-{n}')
            result={'owner_decision':request}
    store.tool(identity,'finish_task',{'summary':'Отклонено'},'old-summary')
    changed=answer(store,identity,'additional_cost','25000 IDR','fee-correction')
    assert changed['error']=='decision_limit'
    call=store.get_call(identity)
    assert call['answers']['additional_cost']['value']=='25000 IDR'
    assert call['outcome']=='needs_owner'
    assert all(d['status']=='superseded' for d in call['decisions'])
    assert not store.tool(identity,'end_conversation',{'reason':'completed','summary':'Готово'},'close').get('end_requested')
    store.close()
