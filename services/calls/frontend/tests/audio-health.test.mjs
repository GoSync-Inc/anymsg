import test from 'node:test';
import assert from 'node:assert/strict';
import { AudioHealth, audioHealthText } from '../src/audio-health.mjs';

test('collects audio loss and concealment without retaining network identifiers',async()=>{
  const health=new AudioHealth();
  const pc={getStats:async()=>new Map([
    ['audio',{type:'inbound-rtp',kind:'audio',packetsReceived:100,packetsLost:3,jitter:.015,
      concealedSamples:480,silentConcealedSamples:240,packetsDiscarded:2,concealmentEvents:2,totalSamplesReceived:48000,
      jitterBufferDelay:2400,jitterBufferEmittedCount:48000,address:'private'}],
    ['video',{type:'inbound-rtp',kind:'video',packetsLost:500}],
    ['candidate',{type:'candidate-pair',address:'private'}],
  ])};
  await health.sample(pc);
  const result=health.snapshot();
  assert.equal(result.packets_lost,3);
  assert.equal(result.concealed_percent,1);
  assert.equal(result.silent_concealed_samples,240);
  assert.equal(result.packets_discarded,2);
  assert.equal(result.jitter_ms,15);
  assert.equal(result.jitter_buffer_ms,50);
  assert.equal(result.samples,1);
  assert.ok(!JSON.stringify(result).includes('private'));
});

test('missing browser statistics remain unknown, rather than reporting clean audio',async()=>{
  const health=new AudioHealth();
  await health.sample({getStats:async()=>new Map()});
  assert.equal(health.snapshot().packets_lost,null);
  assert.equal(health.snapshot().concealed_samples,null);
  await health.sample(null);
  assert.equal(health.snapshot().availability,'unavailable');
});

test('retains observations after a disconnect and observes stalled browser timers',async()=>{
  const health=new AudioHealth();
  await health.sample({getStats:async()=>new Map([['a',{type:'inbound-rtp',mediaType:'audio',packetsReceived:10,packetsLost:2}]])});
  await health.sample({getStats:async()=>{throw new Error('closed');}});
  health.tick(100);health.tick(120);health.tick(500);
  assert.equal(health.snapshot().packets_lost,2);
  assert.equal(health.snapshot().browser_stalls,1);
  assert.equal(health.snapshot().max_poll_gap_ms,380);
});

test('large concealment without silence counters is identified as possible gaps',()=>{
  assert.match(audioHealthText({availability:'supported',concealed_percent:98,total_samples_received:240000,browser_stalls:2}),/Возможны пропуски звука/);
});

test('comfort noise alone does not become a warning about damaged speech',()=>{
  const text=audioHealthText({availability:'supported',concealed_percent:98,concealed_samples:235200,
    silent_concealed_samples:235200,total_samples_received:240000,browser_stalls:0});
  assert.doesNotMatch(text,/Звук приходит с пропусками|Возможны пропуски звука/);
  assert.match(text,/без замещённой тишины: 0%/);
});

test('substantial non-silent concealment is identified as speech gaps',()=>{
  const text=audioHealthText({availability:'supported',concealed_percent:20,concealed_samples:48000,
    silent_concealed_samples:24000,total_samples_received:240000,browser_stalls:0});
  assert.match(text,/Звук приходит с пропусками/);
  assert.match(text,/без замещённой тишины: 10%/);
});
