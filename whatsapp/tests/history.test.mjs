import test from 'node:test';
import assert from 'node:assert/strict';
import {readPage} from '../history.mjs';
function setup(load){
  const models=[1,2,3].map(n=>({t:n,id:{_serialized:String(n)}}));
  const chats=new Map(['slow','fast','other','four','five'].map(id=>[id,{id,msgs:{getModelsArray:()=>id==='fast'?models:[]}}]));
  globalThis.window={WWebJS:{getMessageModel:m=>m},require(name){
    if(name==='WAWebCollections')return {Chat:{get:id=>chats.get(id)}};
    if(name==='WAWebChatLoadMessages')return {loadEarlierMsgs:load};
    throw new Error('unexpected module');
  }};
}
test('hanging history stays pending, is reused, and does not block a different chat',async()=>{
  let calls=0,finish;
  setup(()=>{calls++;return new Promise(resolve=>{finish=resolve;});});
  try {
    const query={chatId:'slow',limit:2,before:null,timeoutMs:5};
    assert.deepEqual(await readPage(query),{raw:[],exhausted:false,pending:true});
    assert.deepEqual(await readPage(query),{raw:[],exhausted:false,pending:true});
    assert.equal(calls,1);
    const fast=await readPage({...query,chatId:'fast'});
    assert.deepEqual(fast.raw.map(x=>x.t),[2,3]);assert.equal(fast.pending,false);
    finish([]);await new Promise(resolve=>setImmediate(resolve));
    assert.equal(window.__messengerArchiveLoads.size,0);
  }finally{delete globalThis.window;}
});
test('history limits outstanding loads and only declares exhaustion after an empty provider result',async()=>{
  let calls=0;
  setup(()=>{calls++;return new Promise(()=>{});});
  try {
    for(const chatId of ['slow','other','four','five'])await readPage({chatId,limit:2,timeoutMs:2});
    assert.equal(calls,4);
    const result=await readPage({chatId:'fast',limit:5,timeoutMs:2});
    assert.equal(calls,4);assert.equal(result.pending,true);assert.equal(result.exhausted,false);
    window.__messengerArchiveLoads.clear();
    setup(async()=>[]);
    const exhausted=await readPage({chatId:'fast',limit:5,timeoutMs:5});
    assert.equal(exhausted.exhausted,true);assert.equal(exhausted.pending,false);
  }finally{delete globalThis.window;}
});
