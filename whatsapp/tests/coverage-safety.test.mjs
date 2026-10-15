// Synthetic local paths only. Intercept blocking FIFO opens before they can hang.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import {DiskCapture, readPolicy} from '../capture.mjs';
import {sendText} from '../outgoing.mjs';

const temporary=()=>fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(),'anymsg-spool-safety-')));

for(const mode of [0o755,0o770,0o777])test(`existing ${mode.toString(8)} capture directory is rejected before publication`,()=>{
  const directory=temporary();
  try {
    fs.chmodSync(directory,mode);
    assert.throws(()=>new DiskCapture(directory,{reserveBytes:0}),/^Error: private_spool_required$/);
    assert.deepEqual(fs.readdirSync(directory),[]);
    assert.equal(fs.statSync(directory).mode&0o777,mode);
  } finally {fs.rmSync(directory,{recursive:true,force:true});}
});

test('private capture directory publishes an exact private original',()=>{
  const directory=temporary();
  try {
    fs.chmodSync(directory,0o700);
    const expected={kind:'synthetic',body:'Fixture message'};
    const name=new DiskCapture(directory,{reserveBytes:0}).put(expected);
    assert.equal(fs.statSync(directory).mode&0o777,0o700);
    assert.equal(fs.statSync(path.join(directory,name)).mode&0o777,0o600);
    assert.deepEqual(JSON.parse(fs.readFileSync(path.join(directory,name),'utf8')),expected);
  } finally {fs.rmSync(directory,{recursive:true,force:true});}
});

test('policy FIFO uses nonblocking admission and is rejected before reading',()=>{
  const directory=temporary(),file=path.join(directory,'.policy');
  const originalOpen=fs.openSync;
  let inspected=0;
  try {
    execFileSync('mkfifo',['-m','600',file]);
    fs.openSync=function(candidate,flags,...rest){
      if(candidate===file){
        inspected++;
        assert.equal(typeof flags,'number');
        assert.notEqual(flags&fs.constants.O_NONBLOCK,0,'policy FIFO must not block before fstat');
        assert.notEqual(flags&fs.constants.O_NOFOLLOW,0,'policy admission must retain symlink refusal');
      }
      return originalOpen(candidate,flags,...rest);
    };
    assert.throws(()=>readPolicy(directory),/^Error: invalid_policy$/);
    assert.equal(inspected,1);
  } finally {
    fs.openSync=originalOpen;
    fs.rmSync(directory,{recursive:true,force:true});
  }
});

for(const id of ['opaque-id','false_123@c.us_message','true_999@c.us_message',
  'true_123@c.us_','true_123@c.us_bad/slash','true_123@c.us_'+'x'.repeat(256)])
test(`contradictory native receipt ${id.slice(0,40)} is rejected after exactly one send`,async()=>{
  let calls=0;
  const policy={enabled:true,send_enabled:true,discover_chats:true,include_chats:[],exclude_chats:[]};
  const client={async sendMessage(){
    calls++;
    return {fromMe:true,to:'123@c.us',id:{remote:'123@c.us',_serialized:id}};
  }};
  await assert.rejects(sendText(client,{chat:'123@c.us',text:'Synthetic message',
    expires_at:Date.now()/1000+60},()=>policy),/^Error: send_receipt_mismatch$/);
  assert.equal(calls,1);
});
