// Contract regression proofs use fake provider methods and local subprocesses only.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {spawn} from 'node:child_process';
import {once, EventEmitter} from 'node:events';
import {PassThrough} from 'node:stream';
import {createHash} from 'node:crypto';
import {readPolicy, DiskCapture, chatId} from '../capture.mjs';
import {cachedIdentityObservations, IdentityCache} from '../identity.mjs';
import {runLane, sendingSocket} from '../ipc.mjs';
import {executeOperation} from '../operations.mjs';
import {sendText} from '../outgoing.mjs';
import {readPage} from '../history.mjs';
import {cachedDialogs} from '../read-state.mjs';
import {download} from '../media.mjs';
import https from 'node:https';

const policy={enabled:true,send_enabled:true,discover_chats:true,include_chats:[],exclude_chats:[],operations_enabled:[]};
const job=(operation,parameters={},chat='123@c.us')=>({operation,parameters,provider_chat:chat,
  provider_message_id:`true_${chat}_m1`,expires_at:Date.now()/1000+300});
const message=(chat='123@c.us',extra={})=>({id:{_serialized:`true_${chat}_m1`,remote:chat},
  fromMe:true,to:chat,body:'original',...extra});
const allowed=operation=>({...policy,operations_enabled:[operation]});
const temporary=()=>fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(),'anymsg-contract-')));

test('capture catalog and text sending reject lookalike chat namespaces before provider calls',async()=>{
  const invalid=['123@cXus','456@gYus','789@c/us'];
  for(const chat of invalid){
    assert.throws(()=>chatId(message(chat)),/unsupported_chat/);
    let calls=0;
    await assert.rejects(sendText({async sendMessage(){calls++;return message(chat);}},
      {chat,text:'synthetic',expires_at:Date.now()/1000+60},()=>policy),/send_target_invalid/);
    assert.equal(calls,0);
  }
  const previous=global.window;
  try {
    global.window={require(name){assert.equal(name,'WAWebCollections');return {Chat:{getModelsArray:()=>
      [...invalid,'123@c.us','456@g.us','789@lid'].map(id=>({id:{_serialized:id,server:id.split('@')[1]}}))}};}};
    assert.deepEqual(cachedDialogs({limit:20,ownerIds:[]}).map(row=>row[0]),['123@c.us','456@g.us','789@lid']);
  } finally {global.window=previous;}
});

test('private lane rejects invalid safe-integer request IDs without invoking the provider',async()=>{
  for(const id of [0,-1,1.5,Number.MAX_SAFE_INTEGER+1,'1',null]){
    const input=new PassThrough(),output=new PassThrough();let calls=0;
    const finished=runLane(input,output,{methods:['page'],handle:async()=>{calls++;return {};}});
    const received=once(output,'data');input.end(JSON.stringify({id,method:'page'})+'\n');
    assert.equal(JSON.parse((await received)[0]).error,'provider_operation_failed');
    await finished;assert.equal(calls,0);
  }
});

test('private response framing rejects an oversized result and keeps every emitted frame bounded',async()=>{
  const input=new PassThrough(),output=new PassThrough(),frames=[];
  output.on('data',chunk=>frames.push(chunk));
  const finished=runLane(input,output,{methods:['page'],handle:async()=>({body:'x'.repeat(1024*1024)})});
  input.end(JSON.stringify({id:1,method:'page'})+'\n');await finished;
  assert.equal(frames.length,1);assert.ok(frames[0].length<=1024*1024);
  assert.deepEqual(JSON.parse(frames[0]),{id:1,error:'provider_operation_failed'});
});

test('private sending descriptor is required and cannot be an unsafe integer or standard stream',()=>{
  const original=process.env.ANYMSG_WHATSAPP_SEND_FD;
  try{
    for(const value of ['', '0','1','2','3.5','-3','9007199254740992','3x']){
      process.env.ANYMSG_WHATSAPP_SEND_FD=value;
      assert.throws(sendingSocket,/private_sending_channel_required/);
    }
    delete process.env.ANYMSG_WHATSAPP_SEND_FD;
    assert.throws(sendingSocket,/private_sending_channel_required/);
  }finally{
    if(original===undefined)delete process.env.ANYMSG_WHATSAPP_SEND_FD;
    else process.env.ANYMSG_WHATSAPP_SEND_FD=original;
  }
});

