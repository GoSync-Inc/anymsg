import test from 'node:test';
import assert from 'node:assert/strict';
import {cachedIdentityObservations,IdentityCache} from '../identity.mjs';

test('cache namespace API verifies pairs without querying contacts or guessing numeric LIDs',()=>{
  const calls=[];
  globalThis.window={require(name){
    calls.push(name);
    if(name==='WAWebWidFactory')return {createWid(id){return {_serialized:id,server:id.split('@')[1]};}};
    if(name==='WAWebApiContact')return {
      getPhoneNumber:wid=>wid._serialized==='700@lid'?{_serialized:'1234567890@c.us'}:null,
      getCurrentLid:wid=>wid._serialized==='1234567890@c.us'?{_serialized:'700@lid'}:null};
    throw new Error('network/cache-miss query is forbidden');
  }};
  try{
    const result=cachedIdentityObservations(['700:2@lid','1234567890@c.us','701@lid','name@lid','123@g.us']);
    assert.equal(result.length,2);
    assert.equal(result[0].identity_id,'700:2@lid');
    assert.equal(result[0].pn,'1234567890@c.us');
    assert.equal(result[0].lid,'700@lid');
    assert.equal(result[0].provenance,'webjs_contact_cache_v1');
    assert.ok(!Number.isNaN(Date.parse(result[0].observed_at)));
    assert.deepEqual(calls,['WAWebWidFactory','WAWebApiContact']);
  }finally{delete globalThis.window;}
});

test('missing optional cache or wrong namespace remains unknown',()=>{
  globalThis.window={require:()=>{throw new Error('unavailable');}};
  try{assert.deepEqual(cachedIdentityObservations(['700@lid']),[]);}
  finally{delete globalThis.window;}
  globalThis.window={require(name){
    if(name==='WAWebWidFactory')return {createWid:id=>({_serialized:id,server:'lid'})};
    return {getPhoneNumber:()=>({_serialized:'700@lid'})};
  }};
  try{assert.deepEqual(cachedIdentityObservations(['700@lid']),[]);}
  finally{delete globalThis.window;}
});

test('live cache bounded eviction and exact source binding preserve original observation time',()=>{
  const cache=new IdentityCache(2);
  const first={identity_id:'700@lid',pn:'1234567890@c.us',lid:'700@lid',observed_at:'2026-10-14T00:00:00Z'};
  cache.remember([first]);
  assert.deepEqual(cache.forEvent({senderId:'700@lid',chatId:'123@g.us'}),[first]);
  assert.deepEqual(cache.forEvent({senderId:'700:2@lid',chatId:'123@g.us'}),[]);
  cache.remember([{identity_id:'701@lid'},{identity_id:'702@lid'}]);
  assert.deepEqual(cache.forEvent({senderId:'700@lid',chatId:'123@g.us'}),[]);
});
