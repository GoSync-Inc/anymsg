// Existing authenticated client only. No new session, TCP listener or approval gate.
import {allows,asId,chatId} from './capture.mjs';

export async function sendText(client,params,currentPolicy) {
  const policy=currentPolicy();
  if(!policy?.enabled || !policy.send_enabled)throw new Error('sending_disabled');
  if(typeof params.chat!=='string' || !/^[a-zA-Z0-9_.:-]{1,140}@(c.us|g.us|lid)$/.test(params.chat) ||
      !allows(policy,params.chat))throw new Error('send_target_invalid');
  if(typeof params.text!=='string' || !params.text.trim() || params.text.length>4000 ||
      /[\x00-\x08\x0b\x0c\x0e-\x1f]/.test(params.text) || !params.text.isWellFormed())throw new Error('send_target_invalid');
  if(params.link_preview!==undefined && typeof params.link_preview!=='boolean')
    throw new Error('send_target_invalid');
  if(!Number.isFinite(params.expires_at) || Date.now()/1000>=params.expires_at)
    throw new Error('send_expired');
  const options={sendSeen:false,linkPreview:params.link_preview??false,waitUntilMsgSent:true,ignoreQuoteErrors:false};
  if(params.reply_provider_id)options.quotedMessageId=params.reply_provider_id;
  const message=await client.sendMessage(params.chat,params.text,options);
  if(!message?.fromMe || chatId(message)!==params.chat || !asId(message.id))
    throw new Error('send_receipt_mismatch');
  return {provider_message_id:asId(message.id),message};
}
