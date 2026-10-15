import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {sendText,sendReceiptEvent} from '../outgoing.mjs';
import {cachedBidirectionalPair} from '../identity.mjs';
import {DiskCapture} from '../capture.mjs';

const pn='123456@c.us',lid='987654@lid',owner='111111@c.us';
const params={chat:pn,text:'Synthetic receipt',send_id:'send_'+'a'.repeat(32),expires_at:Date.now()/1000+300};
const policy={enabled:true,send_enabled:true,discover_chats:true,include_chats:[],exclude_chats:[]};
const message=(chat=lid,changes={})=>({id:{_serialized:`true_${chat}_STANZA`,remote:chat,fromMe:true},
  fromMe:true,to:chat,from:owner,...changes});

test('verified PN to LID receipt retains public and actual references with one send',async()=>{
  let calls=0;
  const client={sendMessage:async()=>{calls++;return message();}};
  const receipt=await sendText(client,params,()=>policy,{ownerIds:[owner],verifyAlias:async pair=>{
    assert.deepEqual(pair,{pn,lid});return true;
  }});
  assert.equal(receipt.provider_message_id,`true_${pn}_STANZA`);
  assert.deepEqual(receipt.archive_reference,{provider_chat_id:lid,provider_message_id:`true_${lid}_STANZA`});
  assert.equal(calls,1);
});

test('unverified mapping, unrelated owner, direction or group is never confirmed or retried',async()=>{
  for(const [value,alias] of [[message(),false],[message(),{}],
      [message(lid,{from:'222222@c.us'}),true],[message(lid,{fromMe:false}),true],
      [message('123456@g.us'),true],[message('999999@c.us'),true],
      [message(lid,{id:{_serialized:`true_${lid}_STANZA`,remote:lid,fromMe:false}}),true]]) {
    let calls=0;
    await assert.rejects(sendText({sendMessage:async()=>{calls++;return value;}},params,()=>policy,
      {ownerIds:[owner],verifyAlias:async()=>alias}),/send_receipt_mismatch/);
    assert.equal(calls,1);
  }
});

test('send correlation ID is validated before sending and only exact cached pairs are accepted',async()=>{
  let calls=0;
  await assert.rejects(sendText({sendMessage:async()=>{calls++;}},
    {...params,send_id:'external-request-id'},()=>policy),/send_target_invalid/);
  assert.equal(calls,0);
  for(const [forward,reverse,expected] of [[lid,pn,true],[lid,'999999@c.us',false],[null,pn,false]]) {
    globalThis.window={require:name=>name==='WAWebWidFactory'?{createWid:id=>({_serialized:id})}:
      {getCurrentLid:()=>({_serialized:forward}),getPhoneNumber:()=>({_serialized:reverse})}};
    try{assert.equal(cachedBidirectionalPair({pn,lid}),expected);}
    finally{delete globalThis.window;}
  }
  assert.equal(cachedBidirectionalPair({pn:lid,lid:pn}),false);
});

test('private exact receipt spool survives a lost IPC response without message content',async()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'anymsg-receipt-'));
  try {
    const spool=new DiskCapture(path.join(root,'send-receipts'),{reserveBytes:0});
    const receipt=await sendText({sendMessage:async()=>message()},params,()=>policy,
      {ownerIds:[owner],verifyAlias:async()=>true});
    const file=spool.put(sendReceiptEvent('synthetic-account',params,receipt));
    const raw=fs.readFileSync(path.join(root,'send-receipts',file),'utf8');
    const event=JSON.parse(raw);
    assert.equal(event.send_id,params.send_id);
    assert.equal(event.requested_chat,pn);
    assert.deepEqual(event.archive_reference,receipt.archive_reference);
    assert.equal(raw.includes(params.text),false);
    assert.equal(fs.statSync(path.join(root,'send-receipts',file)).mode&0o077,0);
  }finally{fs.rmSync(root,{recursive:true,force:true});}
});
