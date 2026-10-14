import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {EventEmitter} from 'node:events';
import {DiskCapture,readPolicy} from '../capture.mjs';
import {normalizeReceipt,bindReceiptCapture} from '../receipts.mjs';
const ACCOUNT='whatsapp-test';
const NOW='2026-10-14T09:00:00.000Z';
const POLICY={discover_chats:true,include_chats:[],exclude_chats:[]};
function message(chat='123@c.us',id='ABC') {
  return {id:{fromMe:true,remote:chat,_serialized:`true_${chat}_${id}`},fromMe:true,to:chat,
    timestamp:12345,body:'Личный текст 🐈',_data:{mediaKey:'private-key',directPath:'/private'},
    getInfo(){throw new Error('unexpected provider RPC');},getChat(){throw new Error('unexpected provider RPC');},
    getContact(){throw new Error('unexpected provider RPC');}};
}
function options(spool,extra={}) {
  return {account:ACCOUNT,currentPolicy:()=>POLICY,spool,isVerified:()=>true,isStopping:()=>false,
    observedAt:()=>NOW,onFailure:error=>{throw error;},...extra};
}
function bind(client,cfg) {
  return bindReceiptCapture(client,cfg);
}
function normalize(msg,ack,account=ACCOUNT,observedAt=NOW) {
  return normalizeReceipt(msg,ack,account,observedAt);
}

