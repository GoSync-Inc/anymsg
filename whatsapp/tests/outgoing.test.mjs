import test from 'node:test';
import assert from 'node:assert/strict';
import {sendText} from '../outgoing.mjs';

const policy = {enabled:true,send_enabled:true,discover_chats:true,include_chats:[],exclude_chats:[]};
const params = {chat:'fixture@c.us',text:'Synthetic literal text',expires_at:Date.now()/1000+300};

test('send through existing client without marking read or preview, return exact receipt',async()=>{
  let calls=[];
  const client={sendMessage:async(...args)=>{calls.push(args);return {
    id:{_serialized:'true_fixture@c.us_42'},fromMe:true,to:'fixture@c.us'};}};
  const result=await sendText(client,params,()=>policy);
  assert.equal(result.provider_message_id,'true_fixture@c.us_42');
  assert.deepEqual(calls,[[params.chat,params.text,{
    sendSeen:false,linkPreview:false,waitUntilMsgSent:true,ignoreQuoteErrors:false}]]);
});

test('disabled, excluded, expired or invalid targets never call provider',async()=>{
  let calls=0;
  const client={sendMessage:async()=>{calls++;}};
  for(const [changes,scope] of [
    [{}, {...policy,send_enabled:false}],
    [{}, {...policy,exclude_chats:[params.chat]}],
    [{expires_at:Date.now()/1000-1},policy],
    [{chat:'status@broadcast'},policy],
    [{text:'😀'.repeat(2001)},policy]]) {
    await assert.rejects(sendText(client,{...params,...changes},()=>scope));
  }
  assert.equal(calls,0);
});

test('wrong receipt target is never a confirmed send',async()=>{
  const client={sendMessage:async()=>({id:{_serialized:'true_wrong@c.us_42'},fromMe:true,to:'wrong@c.us'})};
  await assert.rejects(sendText(client,params,()=>policy),/send_receipt_mismatch/);
});