test('policy reader refuses symlinks oversized files expired timestamps and invalid collections',()=>{
  const dir=temporary(),file=path.join(dir,'.policy');
  try{
    for(const saved of [{expires_at:1,policy},{expires_at:Date.now()/1000+60,policy:{...policy,include_chats:'all'}},
      {expires_at:'forever',policy}]){
      fs.writeFileSync(file,JSON.stringify(saved),{mode:0o600});
      assert.throws(()=>readPolicy(dir));
    }
    fs.writeFileSync(file,' '.repeat(1024*1024+1));
    assert.throws(()=>readPolicy(dir),/invalid_policy/);
    fs.unlinkSync(file);const target=path.join(dir,'target');
    fs.writeFileSync(target,JSON.stringify({expires_at:Date.now()/1000+60,policy}),{mode:0o600});
    fs.symlinkSync(target,file);assert.throws(()=>readPolicy(dir));
  }finally{fs.rmSync(dir,{recursive:true,force:true});}
});

test('capture record and disk reserve boundaries fail before publishing any file',()=>{
  const dir=temporary();
  try{
    const spool=new DiskCapture(dir,{reserveBytes:0});
    assert.throws(()=>spool.put({body:'x'.repeat(512*1024)}),/capture_too_large/);
    const reserved=new DiskCapture(dir,{reserveBytes:Number.MAX_SAFE_INTEGER});
    assert.throws(()=>reserved.put({message:'synthetic'}),/disk_reserve/);
    assert.deepEqual(fs.readdirSync(dir),[]);
  }finally{fs.rmSync(dir,{recursive:true,force:true});}
});

test('cache identity lookup is capped at 200 and source observations are exact and bounded',()=>{
  let calls=0;
  globalThis.window={require(name){
    if(name==='WAWebWidFactory')return {createWid:id=>({_serialized:id,server:'lid'})};
    if(name==='WAWebApiContact')return {getPhoneNumber:wid=>{calls++;return {_serialized:wid._serialized.split('@')[0]+'@c.us'};},
      queryWidExists(){throw new Error('network lookup forbidden');}};
    throw new Error('unexpected lookup');
  }};
  try{
    const observed=cachedIdentityObservations(Array.from({length:250},(_,i)=>`${i+1}@lid`));
    assert.equal(observed.length,200);assert.equal(calls,200);
    assert.ok(observed.every(x=>x.provenance==='webjs_contact_cache_v1' && Number.isFinite(Date.parse(x.observed_at))));
    const cache=new IdentityCache();cache.remember(Array.from({length:10001},(_,i)=>({identity_id:`${i}@lid`})));
    assert.equal(cache.values.size,10000);assert.deepEqual(cache.forEvent({senderId:'0@lid'}),[]);
    assert.deepEqual(cache.forEvent({senderId:'10000@lid',chatId:'10000@lid'}),[{identity_id:'10000@lid'}]);
  }finally{delete globalThis.window;}
});

test('history uses strict timestamp and ID cursors and excludes revoked and notification rows',async()=>{
  const rows=[{t:10,id:{_serialized:'b'}},{t:9,id:{_serialized:'z'}},{t:10,id:{_serialized:'a'}},
    {t:1,id:{_serialized:'ignored-notification'},isNotification:true},
    {t:2,id:{_serialized:'ignored-revoke'},type:'revoked'}];
  let loads=0;
  globalThis.window={WWebJS:{getMessageModel:m=>m},require(name){
    if(name==='WAWebCollections')return {Chat:{get:()=>({msgs:{getModelsArray:()=>rows}})}};
    return {loadEarlierMsgs:async()=>{loads++;return [];}};
  }};
  try{
    const result=await readPage({chatId:'123@c.us',limit:2,before:[10,'b']});
    assert.deepEqual(result.raw.map(x=>x.id._serialized),['z','a']);assert.equal(loads,0);
    const older=await readPage({chatId:'123@c.us',limit:2,before:[9,'z']});
    assert.deepEqual(older.raw,[]);assert.equal(older.exhausted,true);assert.equal(older.pending,false);
  }finally{delete globalThis.window;}
});

test('history stops after four earlier-load attempts and reports pending instead of exhaustion',async()=>{
  let calls=0;
  globalThis.window={WWebJS:{getMessageModel:m=>m},require(name){
    if(name==='WAWebCollections')return {Chat:{get:()=>({msgs:{getModelsArray:()=>[]}})}};
    return {loadEarlierMsgs:async()=>{calls++;return [{}];}};
  }};
  try{
    assert.deepEqual(await readPage({chatId:'123@c.us',limit:100}),{raw:[],pending:true,exhausted:false});
    assert.equal(calls,4);
  }finally{delete globalThis.window;}
});

