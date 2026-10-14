// Runs in the existing browser page. A stalled provider history request must not
// consume the IPC deadline or repeatedly spawn more requests for the same chat.
export async function readPage({chatId,limit,before,timeoutMs=5000}) {
  const chat=window.require('WAWebCollections').Chat.get(chatId);
  if(!chat?.msgs)throw new Error('chat_unavailable');
  const loads=window.__messengerArchiveLoads ??= new Map();
  const deadline=Date.now()+timeoutMs;
  const id=m=>m.id?._serialized || (m.id?.id && m.id?.remote ?
    `${m.id.fromMe}_${m.id.remote._serialized || m.id.remote}_${m.id.id}${m.id.participant?'_'+(m.id.participant._serialized || m.id.participant):''}`:'');
  const compare=(a,b)=>a[0]-b[0] || (a[1]<b[1]?-1:a[1]>b[1]?1:0);
  let exhausted=false,pending=false,selected=[];
  for(let step=0;step<=4;step++) {
    const all=chat.msgs.getModelsArray().filter(m=>!m.isNotification && m.type!=='revoked');
    selected=all.filter(m=>!before || compare([m.t,id(m)],before)<0).sort((a,b)=>compare([a.t,id(a)],[b.t,id(b)]));
    if(selected.length>=limit)break;
    if(step===4 || Date.now()>=deadline){pending=true;break;}
    let loading=loads.get(chatId);
    if(!loading){
      if(loads.size>=4){pending=true;break;}
      loading=Promise.resolve().then(()=>window.require('WAWebChatLoadMessages').loadEarlierMsgs({chat}));
      loads.set(chatId,loading);
      const done=()=>{if(loads.get(chatId)===loading)loads.delete(chatId);};
      loading.then(done,done);
    }
    let timer;
    const timeout=Symbol();
    let loaded;
    try {
      loaded=await Promise.race([loading,new Promise(resolve=>{timer=setTimeout(()=>resolve(timeout),Math.max(1,deadline-Date.now()));})]);
    }finally{clearTimeout(timer);}
    if(loaded===timeout){pending=true;break;}
    if(!loaded?.length){exhausted=true;break;}
  }
  return {raw:selected.slice(-limit).map(m=>window.WWebJS.getMessageModel(m)),exhausted,pending};
}
