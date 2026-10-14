import test from 'node:test';
import assert from 'node:assert/strict';
import {createPageBounds,PageEvaluationStalled} from '../bounds.mjs';

function deferred(){
  let resolve,reject;
  const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});
  return {promise,resolve,reject};
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));

test('mandatory reads preserve rows and checkpoint flags, and propagate source errors',async()=>{
  const bounds=createPageBounds();
  const page={raw:[{id:'one'},{id:'two'}],exhausted:false,pending:true};
  assert.equal(await bounds.read(async()=>page),page);
  const error=new Error('source_unavailable');
  await assert.rejects(bounds.read(async()=>{throw error;}),e=>e===error);
  assert.equal(await bounds.read(async()=>page),page);
});

test('stalled mandatory read fails instead of returning an empty page; no overlapping retry', {timeout:1000},async()=>{
  const bounds=createPageBounds({readTimeoutMs:5});
  const stuck=deferred();let calls=0;
  await assert.rejects(bounds.read(()=>{calls++;return stuck.promise;}),error=>
    error instanceof PageEvaluationStalled && error.message==='provider_page_stalled');
  await assert.rejects(bounds.read(()=>{calls++;return {raw:[],exhausted:true};}),PageEvaluationStalled);
  assert.equal(calls,1);
  stuck.resolve({raw:[{id:'late'}],exhausted:false});await tick();
  const fresh={raw:[{id:'fresh'}],exhausted:false};
  assert.equal(await bounds.read(()=>fresh),fresh);
});

test('concurrent mandatory reads reserve their slot before invoking the operation',async()=>{
  const bounds=createPageBounds();const waiting=deferred();let calls=0;
  const first=bounds.read(()=>{calls++;return waiting.promise;});
  await assert.rejects(bounds.read(()=>{calls++;}),PageEvaluationStalled);
  waiting.resolve('first');assert.equal(await first,'first');assert.equal(calls,1);
});

test('empty sender IDs skip evaluate, fast names enrich, and lookup errors stay optional',async()=>{
  const bounds=createPageBounds();let calls=0;
  assert.deepEqual(await bounds.names([],()=>{calls++;}),{});
  assert.equal(calls,0);
  assert.deepEqual(await bounds.names(['one'],async ids=>{
    assert.deepEqual(ids,['one']);return {one:'Name'};
  }),{one:'Name'});
  assert.deepEqual(await bounds.names(['one'],()=>{throw new Error('cache_unavailable');}),{});
  assert.deepEqual(await bounds.names(['two'],async()=>({two:'Other'})),{two:'Other'});
});

test('timed out names retain one slot while core pages continue, without reusing another page names', {timeout:1000},async()=>{
  const bounds=createPageBounds({namesTimeoutMs:5});
  const stuck=deferred();let calls=0;
  assert.deepEqual(await bounds.names(['old'],()=>{calls++;return stuck.promise;}),{});
  for(let i=0;i<20;i++)
    assert.deepEqual(await bounds.names(['new'],()=>{calls++;return {new:'Wrong'};}),{});
  assert.equal(calls,1);
  const page={raw:[{id:'new'}],exhausted:false,pending:false};
  assert.equal(await bounds.read(async()=>page),page);
  stuck.resolve({old:'Late old name'});await tick();
  assert.deepEqual(await bounds.names(['new'],async()=>({new:'Fresh name'})),{new:'Fresh name'});
});

test('late rejected evaluates are observed and free their slots', {timeout:1000},async()=>{
  const bounds=createPageBounds({readTimeoutMs:5,namesTimeoutMs:5});
  const read=deferred(),names=deferred();
  await assert.rejects(bounds.read(()=>read.promise),PageEvaluationStalled);
  assert.deepEqual(await bounds.names(['one'],()=>names.promise),{});
  read.reject(new Error('late_read_failure'));names.reject(new Error('late_names_failure'));
  await tick();
  assert.equal(await bounds.read(async()=>42),42);
  assert.deepEqual(await bounds.names(['two'],async()=>({two:'Recovered'})),{two:'Recovered'});
});