test('text validation rejects malformed or absent values and one provider exception is never retried',async()=>{
  let calls=0;
  const client={sendMessage:async()=>{calls++;throw new Error('synthetic provider failure');}};
  const params={chat:'123@c.us',text:'hello',expires_at:Date.now()/1000+300};
  for(const change of [{text:''},{text:' \n'},{text:'bad\0text'},{text:'\ud800'},
    {link_preview:'yes'},{expires_at:undefined},{expires_at:Infinity},{expires_at:NaN}])
    await assert.rejects(sendText(client,{...params,...change},()=>policy));
  assert.equal(calls,0);
  await assert.rejects(sendText(client,params,()=>policy),/synthetic provider failure/);
  assert.equal(calls,1);
  const options=[];client.sendMessage=async(...args)=>{options.push(args[2]);return message();};
  await sendText(client,{...params,text:'😀'.repeat(2000),reply_provider_id:'false_123@c.us_reply',link_preview:true},()=>policy);
  assert.deepEqual(options,[{sendSeen:false,linkPreview:true,waitUntilMsgSent:true,ignoreQuoteErrors:false,
    quotedMessageId:'false_123@c.us_reply'}]);
});

// All remaining reviewed administration/message families exercise their actual
// adapter branches. A true provider return still means submitted, not verified effect.
const mutations=[
  ['pin_message',{seconds:60},'message','pin',[60]],['unpin_message',{},'message','unpin',[]],
  ['star_message',{},'message','star',[]],['unstar_message',{},'message','unstar',[]],
  ['mark_unread',{},'chat','markUnread',[]],['archive_chat',{},'chat','archive',[]],
  ['unarchive_chat',{},'chat','unarchive',[]],['unmute_chat',{},'chat','unmute',[]],
  ['pin_chat',{},'chat','pin',[]],['unpin_chat',{},'chat','unpin',[]],
  ['rename_group',{title:'New'},'group','setSubject',['New']],
  ['set_group_description',{description:'Description'},'group','setDescription',['Description']],
  ['remove_participant',{user_id:'456@c.us'},'group','removeParticipants',[['456@c.us']]],
  ['promote_participant',{user_id:'456@c.us'},'group','promoteParticipants',[['456@c.us']]],
  ['demote_participant',{user_id:'456@c.us'},'group','demoteParticipants',[['456@c.us']]],
  ['leave_group',{},'group','leave',[]],['revoke_invite_link',{},'group','revokeInvite',[]],
];
for(const [op,params,type,method,args] of mutations)test(`explicit ${op} uses one native call and reports submitted`,async()=>{
  const chat=type==='group'?'123@g.us':'123@c.us',calls=[];
  const target={id:{_serialized:chat},isGroup:type==='group',[method]:async(...values)=>{calls.push(values);return true;}};
  const m=message(chat,{[method]:target[method]});
  const client={getChatById:async()=>target,getMessageById:async()=>m};
  assert.deepEqual(await executeOperation(client,job(op,params,chat),()=>allowed(op)),{outcome:'submitted',data:{}});
  assert.deepEqual(calls,[args]);
  target[method]=async()=>{calls.push('failed-attempt');throw new Error('provider private details');};
  m[method]=target[method];
  assert.deepEqual(await executeOperation(client,job(op,params,chat),()=>allowed(op)),
    {outcome:'unknown',data:{code:'provider_operation_uncertain'}});
  assert.equal(calls.length,2);
});

test('mute expiry and invite data are bounded and remain submitted observations',async()=>{
  let until;const target={id:{_serialized:'123@g.us'},isGroup:true,mute:async value=>{until=value;},getInviteCode:async()=> 'synthetic_invite'};
  const client={getChatById:async()=>target};
  const start=Date.now();const muted=await executeOperation(client,job('mute_chat',{seconds:60},'123@g.us'),()=>allowed('mute_chat'));
  assert.equal(muted.outcome,'submitted');assert.ok(until.getTime()>=start+60000 && until.getTime()<=Date.now()+60000);
  assert.deepEqual(await executeOperation(client,job('get_invite_link',{},'123@g.us'),()=>allowed('get_invite_link')),
    {outcome:'submitted',data:{invite_link:'https://chat.whatsapp.com/synthetic_invite'}});
});

