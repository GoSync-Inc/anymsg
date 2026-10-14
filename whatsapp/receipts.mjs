// Passive observations from the already authenticated client. No browser/RPC access.
import {allows,chatId} from './capture.mjs';

const STATES=new Map([[-1,'error'],[0,'pending'],[1,'server'],[2,'delivered'],[3,'read'],[4,'played']]);
export function normalizeReceipt(message,ack,account,observedAt=new Date().toISOString()) {
  if(message?.fromMe!==true || !Number.isInteger(ack) || !STATES.has(ack) ||
      typeof account!=='string' || !/^[a-zA-Z0-9_.-]{1,64}$/.test(account) ||
      typeof observedAt!=='string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(observedAt) ||
      !Number.isFinite(Date.parse(observedAt)) || new Date(observedAt).toISOString()!==observedAt)return null;
  // Read the exact serialized ID. Reconstructing one could change its namespace.
  const id=message.id;
  if(id?.fromMe!==undefined && id.fromMe!==true)return null;
  const providerId=typeof id==='string'?id:id?._serialized;
  if(typeof providerId!=='string' || providerId.length>256 || !/^[a-zA-Z0-9_.:@-]+$/.test(providerId))return null;
  let chat;
  try{chat=chatId(message);}catch{return null;}
  if(!/^[a-zA-Z0-9_.:-]{1,140}@(c\.us|g\.us|lid)$/.test(chat))return null;
  const prefix=`true_${chat}_`;
  if(!providerId.startsWith(prefix) || providerId.length===prefix.length)return null;
  return {kind:'message_receipt',account,platform:'whatsapp',chat,provider_id:providerId,max_id:null,
    state:STATES.get(ack),basis:'whatsapp_message_ack',
    audience:chat.endsWith('@g.us')?'provider_aggregate':'private_peer',observed_at:observedAt,provider_at:null};
}

export function bindReceiptCapture(client,{account,currentPolicy,spool,isVerified,isStopping,onFailure,
    observedAt=()=>new Date().toISOString()}) {
  function capture(message,ack) {
    if(!isVerified() || isStopping())return;
    try {
      const value=normalizeReceipt(message,ack,account,observedAt());
      if(!value || !allows(currentPolicy(),value.chat))return;
      // DiskCapture publishes atomically and fsyncs before returning. Failed writes
      // must stop this provider through the same failure path as message capture.
      spool.put(value);
    }catch(error){onFailure(error);}
  }
  client.on('message_ack',capture);
  return ()=>client.off('message_ack',capture);
}
