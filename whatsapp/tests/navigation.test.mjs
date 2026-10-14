import test from 'node:test';
import assert from 'node:assert/strict';
import {recoverNavigation} from '../navigation.mjs';
const navigation=()=>new Error('Execution context was destroyed, most likely because of a navigation.');
test('initial injection survives navigation using the existing browser and bounded wait',async()=>{
  let calls=0,waits=0;
  const client={pupPage:{async waitForFunction(predicate,options){assert.equal(predicate,'window.Debug?.VERSION != undefined');assert.ok(options.timeout<=60000);waits++;return {async dispose(){}};}},
    async inject(){assert.equal(this,client);if(++calls===1)throw navigation();return 'ready';}};
  recoverNavigation(client);
  assert.equal(await client.inject(),'ready');assert.equal(calls,2);assert.equal(waits,1);
});
test('non-navigation failure propagates without retry or auth reset',async()=>{
  const error=new Error('authentication_failed');let calls=0;
  const client={async inject(){calls++;throw error;},pupPage:{async waitForFunction(){throw new Error('unexpected');}}};
  recoverNavigation(client);
  await assert.rejects(client.inject(),e=>e===error);assert.equal(calls,1);
});
test('repeated navigation fails after three attempts',async()=>{
  let calls=0,waits=0;
  const client={async inject(){calls++;throw navigation();},pupPage:{async waitForFunction(){waits++;return {async dispose(){}};}}};
  recoverNavigation(client);
  await assert.rejects(client.inject(),/Execution context was destroyed/);
  assert.equal(calls,3);assert.equal(waits,2);
});
test('concurrent navigation callbacks share one injection and a failed wait propagates',async()=>{
  let finish,calls=0;
  const client={async inject(){calls++;await new Promise(resolve=>{finish=resolve;});return 'ready';}};
  recoverNavigation(client);
  const first=client.inject(),second=client.inject();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(calls,1);finish();assert.deepEqual(await Promise.all([first,second]),['ready','ready']);
  const failure=new Error('TimeoutError');
  const broken={async inject(){throw navigation();},pupPage:{async waitForFunction(){throw failure;}}};
  recoverNavigation(broken);await assert.rejects(broken.inject(),e=>e===failure);
});