test('contact delete requires phone namespace and block actions match exact fetched contact',async()=>{
  const calls=[],client={deleteAddressbookContact:async id=>calls.push(['delete',id]),
    getContactById:async id=>({id:{_serialized:id},block:async()=>calls.push(['block',id]),unblock:async()=>calls.push(['unblock',id])})};
  for(const [op,params] of [['delete_contact',{user_id:'456@c.us'}],['block_contact',{user_id:'456@lid'}],['unblock_contact',{user_id:'456@lid'}]])
    assert.equal((await executeOperation(client,job(op,params,''),()=>allowed(op))).outcome,'submitted');
  assert.deepEqual(calls,[['delete','456'],['block','456@lid'],['unblock','456@lid']]);
  await assert.rejects(executeOperation(client,job('delete_contact',{user_id:'456@lid'},''),()=>allowed('delete_contact')),/operation_unsupported/);
  client.getContactById=async()=>({id:{_serialized:'999@c.us'},block:async()=>calls.push('unexpected')});
  await assert.rejects(executeOperation(client,job('block_contact',{user_id:'456@c.us'},''),()=>allowed('block_contact')),/operation_target_unavailable/);
  assert.equal(calls.length,3);
});

test('participant pinned contact blocked profile and info reads expose bounded observations and unknown collections',async()=>{
  const client={info:{wid:{_serialized:'123@c.us'},pushname:'Self',platform:'web'},
    getChatById:async()=>({id:{_serialized:'123@g.us'},isGroup:true,participants:undefined}),
    getPinnedMessages:async()=>[message('999@c.us'),message('123@c.us',{body:'x'.repeat(1000)})],
    getBlockedContacts:async()=>undefined,
    getContactById:async id=>({id:{_serialized:id},name:'Name',token:'secret'}),
    getMessageById:async()=>message('123@c.us',{getInfo:async()=>({delivery:Array.from({length:130},()=>({id:'456@c.us',t:1})),
      read:[],played:[],deliveryRemaining:7,private:'secret'})})};
  const participants=(await executeOperation(client,job('list_participants',{},'123@g.us'),()=>allowed('list_participants'))).data;
  assert.equal(participants.state,'unknown');assert.equal(participants.limit,50);assert.equal(participants.coverage,'provider_observation');
  const blocked=(await executeOperation(client,job('list_blocked',{},''),()=>allowed('list_blocked'))).data;
  assert.equal(blocked.state,'unknown');assert.deepEqual(blocked.items,[]);
  const pinned=(await executeOperation(client,job('get_pinned_messages'),()=>allowed('get_pinned_messages'))).data;
  assert.equal(pinned.items.length,1);assert.equal(pinned.items[0].provider_chat,'123@c.us');assert.equal(pinned.items[0].body_truncated,true);
  const profile=(await executeOperation(client,job('get_profile',{},''),()=>allowed('get_profile'))).data;
  assert.equal(profile.coverage,'cached_self');assert.equal(profile.name,'Self');
  const contact=(await executeOperation(client,job('get_contact',{user_id:'456@c.us'},''),()=>allowed('get_contact'))).data;
  assert.equal(contact.id,'456@c.us');assert.ok(!JSON.stringify(contact).includes('secret'));
  const info=(await executeOperation(client,job('get_message_info'),()=>allowed('get_message_info'))).data;
  assert.equal(info.delivery.length,100);assert.equal(info.delivery_truncated,true);assert.equal(info.delivery_remaining,7);
  assert.equal(info.read_truncated,false);assert.ok(Buffer.byteLength(JSON.stringify(info))<64*1024);
});

test('pagination and reaction namespace evidence never equate a phone actor to a LID self',async()=>{
  const client={info:{wid:{_serialized:'123@lid'}},getMessageById:async()=>message('123@c.us',{getReactions:async()=>[
    {id:'👍',hasReactionByMe:true,senders:[{senderId:'123@c.us'},{senderId:'123@lid'},{senderId:'456@lid'}]}]})};
  const result=await executeOperation(client,job('get_reactions',{offset:0,limit:2}),()=>allowed('get_reactions'));
  assert.equal(result.data.items[0].is_by_self,null);assert.equal(result.data.items[1].is_by_self,true);
  assert.equal(result.data.items[0].group_has_reaction_by_self,true);
  assert.equal(result.data.has_more,true);assert.equal(result.data.next_offset,2);
  for(const p of [{offset:-1},{offset:100001},{limit:0},{limit:101},{limit:1.5}])
    await assert.rejects(executeOperation(client,job('get_reactions',p),()=>allowed('get_reactions')),/operation_invalid_parameters/);
});

