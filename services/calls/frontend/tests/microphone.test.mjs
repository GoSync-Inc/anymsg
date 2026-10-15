import test from 'node:test';
import assert from 'node:assert/strict';
import { MicrophoneControl, bindTalkButton } from '../src/microphone.mjs';

test('hold-to-talk starts muted and only transmits while held',()=>{
  const sent=[],mic=new MicrophoneControl(value=>sent.push(value));
  mic.setMode(true);mic.start();
  assert.equal(mic.enabled,false);
  mic.press();assert.equal(mic.enabled,true);
  mic.release();assert.equal(mic.enabled,false);
  mic.stop();mic.press();assert.equal(mic.enabled,false);
  assert.deepEqual(sent,[false,true,false]);
});

test('changing microphone mode or stopping clears a held press',()=>{
  const mic=new MicrophoneControl(()=>{});
  mic.setMode(true);mic.start();mic.press();
  mic.setMode(false);mic.toggle();assert.equal(mic.enabled,false);
  mic.setMode(true);assert.equal(mic.enabled,false);
  mic.press();mic.stop();mic.start();assert.equal(mic.enabled,false);
});

test('normal microphone toggle cannot bypass hold-to-talk',()=>{
  const mic=new MicrophoneControl(()=>{});
  mic.start();assert.equal(mic.enabled,true);
  mic.toggle();assert.equal(mic.enabled,false);
  mic.setMode(true);mic.toggle();assert.equal(mic.enabled,false);
});

test('pointer cancellation, lost focus and hidden page release the microphone',()=>{
  const mic=new MicrophoneControl(()=>{}),button=new EventTarget(),win=new EventTarget(),doc=new EventTarget();
  button.setPointerCapture=()=>{};doc.hidden=false;
  bindTalkButton(button,mic,win,doc);mic.setMode(true);mic.start();
  for(const release of ['pointerup','pointercancel','lostpointercapture','blur']) {
    const down=new Event('pointerdown');down.button=0;down.pointerId=1;
    button.dispatchEvent(down);assert.equal(mic.enabled,true);
    button.dispatchEvent(new Event(release));assert.equal(mic.enabled,false);
  }
  mic.press();win.dispatchEvent(new Event('blur'));assert.equal(mic.enabled,false);
  mic.press();doc.hidden=true;doc.dispatchEvent(new Event('visibilitychange'));assert.equal(mic.enabled,false);
});

test('keyboard repeat cannot reopen the microphone after focus was lost',()=>{
  const mic=new MicrophoneControl(()=>{}),button=new EventTarget();
  bindTalkButton(button,mic,new EventTarget(),new EventTarget());mic.setMode(true);mic.start();
  const key=(type,repeat=false)=>{const e=new Event(type);e.code='Space';e.repeat=repeat;button.dispatchEvent(e);};
  key('keydown');assert.equal(mic.enabled,true);
  key('keyup');assert.equal(mic.enabled,false);
  key('keydown');button.dispatchEvent(new Event('blur'));key('keydown',true);assert.equal(mic.enabled,false);
});
