import test from 'node:test';
import assert from 'node:assert/strict';
import {sendText} from '../outgoing.mjs';
const policy={enabled:true,send_enabled:true,discover_chats:true,include_chats:[],exclude_chats:[]};
const params={chat:'fixture@c.us',text:'*Привет 😀*',link_preview:true,expires_at:Date.now()/1000+300};
test('persisted native formatting and explicit preview stay on existing client',async()=>{
  const calls=[];
  const client={sendMessage:async(...args)=>{calls.push(args);return {id:{_serialized:'true_fixture@c.us_42'},fromMe:true,to:'fixture@c.us'};}};
  await sendText(client,params,()=>policy);
  assert.deepEqual(calls,[[params.chat,params.text,{sendSeen:false,linkPreview:true,waitUntilMsgSent:true,ignoreQuoteErrors:false}]]);
});
test('bad preview policy and lone surrogates reject before provider invocation',async()=>{
  let calls=0;
  const client={sendMessage:async()=>{calls++;}};
  for (const bad of [{link_preview:'true'},{link_preview:1},{text:'bad\ud800'},{text:'bad\udfff'}]) {
    await assert.rejects(sendText(client,{...params,...bad},()=>policy),/send_target_invalid/);
  }
  assert.equal(calls,0);
});