test('business labels require business identity and bounded distinct labels before mutation',async()=>{
  let business=false,calls=0;
  const client={info:{wid:{_serialized:'123@c.us'}},getContactById:async()=>({isBusiness:business}),
    getLabels:async()=>[{id:3,name:'Synthetic',hexColor:255,token:'secret'}],
    getChatById:async()=>({id:{_serialized:'123@c.us'},changeLabels:async values=>{assert.deepEqual(values,['3']);calls++;}})};
  await assert.rejects(executeOperation(client,job('get_labels',{},''),()=>allowed('get_labels')),/operation_unsupported/);
  business=true;
  const data=(await executeOperation(client,job('get_labels',{},''),()=>allowed('get_labels'))).data;
  assert.deepEqual(data.items,[{id:'3',name:'Synthetic',color:255}]);assert.equal(data.coverage,'provider_observation');
  for(const label_ids of [['3','3'],['bad label'],Array.from({length:21},(_,i)=>String(i))])
    await assert.rejects(executeOperation(client,job('set_chat_labels',{label_ids}),()=>allowed('set_chat_labels')),/operation_invalid_parameters/);
  assert.equal(calls,0);
  assert.equal((await executeOperation(client,job('set_chat_labels',{label_ids:['3']}),()=>allowed('set_chat_labels'))).outcome,'submitted');
  assert.equal(calls,1);
});

test('poll contact location and group validators reject bounds and duplicates before provider mutation',async()=>{
  let calls=0;
  const client={sendMessage:async()=>{calls++;return message();},createGroup:async()=>{calls++;},getContactById:async id=>({id:{_serialized:id}})};
  const cases=[['send_poll',{question:'Q',options:['A'],multiple:false}],['send_poll',{question:'Q',options:['A','A'],multiple:false}],
    ['send_poll',{question:'Q',options:['A','B'],multiple:'yes'}],
    ['send_poll',{question:'Q',options:Array.from({length:11},(_,i)=>String(i)),multiple:false}],
    ['send_contact',{contact_ids:['456@c.us','456@c.us']}],['send_contact',{contact_ids:Array.from({length:21},(_,i)=>`${i}@c.us`)}],
    ['send_location',{latitude:NaN,longitude:0}],['send_location',{latitude:91,longitude:0}],['send_location',{latitude:0,longitude:181}],
    ['create_group',{title:'Fixture',user_ids:['456@c.us','456@c.us']}],
    ['create_group',{title:'Fixture',user_ids:Array.from({length:21},(_,i)=>`${i}@c.us`)}]];
  for(const [op,p] of cases)await assert.rejects(executeOperation(client,job(op,p,op==='create_group'?'':'123@c.us'),()=>allowed(op)));
  assert.equal(calls,0);
  class Poll{constructor(question,options,settings){this.settings=settings;}}
  class Location{constructor(latitude,longitude,settings){this.latitude=latitude;this.longitude=longitude;}}
  for(const [op,p] of [['send_poll',{question:'Q',options:['A','B'],multiple:false}],['send_contact',{contact_ids:['456@c.us']}],
    ['send_location',{latitude:-90,longitude:180,description:'Fixture'}]]){
    assert.equal((await executeOperation(client,job(op,p),()=>allowed(op),{library:{Poll,Location}})).outcome,'confirmed');
  }
  assert.equal(calls,3);
});

test('registered file modes MIME requested hash and display-name boundaries fail before send',async()=>{
  const dir=temporary();
  try{
    const bytes=Buffer.from('registered synthetic file'),digest=createHash('sha256').update(bytes).digest('hex');
    const file=path.join(dir,digest);fs.writeFileSync(file,bytes);let calls=0;
    const base={...job('send_file',{mode:'document'}),registered_file_path:file,trusted_blob_directory:dir,
      file_digest:digest,file_bytes:bytes.length,file_mime:'text/plain',file_name:'fixture.txt'};
    const client={sendMessage:async()=>{calls++;return message();}};
    class MessageMedia{constructor(...args){this.args=args;}}
    for(const changes of [{parameters:{mode:'photo'}},{parameters:{mode:'document',sha256:'0'.repeat(64)}},
      {parameters:{mode:'document',caption:'x'.repeat(1025)}},{file_name:'../outside'},
      {file_mime:'text/plain\nHeader: value'},{registered_file_path:path.join(dir,'..','outside')},{file_bytes:0}])
      await assert.rejects(executeOperation(client,{...base,...changes},()=>allowed('send_file'),{library:{MessageMedia}}));
    assert.equal(calls,0);
    for(const [mode,mime] of [['document','text/plain'],['photo','image/png'],['video','video/mp4'],
      ['audio','audio/ogg'],['voice','audio/ogg'],['sticker','image/webp']])
      assert.equal((await executeOperation(client,{...base,parameters:{mode},file_mime:mime},()=>allowed('send_file'),{library:{MessageMedia}})).outcome,'confirmed');
    assert.equal(calls,6);
  }finally{fs.rmSync(dir,{recursive:true,force:true});}
});

