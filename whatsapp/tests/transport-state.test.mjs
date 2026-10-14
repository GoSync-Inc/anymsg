import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {EventEmitter} from 'node:events';
import {connectClient,disconnectCode,writeFailure} from '../transport-state.mjs';

test('logout is terminal; unknown disconnect strings do not leak',()=>{
  for(const reason of ['LOGOUT','UNPAIRED','UNPAIRED_IDLE'])assert.equal(disconnectCode(reason),'whatsapp_logged_out');
  assert.equal(disconnectCode('private diagnostic token=secret'),'provider_disconnected');
});
test('post-ready logout still records the reason and stops the same client',async()=>{
  const client=new EventEmitter();client.initialize=async()=>client.emit('ready');
  const failures=[],stops=[];
  await connectClient(client,async()=>{},c=>failures.push(c),c=>stops.push(c));
  client.emit('disconnected','LOGOUT');
  assert.deepEqual(failures,['whatsapp_logged_out']);assert.deepEqual(stops,[1]);
});
test('initialization and auth failures reject without an unresolved startup promise',async()=>{
  for(const kind of ['init','auth','qr','identity']) {
    const client=new EventEmitter();const failures=[];
    client.initialize=async()=>{if(kind==='init')throw new Error('private');client.emit(kind==='auth'?'auth_failure':kind==='qr'?'qr':'ready','private');};
    const code={init:'provider_initialization_failed',auth:'provider_auth_failed',qr:'whatsapp_pairing_required',identity:'whatsapp_identity_mismatch'}[kind];
    await assert.rejects(connectClient(client,async()=>{throw new Error('whatsapp_identity_mismatch');},c=>failures.push(c),()=>{}),new RegExp(code));
    assert.deepEqual(failures,[code]);
  }
});
test('failure file is private, bounded and never contains raw exception text',()=>{
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'archive-transport-'));
  try {
    writeFailure(directory,'private token=secret');
    const file=path.join(directory,'.transport-failure');const value=JSON.parse(fs.readFileSync(file));
    assert.equal(value.code,'provider_disconnected');assert.ok(value.observed_at);
    assert.equal(fs.statSync(file).mode&0o077,0);assert.ok(fs.statSync(file).size<256);
    writeFailure(directory,'whatsapp_logged_out');assert.equal(JSON.parse(fs.readFileSync(file)).code,'whatsapp_logged_out');
    assert.deepEqual(fs.readdirSync(directory),['.transport-failure']);
  }finally{fs.rmSync(directory,{recursive:true});}
});