test('outgoing ACK states normalize from cached fields without timestamps or private message content',()=>{
  const states=['error','pending','server','delivered','read','played'];
  for(let ack=-1;ack<=4;ack++) {
    const msg=message();
    const value=normalize(msg,ack);
    assert.deepEqual(value,{kind:'message_receipt',account:ACCOUNT,platform:'whatsapp',chat:'123@c.us',
      provider_id:'true_123@c.us_ABC',max_id:null,state:states[ack+1],basis:'whatsapp_message_ack',
      audience:'private_peer',observed_at:NOW,provider_at:null});
    const encoded=JSON.stringify(value);
    for(const absent of ['12345','Личный','private-key','/private'])assert.ok(!encoded.includes(absent));
  }
});
test('LID, phone and group references keep their namespaces and groups claim only provider aggregate',()=>{
  for(const chat of ['123@lid','123@c.us','123@g.us']) {
    const result=normalize(message(chat),3);
    assert.equal(result.chat,chat);
    assert.equal(result.provider_id,`true_${chat}_ABC`);
    assert.equal(result.audience,chat.endsWith('@g.us')?'provider_aggregate':'private_peer');
  }
});
test('chat namespaces are exact and lookalike separators cannot capture unsupported sources',()=>{
  for(const chat of ['123@cXus','123@gXus','123@c/us','123@C.us'])
    assert.equal(normalize(message(chat),3),null);
});
test('incoming, unsupported ACK and unsupported or malformed references are ignored',()=>{
  const incoming=message();incoming.fromMe=false;
  const wrongDirection=message();wrongDirection.id.fromMe=false;
  const wrongPrefix=message();wrongPrefix.id._serialized='false_123@c.us_ABC';
  const mismatched=message();mismatched.id._serialized='true_999@c.us_ABC';
  const control=message();control.id._serialized+='\n';
  const long=message();long.id._serialized+='A'.repeat(256);
  const empty=message();empty.id._serialized='true_123@c.us_';
  const notString=message();notString.id._serialized=123;
  const noID=message();delete noID.id;
  const blankID=message();blankID.id._serialized='';
  for(const msg of [incoming,wrongDirection,wrongPrefix,mismatched,control,long,empty,notString,noID,blankID,
    message('status@broadcast'),message('123@newsletter'),null,{},message('123@c.us.evil')])
    assert.equal(normalize(msg,3),null);
  for(const ack of [null,undefined,true,false,'3',3.1,-2,5,Infinity,NaN,{},[]])
    assert.equal(normalize(message(),ack),null);
  for(const account of ['',null,1,'x'.repeat(65),' bad','bad\n','bad/','аккаунт'])
    assert.equal(normalize(message(),3,account),null);
  for(const observedAt of ['',null,'yesterday','2026-10-14T09:00:00','2026-10-14T09:00:00+08:00'])
    assert.equal(normalize(message(),3,ACCOUNT,observedAt),null);
});
test('existing client emits durable captures across reopen and unsubscribes without browser imports',()=>{
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'wa-receipts-'));
  try {
    const client=new EventEmitter();
    const unbind=bind(client,options(new DiskCapture(directory,{reserveBytes:0})));
    client.emit('message_ack',message(),2);
    client.emit('message_ack',message('123@lid'),3);
    const files=fs.readdirSync(directory).filter(n=>n.endsWith('.json')).sort();
    assert.equal(files.length,2);
    assert.equal(fs.statSync(path.join(directory,files[0])).mode&0o777,0o600);
    const saved=JSON.parse(fs.readFileSync(path.join(directory,files[0]),'utf8'));
    assert.equal(saved.state,'delivered');assert.equal(saved.observed_at,NOW);assert.equal(saved.provider_at,null);
    assert.equal(new DiskCapture(directory,{reserveBytes:0}).directory,directory);
    assert.equal(JSON.parse(fs.readFileSync(path.join(directory,files[1]),'utf8')).chat,'123@lid');
    unbind();client.emit('message_ack',message(),4);
    assert.equal(fs.readdirSync(directory).filter(n=>n.endsWith('.json')).length,2);
  }finally{fs.rmSync(directory,{recursive:true});}
});
test('capture guards block excluded chats, unverified owners and stopping clients',()=>{
  const client=new EventEmitter();const saved=[];let policyReads=0;
  let verified=false,stopping=false,policy=POLICY;
  const unbind=bind(client,options({put:value=>saved.push(value)},{
    isVerified:()=>verified,isStopping:()=>stopping,currentPolicy:()=>{policyReads++;return policy;}}));
  client.emit('message_ack',message(),3);assert.equal(policyReads,0);
  verified=true;stopping=true;client.emit('message_ack',message(),3);assert.equal(policyReads,0);
  stopping=false;policy={...POLICY,exclude_chats:['123@c.us']};client.emit('message_ack',message(),3);
  assert.equal(saved.length,0);
  policy={...POLICY,discover_chats:false,include_chats:['123@lid']};client.emit('message_ack',message(),3);
  assert.equal(saved.length,0);client.emit('message_ack',message('123@lid'),3);assert.equal(saved.length,1);
  unbind();
});
test('policy expiration fails closed before durable capture using the actual policy reader',()=>{
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'wa-receipt-policy-'));
  try {
    fs.writeFileSync(path.join(directory,'.policy'),JSON.stringify({expires_at:50,policy:POLICY}),{mode:0o600});
    const client=new EventEmitter();const failures=[];
    bind(client,options(new DiskCapture(directory,{reserveBytes:0}),{
      currentPolicy:()=>readPolicy(directory,51000),onFailure:error=>failures.push(error.message)}));
    client.emit('message_ack',message(),3);
    assert.deepEqual(failures,['policy_expired']);
    assert.equal(fs.readdirSync(directory).filter(n=>n.endsWith('.json')).length,0);
  }finally{fs.rmSync(directory,{recursive:true});}
});
test('a full durable spool invokes failure handler and never drops an ACK as success',()=>{
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'wa-receipt-full-'));
  try {
    const client=new EventEmitter();const failures=[];
    bind(client,options(new DiskCapture(directory,{reserveBytes:0,maxEntries:0}),{
      onFailure:error=>failures.push(error.message)}));
    client.emit('message_ack',message(),3);
    assert.deepEqual(failures,['spool_full']);assert.equal(fs.readdirSync(directory).length,0);
  }finally{fs.rmSync(directory,{recursive:true});}
});
test('invalid ACKs do not renew policy or trigger durable errors; repeated valid observations stay durable',()=>{
  const client=new EventEmitter();const saved=[];let policyReads=0;
  bind(client,options({put:value=>saved.push(value)},{currentPolicy:()=>{policyReads++;return POLICY;}}));
  for(const ack of [true,'3',5])client.emit('message_ack',message(),ack);
  assert.equal(saved.length,0);assert.equal(policyReads,0);
  client.emit('message_ack',message(),3);client.emit('message_ack',message(),3);
  assert.equal(saved.length,2);assert.deepEqual(saved[0],saved[1]);
});
