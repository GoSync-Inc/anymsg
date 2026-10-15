import test from 'node:test';
import assert from 'node:assert/strict';
import { settleDisconnectedCall } from '../src/call-cleanup.mjs';

test('a disconnected browser closes its still-waiting server call',async()=>{
  const operations=[];
  const api=async(path,method='GET')=>{
    operations.push([path,method]);
    return {id:'own-call',status:method==='POST'?'finished':'waiting_for_browser'};
  };
  const call=await settleDisconnectedCall(api,'own-call');
  assert.equal(call.status,'finished');
  assert.deepEqual(operations,[['/api/calls/own-call','GET'],['/api/calls/own-call/stop','POST']]);
});

test('a terminal server call is preserved after peer disconnect',async()=>{
  const call={id:'own-call',status:'finished',end_reason:'task_completed'};
  assert.equal(await settleDisconnectedCall(async(path,method)=>{
    assert.equal(path,'/api/calls/own-call');assert.equal(method,undefined);return call;
  },'own-call'),call);
});
