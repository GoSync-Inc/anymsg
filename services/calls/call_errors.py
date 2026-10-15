"""Finite error vocabulary: provider messages, bodies and headers stay private."""
MESSAGES = {
    'authentication':'API отклонил ключ. Проверь ключ выбранной модели.',
    'authorization':'API отказал в доступе. Проверь разрешения ключа, доступ к модели и Billing.',
    'quota':'API сообщил об исчерпанном балансе или квоте. Проверь Billing выбранного провайдера.',
    'rate_limit':'Достигнут лимит запросов или одновременных разговоров. Повтори позже.',
    'invalid_request':'API не принял параметры разговора. Сохранён код ошибки; проверь модель и голос.',
    'connectivity':'Не удалось соединиться с моделью. Проверь сеть и доступность API.',
    'server':'Ошибка на стороне провайдера модели. Повтори позже.',
    'application':'Ошибка обработки разговора в голосовом сервисе.',
    'unknown':'Разговор остановлен из-за ошибки модели или соединения. Причина не определена.',
}


def record_model_error(state,frame=None,exception=None):
    exception=exception or getattr(frame,'exception',None)
    response=getattr(exception,'response',None)
    status=next((value for value in (
        getattr(response,'status_code',None),getattr(response,'status',None),
        getattr(exception,'status_code',None),getattr(exception,'status',None))
        if type(value) is int and 100<=value<=599),None)
    category=getattr(getattr(frame,'category',None),'value',None)
    code=category if category in MESSAGES else 'unknown'
    if code=='unknown' and status is not None:
        code={400:'invalid_request',401:'authentication',402:'quota',403:'authorization',
              404:'invalid_request',422:'invalid_request',429:'rate_limit'}.get(status,
              'server' if status>=500 else 'unknown')
    if code=='unknown' and isinstance(exception,(ConnectionError,TimeoutError)):
        code='connectivity'
    state['end_reason']='model_error'
    payload={'kind':'error','code':code,'message':MESSAGES[code]}
    event={'kind':'model_error','code':code}
    if status is not None:
        payload['http_status']=status;event['http_status']=status
    if state.get('service') and state.get('call_id'):
        state['service'].runtime_event(state['call_id'],event)
    return payload
