import test from 'node:test';
import assert from 'node:assert/strict';
import {PassThrough} from 'node:stream';

const request=(stream,id,method)=>stream.write(JSON.stringify({id,method,params:{}})+'\n');
async function response(output){
 const chunk=await new Promise(resolve=>output.once('data',resolve));
 return JSON.parse(chunk.toString());
}

test('sending lane responds while media lane is waiting',async()=>{
 const {runLane}=await import('../ipc.mjs');
 const background=new PassThrough(),mediaOutput=new PassThrough();
 const sending=new PassThrough(),sendOutput=new PassThrough();
 let release,started;
 const mediaStarted=new Promise(resolve=>{started=resolve;});
 const mediaBlocked=new Promise(resolve=>{release=resolve;});
 const bg=runLane(background,mediaOutput,{methods:['media'],
  handle:async(r,emit)=>{await emit({id:r.id,chunk:'ZGF0YQ=='});started();await mediaBlocked;return {verified:true};}});
 const fg=runLane(sending,sendOutput,{methods:['send_text'],
  handle:async()=>({provider_message_id:'synthetic'})});
 mediaOutput.resume();request(background,1,'media');await mediaStarted;
 const receipt=response(sendOutput);request(sending,2,'send_text');
 assert.deepEqual(await receipt,{id:2,result:{provider_message_id:'synthetic'}});
 release();background.end();sending.end();await Promise.all([bg,fg]);
});

test('each private lane rejects commands assigned to the other lane',async()=>{
 const {runLane}=await import('../ipc.mjs');
 for(const [allowed,wrong] of [['send_text','media'],['media','send_text']]){
  const input=new PassThrough(),output=new PassThrough();let calls=0;
  const done=runLane(input,output,{methods:[allowed],handle:async()=>{calls++;return {};}});
  const reply=response(output);request(input,1,wrong);
  assert.deepEqual(await reply,{id:1,error:'provider_operation_failed'});
  input.end();await done;assert.equal(calls,0);
 }
});

test('private lane bounds unterminated input and refuses partial requests',async()=>{
 const {runLane}=await import('../ipc.mjs');
 const input=new PassThrough(),output=new PassThrough();let calls=0;
 const done=runLane(input,output,{methods:['media'],handle:async()=>{calls++;}});
 const rejected=assert.rejects(done,/provider_request_too_large/);
 input.end(Buffer.alloc(1024*1024+1,97));await rejected;assert.equal(calls,0);
 const partial=new PassThrough();
 const closed=runLane(partial,new PassThrough(),{methods:['media'],handle:async()=>{calls++;}});
 const bad=assert.rejects(closed,/provider_protocol_error/);
 partial.end('{"id":1');await bad;assert.equal(calls,0);
});

test('lane redacts private errors and preserves synchronized local rejections',async()=>{
 const {runLane}=await import('../ipc.mjs');
 const input=new PassThrough(),output=new PassThrough();
 const done=runLane(input,output,{methods:['send_text'],safe:new Set(['send_expired']),
  handle:async r=>{if(r.id===1)throw new Error('secret-provider-diagnostics');
   if(r.id===2)throw new Error('send_expired');return {provider_message_id:'synthetic'};}});
 for(const [id,expected] of [[1,{error:'provider_operation_failed'}],[2,{error:'send_expired'}],
  [3,{result:{provider_message_id:'synthetic'}}]]){
  const reply=response(output);request(input,id,'send_text');assert.deepEqual(await reply,{id,...expected});
 }
 input.end();await done;
});
