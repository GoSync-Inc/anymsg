import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {createPairingCoordinator} from '../pairing.mjs';

function fixture(plans) {
  let live=0,peak=0,created=0;const clients=[],events=[],qr=[];
  const createClient=()=>{
    const number=++created,plan=plans[number-1] || {};
    assert.ok(number<=plans.length,'unexpected extra client');
    assert.equal(live,0,'previous Chromium must exit before creating a client');
    const client=new EventEmitter(),child=new EventEmitter();
    child.exitCode=null;child.signalCode=null;
    child.finish=(code=0,signal=null)=>{
      child.exitCode=code;child.signalCode=signal;live--;events.push(`exit${number}`);child.emit('exit',code,signal);
    };
    client.pupBrowser={process:()=>child};
    client.info={wid:{_serialized:plan.wid || 'expected@c.us'}};
    client.initialize=async()=>{
      live++;peak=Math.max(peak,live);events.push(`initialize${number}`);
      if(plan.initialize)return plan.initialize(client);
      if(plan.event)client.emit(plan.event,'opaque-qr');
      else client.emit('ready');
      events.push(`initialized${number}`);
    };
    client.destroy=async()=>{
      events.push(`destroy${number}`);
      if(plan.destroy)return plan.destroy(child);
      child.finish();
    };
    clients.push(client);return client;
  };
  const coordinator=createPairingCoordinator({createClient,expectedWid:'expected@c.us',
    onQr:value=>qr.push(value),closeTimeoutMs:10});
  return {coordinator,clients,events,qr,get created(){return created;},get peak(){return peak;}};
}

test('success requires two sequential ready identities, completed initialize and clean closes',async()=>{
  const f=fixture([{initialize:async client=>{client.emit('qr','owner-qr');client.emit('ready');await Promise.resolve();}},{}]);
  assert.deepEqual(await f.coordinator.run(),{restored:true});
  assert.equal(f.created,2);assert.equal(f.peak,1);assert.deepEqual(f.qr,['owner-qr']);
  assert.deepEqual(f.events,['initialize1','destroy1','exit1','initialize2','initialized2','destroy2','exit2']);
  await f.coordinator.stop();assert.equal(f.events.filter(e=>e.startsWith('destroy')).length,2);
});

test('destroy failure and abnormal Chromium exit never claim success or open another browser',async()=>{
  for(const destroy of [async()=>{throw new Error('close broke');},async child=>child.finish(null,'SIGKILL'),async child=>child.finish(1)]){
    const f=fixture([{destroy},{}]);
    await assert.rejects(f.coordinator.run(),/pairing_close_failed/);assert.equal(f.created,1);
  }
});

test('hung or silently incomplete browser close times out without opening another client', {timeout:1000},async()=>{
  for(const destroy of [()=>new Promise(()=>{}),async()=>{}])for(const phase of [1,2]){
    const f=fixture(phase===1?[{destroy},{}]:[{}, {destroy}]);
    await assert.rejects(f.coordinator.run(),/pairing_close_timeout/);assert.equal(f.created,phase);
    assert.equal(f.clients[phase-1].pupBrowser.process().listenerCount('exit'),0);
  }
});

test('restore QR is a failure, is never rendered, and its browser is closed',async()=>{
  const f=fixture([{}, {event:'qr'}]);
  await assert.rejects(f.coordinator.run(),/pairing_restore_requires_qr/);
  assert.equal(f.created,2);assert.deepEqual(f.qr,[]);assert.equal(f.events.at(-1),'exit2');
});

test('both pairing and restored identity must match',async()=>{
  for(const plans of [[{wid:'wrong@c.us'},{}],[{},{wid:'wrong@c.us'}]]){
    const f=fixture(plans);
    await assert.rejects(f.coordinator.run(),/whatsapp_identity_mismatch/);
    assert.equal(f.events.at(-1),`exit${f.created}`);
  }
});

test('restore auth failure, disconnect and a second close failure all reject pairing',async()=>{
  for(const [plan,expected] of [
    [{event:'auth_failure'},/pairing_auth_failed/],
    [{event:'disconnected'},/pairing_disconnected/],
    [{destroy:async child=>child.finish(2)},/pairing_close_failed/],
  ]){
    const f=fixture([{},plan]);
    await assert.rejects(f.coordinator.run(),expected);assert.equal(f.created,2);
  }
});

test('ready does not bypass a failed initialize, and cancellation cannot start restoration',async()=>{
  const f=fixture([{initialize:async client=>{client.emit('ready');throw new Error('initialize_failed');}},{}]);
  await assert.rejects(f.coordinator.run(),/initialize_failed/);assert.equal(f.created,1);
  const g=fixture([{initialize:async()=>{}},{}]);
  const running=g.coordinator.run();
  const rejected=assert.rejects(running,/pairing_cancelled/);
  await new Promise(resolve=>setImmediate(resolve));
  await g.coordinator.stop();await rejected;
  assert.equal(g.created,1);assert.equal(g.events.filter(e=>e==='destroy1').length,1);
});

test('ready waits for initialize to finish before closing its Chromium',async()=>{
  let finish;
  const f=fixture([{initialize:async client=>{
    client.emit('ready');await new Promise(resolve=>{finish=resolve;});
  }},{}]);
  const running=f.coordinator.run();
  await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(f.events,['initialize1']);assert.equal(f.created,1);
  finish();await running;assert.equal(f.peak,1);assert.equal(f.events.at(-1),'exit2');
});
