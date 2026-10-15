import pytest

from core import BriefInput, VoiceService


def social(tmp_path):
    store=VoiceService(tmp_path)
    brief=store.prepare(BriefInput(goal='Поболтать о текущих делах',counterparty='Test',
        conversation_mode='social',success_fields=['feedback']),[])
    call=store.start(brief['id']);store.connected(call['id'])
    store.transcript(call['id'],'user','Всё хорошо.')
    store.tool(call['id'],'record_answer',{'field':'feedback','value':'Всё хорошо',
        'evidence':'Всё хорошо.'},'answer')
    store.tool(call['id'],'finish_task',{'summary':'Ответ получен'},'summary')
    return store,call['id']


def test_collected_social_answers_cannot_end_an_unfinished_story(tmp_path):
    store,identity=social(tmp_path)
    store.transcript(identity,'user','И ещё я хочу рассказать про свой проект.')
    result=store.tool(identity,'end_conversation',{'reason':'completed','summary':'Ответы собраны'},'end')
    assert result['error']=='closing_confirmation_required'
    assert 'close_requested' not in store.get_call(identity)
    assert store.get_call(identity)['status']=='active'
    store.close()


@pytest.mark.parametrize('goodbye',['Ладно, пока!','Давай заканчивать.','Bye, take care!',
                                    'Sampai jumpa!'])
def test_social_can_end_after_latest_explicit_goodbye(tmp_path,goodbye):
    store,identity=social(tmp_path)
    store.transcript(identity,'user',goodbye)
    result=store.tool(identity,'end_conversation',{'reason':'completed','summary':'Поболтали',
        'evidence':goodbye},'end')
    assert result['end_requested']
    assert store.get_call(identity)['close_requested']['evidence_event']
    store.close()


def test_old_goodbye_or_substring_in_a_story_does_not_authorize_closing(tmp_path):
    store,identity=social(tmp_path)
    store.transcript(identity,'user','Пока!')
    store.transcript_chunk(identity,'user','Нет, подожди, я ещё не закончил.')
    result=store.tool(identity,'end_conversation',{'reason':'completed','summary':'Поболтали',
        'evidence':'Пока!'},'old-end')
    assert result['error']=='closing_confirmation_required'
    store.transcript(identity,'user','Пока я работаю над своим проектом.')
    result=store.tool(identity,'end_conversation',{'reason':'completed','summary':'Поболтали',
        'evidence':'Пока'},'substring-end')
    assert result['error']=='closing_confirmation_required'
    store.close()


def test_social_refusal_still_ends_immediately(tmp_path):
    store,identity=social(tmp_path)
    text='Не хочу разговаривать, остановись.'
    store.transcript(identity,'user',text)
    assert store.tool(identity,'end_conversation',{'reason':'refused','summary':'Отказ',
        'evidence':text},'refused')['end_requested']
    store.close()


def test_voice_complaint_is_not_a_refusal_to_talk(tmp_path):
    store,identity=social(tmp_path)
    text='Мне не пойдёт.'
    store.transcript(identity,'user',text)
    result=store.tool(identity,'end_conversation',{'reason':'refused','summary':'Отказ',
        'evidence':text},'complaint')
    assert result['error']=='closing_intent_unconfirmed'
    assert 'close_requested' not in store.get_call(identity)
    store.close()


def test_social_goodbye_can_end_without_all_notes_collected(tmp_path):
    store=VoiceService(tmp_path)
    brief=store.prepare(BriefInput(goal='Свободная беседа',counterparty='Test',
        conversation_mode='social',success_fields=['feedback']),[])
    call=store.start(brief['id']);store.connected(call['id'])
    store.transcript(call['id'],'user','Ладно, пока!')
    result=store.tool(call['id'],'end_conversation',{'reason':'completed','summary':'Поболтали',
        'evidence':'Ладно, пока!'},'end')
    assert result['end_requested']
    assert result['outcome']=='incomplete'
    store.close()


def test_default_is_neutral_standard_male_voice_for_direct_live_audio():
    brief=BriefInput(goal='Проверить звук',counterparty='Test',success_fields=['feedback'])
    assert brief.voice=='Puck'
    assert brief.self_reference=='masculine'
    assert brief.voice_style=='neutral'
    assert brief.custom_voice_id is None


@pytest.mark.parametrize('text,evidence',[
    ('Давай.  Ага.  Давай.  До свидания.','Давай. Ага. Давай. До свидания.'),
    ('Да, да, да. Давай, давай, пока, давай, пока.','пока'),
    ('Всё, давай, давай. Пока.','Всё, давай, давай. Пока.'),
])
def test_real_call_goodbye_with_fillers_and_aggregator_spaces(tmp_path,text,evidence):
    store,identity=social(tmp_path)
    store.transcript(identity,'user',text)
    result=store.tool(identity,'end_conversation',{'reason':'completed','summary':'Поболтали',
        'evidence':evidence},'end')
    assert result.get('end_requested'), result
    store.close()


def test_realtime_goodbye_chunks_with_fillers_can_end(tmp_path):
    store,identity=social(tmp_path)
    store.transcript_chunk(identity,'user','Давай. Ага. ')
    store.transcript_chunk(identity,'user','Давай. До свидания.')
    result=store.tool(identity,'end_conversation',{'reason':'completed','summary':'Поболтали',
        'evidence':'До свидания.'},'end')
    assert result.get('end_requested'), result
    store.close()


def test_ambiguous_asr_closing_requests_clarification_without_goodbye_loop(tmp_path):
    store,identity=social(tmp_path)
    text='Нет, всё, давай. Расход.'
    store.transcript_chunk(identity,'user',text)
    result=store.tool(identity,'end_conversation',{'reason':'completed','summary':'Поболтали',
        'evidence':text},'end')
    assert result['error']=='closing_confirmation_required'
    assert result['await_new_human_speech'] is True
    assert 'Do not repeat' in result['next_action']
    assert store.get_call(identity)['status']=='active'
    store.close()


@pytest.mark.parametrize('text',[
    'Давай, пока я объясню свой план.',
    'До свидания, нет, подожди, я ещё не закончил.',
])
def test_farewell_words_inside_unfinished_speech_do_not_end(tmp_path,text):
    store,identity=social(tmp_path)
    store.transcript_chunk(identity,'user',text)
    result=store.tool(identity,'end_conversation',{'reason':'completed','summary':'Поболтали',
        'evidence':text},'end')
    assert result['error']=='closing_confirmation_required'
    store.close()