test('media HTTPS download rejects redirects and destroys requests with a bounded idle timeout',async()=>{
  const original=https.get,requests=[];
  try{
    const {EventEmitter}=await import('node:events');
    https.get=(url,options)=>{
      assert.equal(url.protocol,'https:');assert.equal(url.hostname,'mmg.whatsapp.net');assert.equal(options.timeout,60000);
      const request=new EventEmitter();request.destroy=()=>{request.destroyed=true;};
      const response=new PassThrough();response.statusCode=302;response.headers={location:'https://untrusted.invalid'};
      requests.push({request,response});queueMicrotask(()=>request.emit('response',response));return request;
    };
    const metadata={type:'document',size:1,directPath:'/v/synthetic.enc',mediaKey:Buffer.alloc(32,1).toString('base64'),
      filehash:Buffer.alloc(32,2).toString('base64'),encFilehash:Buffer.alloc(32,3).toString('base64')};
    await assert.rejects(async()=>{for await(const _ of download(metadata,10)){}},/media_network_error/);
    assert.equal(requests.length,1);assert.equal(requests[0].request.destroyed,true);assert.equal(requests[0].response.destroyed,true);
  }finally{https.get=original;}
});

// The actual provider entrypoint runs offline with the upstream package resolved
// to a fake client. No browser is loaded, provider network called or account used.
async function providerFixture({wrongIdentity=false,reserveBytes=0}={}){
  const dir=temporary(),events=path.join(dir,'events'),session=path.join(dir,'session');
  fs.mkdirSync(events,{mode:0o700});fs.mkdirSync(session,{mode:0o700});
  const cfg={account:'wa-contract',expected_wid:'123456789@c.us',events_directory:events,session_directory:session,
    chromium_executable:'/synthetic/chromium',disk_reserve_bytes:reserveBytes,wrongIdentity};
  const config=path.join(dir,'config.json');fs.writeFileSync(config,JSON.stringify(cfg),{mode:0o600});
  fs.writeFileSync(path.join(events,'.policy'),JSON.stringify({expires_at:Date.now()/1000+60,policy}),{mode:0o600});
  const fake=path.join(dir,'fake.mjs');
  fs.writeFileSync(fake,`import {EventEmitter} from 'node:events';import fs from 'node:fs';
const cfg=JSON.parse(fs.readFileSync(process.argv[2]));
function msg(n,chat='123@c.us',fromMe=false){return {id:{_serialized:(fromMe?'true_':'false_')+chat+'_'+n,remote:chat},
 from:chat,to:chat,fromMe,body:'*observed*',timestamp:1792000000+n,hasMedia:true,type:'ptt',isForwarded:true,
 _data:{size:17,mimetype:'audio/ogg',quotedStanzaID:'reply',quotedParticipant:'123456789@c.us',notifyName:'Name',latestEditSenderTimestampMs:1792000020000},
 mentionedIds:['123456789@c.us']};}
export class LocalAuth{constructor(options){this.options=options;}}
export class Message{constructor(client,raw){Object.assign(this,raw);}}
export class Client extends EventEmitter{
 constructor(options){super();fs.writeFileSync(cfg.session_directory+'/observed-options.json',JSON.stringify(options));
 this.info={wid:{_serialized:cfg.wrongIdentity?'999999999@c.us':cfg.expected_wid}};
 this.pupPage={evaluate:async(fn,args)=>{
  if(fn.name==='cachedOwnerIds')return [];
  if(fn.name==='readPage'){
   const rows=Array.from({length:150},(_,i)=>({...msg(i+1),t:1792000001+i,body:'x'.repeat(20000)}));
   globalThis.window={WWebJS:{getMessageModel:m=>m},require:name=>name==='WAWebCollections'?
    {Chat:{get:()=>({msgs:{getModelsArray:()=>rows}})}}:{loadEarlierMsgs:async()=>[]}};
   return fn(args);
  }
  return fn.name==='cachedIdentityObservations'?[]:{};
 }};
 }
 async inject(){}
 async initialize(){this.emit('message_create',msg(0));this.emit('ready');}
 async destroy(){}
 async sendMessage(chat,text,options){
  fs.writeFileSync(cfg.session_directory+'/send-options.json',JSON.stringify(options));
  if(text==='events'){
   this.emit('message_create',msg(1));this.emit('message_edit',msg(2),'_edited_');
   this.emit('message_revoke_everyone',msg(3),msg(3));this.emit('message_revoke_me',msg(4));
   this.emit('message_create',msg(5,'status@broadcast'));this.emit('message_create',msg(6,'123@newsletter'));
   this.emit('message_ack',msg(7,chat,true),3);
  }
  return {...msg(8,chat,true),body:text};
 }
}
export default {Client,LocalAuth,Message};`);
  const loader=path.join(dir,'loader.mjs');fs.writeFileSync(loader,
    `export async function resolve(specifier,context,next){if(specifier==='whatsapp-web.js')return {url:${JSON.stringify(pathToFileURL(fake).href)},shortCircuit:true};return next(specifier,context);}`);
  const register=path.join(dir,'register.mjs');fs.writeFileSync(register,
    `import {register} from 'node:module';register(${JSON.stringify(pathToFileURL(loader).href)});`);
  const provider=fileURLToPath(new URL('../provider.mjs',import.meta.url));
  const child=spawn(process.execPath,['--import',register,provider,config],
    {stdio:['pipe','pipe','ignore','pipe'],env:{...process.env,ANYMSG_WHATSAPP_SEND_FD:'3'}});
  let counter=0;
  async function call(method,params={},sending=false){
    const stream=sending?child.stdio[3]:child.stdout,writer=sending?child.stdio[3]:child.stdin;
    const response=new Promise((resolve,reject)=>{
      let pending='';
      const clean=()=>{stream.off('data',receive);stream.off('error',failed);stream.off('end',ended);};
      const failed=error=>{clean();reject(error);};
      const ended=()=>{clean();reject(new Error('private channel closed before response'));};
      const receive=chunk=>{
        pending+=chunk.toString();
        if(pending.includes('\n')){clean();resolve(pending.slice(0,pending.indexOf('\n')));}
      };
      stream.on('data',receive);stream.once('error',failed);stream.once('end',ended);
    });
    writer.write(JSON.stringify({id:++counter,method,params})+'\n');
    return JSON.parse(await response);
  }
  async function close(){
    if(child.exitCode===null){const exited=once(child,'exit');child.kill('SIGTERM');await exited;}
    fs.rmSync(dir,{recursive:true,force:true});
  }
  return {dir,events,session,child,call,close};
}

