// Existing authenticated client only. No new session, TCP listener or approval gate.
import {allows,asId,chatId} from './capture.mjs';

export async function sendText(client,params,currentPolicy,{ownerIds=[],verifyAlias=async()=>false}={}) {
  const policy=currentPolicy();
  if(!policy?.enabled || !policy.send_enabled)throw new Error('sending_disabled');
  if(typeof params.chat!=='string' || !/^[a-zA-Z0-9_.:-]{1,140}@(c\.us|g\.us|lid)$/.test(params.chat) ||
      !allows(policy,params.chat))throw new Error('send_target_invalid');
  if(typeof params.text!=='string' || !params.text.trim() || params.text.length>4000 ||
      /[\x00-\x08\x0b\x0c\x0e-\x1f]/.test(params.text) || !params.text.isWellFormed())throw new Error('send_target_invalid');
  if(params.link_preview!==undefined && typeof params.link_preview!=='boolean')
    throw new Error('send_target_invalid');
  if(!Number.isFinite(params.expires_at) || Date.now()/1000>=params.expires_at)
    throw new Error('send_expired');
  if(params.send_id!==undefined && !/^send_[a-f0-9]{32}$/.test(params.send_id))throw new Error('send_target_invalid');
  const options={sendSeen:false,linkPreview:params.link_preview??false,waitUntilMsgSent:true,ignoreQuoteErrors:false};
  if(params.reply_provider_id)options.quotedMessageId=params.reply_provider_id;
  const message=await client.sendMessage(params.chat,params.text,options);
  const id=asId(message?.id),actual=chatId(message),prefix=`true_${actual}_`;
  if(message?.fromMe!==true || message.id?.fromMe===false || typeof id!=='string' ||
      !id.startsWith(prefix) || id.length<=prefix.length || id.length>256 || !/^[a-zA-Z0-9_.:@-]+$/.test(id))
    throw new Error('send_receipt_mismatch');
  // A returned outgoing Message supplies sender proof when the authenticated
  // provider binds its owner identities. Synthetic legacy callers omit it.
  if(ownerIds.length) {
    const sender=asId(message.from || message._data?.from || '');
    if(!ownerIds.includes(sender) || (message.id?.participant && !ownerIds.includes(asId(message.id.participant))))
      throw new Error('send_receipt_mismatch');
    const participant=id.slice(id.lastIndexOf('_')+1);
    if(actual.endsWith('@g.us') && participant.includes('@') && !ownerIds.includes(participant))
      throw new Error('send_receipt_mismatch');
  }
  if(actual!==params.chat && !(params.chat.endsWith('@c.us') && actual.endsWith('@lid') &&
      (await verifyAlias({pn:params.chat,lid:actual}))===true))throw new Error('send_receipt_mismatch');
  const provider_message_id=actual===params.chat?id:`true_${params.chat}_${id.slice(prefix.length)}`;
  if(provider_message_id.length>256)throw new Error('send_receipt_mismatch');
  return {provider_message_id,archive_reference:{provider_chat_id:actual,provider_message_id:id},message};
}

export function sendReceiptEvent(account,params,receipt,observedAt=new Date().toISOString()) {
  if(!/^[a-zA-Z0-9_.-]{1,64}$/.test(account || '') || !/^send_[a-f0-9]{32}$/.test(params.send_id || ''))
    throw new Error('send_target_invalid');
  return {kind:'outgoing_send_receipt',account,send_id:params.send_id,requested_chat:params.chat,
    provider_message_id:receipt.provider_message_id,archive_reference:receipt.archive_reference,observed_at:observedAt};
}
