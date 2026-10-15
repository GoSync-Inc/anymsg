export function createLearningUI({api,node,error,currentCall,isActive,promptChanged}) {
  const $=id=>document.getElementById(id);
  const labels={waiting_key:'Ждёт ключ Gemini',queued:'В очереди',running:'Проверка выполняется',complete:'Разбор готов',failed:'Ошибка API — можно повторить',
    no_speech:'Текста разговора нет',input_too_large:'Текст сохранён; для анализа нужен разбор по частям',superseded:'Есть более свежий разбор',
    conversation_active:'Разбор появится после завершения',proposed:'Предложено',testing:'Сравниваем версии',verified:'Текстовые проверки пройдены',
    blocked:'Улучшение не подтверждено',passed:'Улучшение подтверждено текстовым тестом',interrupted:'Проверка прервана; можно повторить'};
  let signature='',analysisSignature='',busy=false;
  async function action(fn){try{error();await fn();await refresh(true);}catch(e){error(e.message);}}
  function button(text,fn,disabled=false){const b=node('button',text,'subtle');b.disabled=disabled;b.onclick=()=>action(fn);return b;}
  function quote(e){const d=node('details',undefined,'evidence');d.append(node('summary',e.kind==='tool'?`Инструмент ${e.name}`:`${e.role==='user'?'Собеседник':'Ассистент'} · ${e.kind==='transcript_chunk'?'фрагмент провайдера':'реплика'}`),node('pre',e.text || JSON.stringify({arguments:e.arguments,result:e.result},null,2)));return d;}
  async function analysis(force=false){
    const call=currentCall();if(!call)return;
    const report=await api(`/api/calls/${call.id}/analysis`);if(currentCall()?.id!==call.id)return;
    const sig=`${call.id}/${report.id}/${report.status}`;if(sig===analysisSignature && !force)return;analysisSignature=sig;
    const box=$('analysis');box.replaceChildren(node('p',labels[report.status] || report.status,'analysis-status'));
    if(report.summary)box.append(node('p',report.summary));
    if(report.semantic?.model)box.append(node('small',`Источник разбора: ${report.semantic.model}. `));
    if(report.coverage)box.append(node('small','Распознавание и фактически услышанный звук по тексту не проверены.'));
    const events=new Map((report.snapshot?.events || []).map(e=>[e.id,e]));
    for(const f of report.findings || []){
      const card=node('article',undefined,'finding');card.append(node('h4',f.title),node('small',f.origin==='rule'?'Проверка журнала':'Гипотеза модели'),node('p',f.explanation),node('p',`Ожидаемое поведение: ${f.expected_behavior}`,'feedback'));
      for(const id of f.evidence_event_ids || [])if(events.has(id))card.append(quote(events.get(id)));
      box.append(card);
    }
    if(report.status==='complete' && !report.findings?.length)box.append(node('p','В этом разборе проблем не найдено. Это не гарантия отсутствия ошибок.','empty'));
    if(['failed','waiting_key'].includes(report.status))box.append(button('Повторить разбор',()=>api(`/api/calls/${call.id}/analysis/retry`,'POST')));
  }
  function caseCard(c){
    const card=node('details',undefined,'case-card');card.append(node('summary',`${c.language.toUpperCase()} · ${c.title} · ${c.status==='accepted'?'принят':c.status==='rejected'?'отклонён':'проверь критерий'}`));
    if(c.synthetic){card.append(node('p',c.expected_behavior),node('pre',c.turns.join('\n')));return card;}
    card.append(node('small',`Источник: разговор ${c.source_call_id.slice(0,8)}. Исходные реплики и критерий сохраняются при редактировании.`));
    const form=node('form'),scenario=node('textarea'),criterion=node('textarea');
    scenario.id=`scenario-${c.id}`;scenario.rows=4;scenario.maxLength=16000;scenario.value=c.turns.join('\n');
    criterion.id=`criterion-${c.id}`;criterion.rows=3;criterion.maxLength=1500;criterion.value=c.expected_behavior;
    const sl=node('label','Реплики собеседника — по одной на строку');sl.htmlFor=scenario.id;
    const cl=node('label','Что считается правильным результатом');cl.htmlFor=criterion.id;
    const submit=node('button',c.status==='accepted'?'Сохранить критерий и проверить':'Принять кейс и проверить улучшение');submit.type='submit';submit.disabled=isActive();
    form.onsubmit=e=>{e.preventDefault();action(()=>api(`/api/learning/cases/${c.id}`,'POST',{status:'accepted',turns:scenario.value.split('\n').map(t=>t.trim()).filter(Boolean),expected_behavior:criterion.value.trim()}));};
    form.append(sl,scenario,cl,criterion,submit);card.append(form,button('Отклонить кейс',()=>api(`/api/learning/cases/${c.id}`,'POST',{status:'rejected'}),isActive()));return card;
  }
  function evaluationDetails(e){
    const d=node('details',undefined,'evaluation');d.append(node('summary',`${labels[e.status] || e.status} · ${e.results.length}/${e.cases.length} кейсов`),node('p',e.limitations,'feedback'));
    for(const pair of e.results){
      const row=node('details',undefined,'comparison');row.append(node('summary',`${pair.language.toUpperCase()} · ${pair.title} · исходный ${pair.baseline.passed?'✓':'✕'} / новый ${pair.candidate.passed?'✓':'✕'}`));
      for(const [label,result] of [['Исходный',pair.baseline],['Новый',pair.candidate]]){
        row.append(node('h4',label),node('p',result.explanation));for(const event of result.events || [])if(event.kind!=='transcript_chunk')row.append(quote(event));
      }d.append(row);
    }return d;
  }
  async function overview(force=false){
    const data=await api('/api/learning');
    const sig=JSON.stringify({active:data.active_prompt_version,cases:data.cases.map(c=>[c.id,c.status,c.reviewed_at]),
      candidates:data.candidates.map(c=>[c.id,c.status,c.evaluation_ids.length]),evaluations:data.evaluations.map(e=>[e.id,e.status,e.results.length]),activeCall:isActive()});
    if(sig===signature && !force)return;signature=sig;
    $('active-prompt').textContent=`Действующая версия: ${data.active_prompt_version}`;
    $('case-count').textContent=`${data.cases.filter(c=>c.synthetic).length} базовых сценариев RU / EN / ID · ${data.cases.filter(c=>!c.synthetic).length} из разговоров`;
    $('learning-cases').replaceChildren(...data.cases.map(caseCard));
    $('learning-candidates').replaceChildren(...data.candidates.map(c=>{
      const card=node('article',undefined,'candidate-card');card.append(node('h4',c.prompt_version),node('small',labels[c.status] || c.status),node('p',c.reason),node('pre',c.instruction));
      for(const e of data.evaluations.filter(e=>e.candidate_id===c.id))card.append(evaluationDetails(e));
      if(c.status!=='testing')card.append(button('Повторить сравнение',()=>api(`/api/learning/candidates/${c.id}/evaluate`,'POST'),isActive()));
      if(c.status==='verified')card.append(button('Включить для новых разговоров',async()=>{await api(`/api/learning/candidates/${c.id}/activate`,'POST');await promptChanged();},isActive()));return card;
    }));
    if(!data.candidates.length)$('learning-candidates').append(node('p','Предложения появятся после разбора ошибок. Модель предлагает только инструкции разговора.','empty'));
    $('learning-rollback').disabled=isActive() || !data.active_prompt_version.includes('.learn-');
  }
  async function refresh(force=false){if(busy)return;busy=true;try{await analysis(force);await overview(force);}finally{busy=false;}}
  $('learning-refresh').onclick=()=>action(()=>refresh(true));
  $('learning-rollback').onclick=()=>action(async()=>{await api('/api/learning/rollback','POST');await promptChanged();});
  setInterval(()=>{if(!isActive())refresh().catch(()=>{});},4000);
  return {refresh,analysis};
}