test('media total deadline stops continuous trickling data and releases request response and timer',{timeout:3000},async t=>{
  const originalGet=https.get;
  t.mock.timers.enable({apis:['setTimeout']});
  const response=new PassThrough();response.statusCode=200;response.headers={};
  const request=new EventEmitter();let calls=0,destroys=0,finished=false,outcome;
  request.destroy=error=>{
    destroys++;
    if(error){response.destroy(error);request.emit('error',error);}
    return request;
  };
  https.get=(_url,options)=>{
    calls++;assert.equal(options.timeout,60000);
    queueMicrotask(()=>request.emit('response',response));return request;
  };
  const metadata={type:'document',directPath:'/v/synthetic.enc',mediaKey:Buffer.alloc(32,7).toString('base64'),
    filehash:Buffer.alloc(32).toString('base64'),encFilehash:Buffer.alloc(32).toString('base64'),size:1024};
  const transfer=(async()=>{for await(const _chunk of download(metadata,2048)){};})()
    .then(()=>{finished=true;outcome='completed';},error=>{finished=true;outcome=error.message;});
  try{
    await new Promise(setImmediate);
    for(let seconds=10;seconds<=50;seconds+=10){
      response.write(Buffer.alloc(1,seconds));
      t.mock.timers.tick(10000);await new Promise(setImmediate);
      assert.equal(finished,false);  // Active bytes did not hit an idle timeout.
    }
    response.write(Buffer.from([1]));t.mock.timers.tick(10000);await new Promise(setImmediate);
    assert.equal(finished,true,'an absolute deadline must expire even while new bytes keep arriving');
    assert.equal(outcome,'media_timeout');assert.equal(calls,1);
    assert.ok(response.destroyed);assert.ok(destroys>=1);
    const previous=destroys;t.mock.timers.tick(60000);assert.equal(destroys,previous,'deadline was cleared after teardown');
  }finally{
    https.get=originalGet;response.destroy(new Error('synthetic cleanup'));
    await transfer;t.mock.timers.reset();
  }
});

