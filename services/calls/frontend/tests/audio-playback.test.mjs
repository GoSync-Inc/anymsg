import test from 'node:test';
import assert from 'node:assert/strict';
import { startRemoteAudio, stopRemoteAudio } from '../src/audio-playback.mjs';

test('remote WebRTC track starts a media player and releases it on stop',async()=>{
  const stream={test:true},calls=[];
  const player={setAttribute:(...v)=>calls.push(['attribute',...v]),play:()=>{calls.push(['play']);return Promise.resolve();},
    pause:()=>calls.push(['pause']),remove:()=>calls.push(['remove'])};
  const doc={createElement:type=>{assert.equal(type,'audio');return player;},body:{append:p=>{assert.equal(p,player);calls.push(['append']);}}};
  const result=startRemoteAudio(stream,doc);
  await result.ready;
  assert.equal(result.player.srcObject,stream);
  assert.equal(result.player.autoplay,true);
  assert.ok(calls.some(v=>v[0]==='play'));
  stopRemoteAudio(result.player);
  assert.equal(player.srcObject,null);
  assert.deepEqual(calls.slice(-2),[['pause'],['remove']]);
});