test('actual provider entrypoint pins identity and captures live edit global-delete and receipt callbacks offline',{timeout:15000},async()=>{
  const f=await providerFixture();
  try{
    assert.equal((await f.call('send_text',{chat:'123@c.us',text:'events'},true)).error,'provider_not_ready');
    assert.deepEqual((await f.call('start',{policy})).result,{ready:true});
    assert.equal(fs.statSync(f.events).mode&0o777,0o700);
    const options=JSON.parse(fs.readFileSync(path.join(f.session,'observed-options.json')));
    assert.equal(options.takeoverOnConflict,false);assert.equal(options.qrMaxRetries,1);
    assert.ok(!options.puppeteer.args.includes('--no-sandbox'));
    assert.equal(fs.statSync(path.join(f.session,'observed-options.json')).mode&0o777,0o600);
    const sent=await f.call('send_text',{chat:'123@c.us',text:'events',expires_at:Date.now()/1000+60},true);
    assert.equal(sent.result.provider_message_id,'true_123@c.us_8');
    const values=fs.readdirSync(f.events).filter(x=>x.endsWith('.json')).map(name=>{
      const bytes=fs.readFileSync(path.join(f.events,name));
      assert.equal(name.slice(21,-5),createHash('sha256').update(bytes).digest('hex'));
      assert.equal(fs.statSync(path.join(f.events,name)).mode&0o777,0o600);
      return JSON.parse(bytes);
    });
    assert.equal(values.length,5);assert.ok(!values.some(x=>x.event?.messageId==='false_123@c.us_0'));
    const live=values.find(x=>x.event?.messageId==='false_123@c.us_1');
    assert.equal(live.origin,'live');assert.ok(Number.isFinite(Date.parse(live.observed_at)));
    assert.equal(live.event.replyToId,'reply');assert.equal(live.event.replyToOwner,true);assert.equal(live.event.mentionsOwner,true);
    assert.equal(live.event.forwarded,true);assert.equal(live.event.mediaKind,'voice');assert.equal(live.event.filesize,17);
    assert.equal(values.find(x=>x.change==='edit').event.body,'_edited_');
    assert.deepEqual(values.find(x=>x.kind==='whatsapp_delete').ids,['false_123@c.us_3']);
    assert.equal(values.find(x=>x.kind==='message_receipt').state,'read');
    assert.ok(!values.some(x=>x.event?.messageId==='false_123@c.us_4'));
    assert.deepEqual(JSON.parse(fs.readFileSync(path.join(f.session,'send-options.json'))),
      {sendSeen:false,linkPreview:false,waitUntilMsgSent:true,ignoreQuoteErrors:false});
  }finally{await f.close();}
  const wrong=await providerFixture({wrongIdentity:true});
  try{
    assert.equal((await wrong.call('start',{policy})).error,'whatsapp_identity_mismatch');
    assert.deepEqual(fs.readdirSync(wrong.events).filter(x=>x.endsWith('.json')),[]);
    assert.equal(JSON.parse(fs.readFileSync(path.join(wrong.events,'.transport-failure'))).code,'whatsapp_identity_mismatch');
  }finally{await wrong.close();}
});

test('actual provider stops and records a safe failure when a live capture cannot reserve disk',{timeout:15000},async()=>{
  const f=await providerFixture({reserveBytes:Number.MAX_SAFE_INTEGER});
  try{
    await f.call('start',{policy});
    const exited=once(f.child,'exit');
    try{await f.call('send_text',{chat:'123@c.us',text:'events',expires_at:Date.now()/1000+60},true);}
    catch(error){assert.match(error.message,/private channel closed before response/);}
    assert.equal((await exited)[0],1);
    assert.equal(JSON.parse(fs.readFileSync(path.join(f.events,'.transport-failure'))).code,'disk_reserve');
    assert.deepEqual(fs.readdirSync(f.events).filter(x=>x.endsWith('.json')),[]);
  }finally{await f.close();}
});

test('actual provider page bounds trim oldest suffix without claiming exhaustion or losing cursor progress',{timeout:15000},async()=>{
  const f=await providerFixture();
  try{
    await f.call('start',{policy});
    const page=(await f.call('page',{chat:'123@c.us',limit:150})).result;
    assert.ok(page.messages.length<=100 && page.messages.length>0);
    assert.ok(Buffer.byteLength(JSON.stringify(page.messages))<=900*1024);
    assert.equal(page.exhausted,false);assert.equal(page.messages.at(-1).messageId,'false_123@c.us_150');
    const first=page.messages[0],before=[first.timestamp,first.messageId];
    const older=(await f.call('page',{chat:'123@c.us',limit:100,before})).result;
    assert.ok(older.messages.length>0 && older.messages.every(x=>x.timestamp<before[0]));
    assert.equal(older.messages.at(-1).timestamp,before[0]-1);
    assert.equal(page.messages[0].formatting.state,'wire_observed');
    assert.equal(page.messages[0].formatting.entities,null);
  }finally{await f.close();}
});
