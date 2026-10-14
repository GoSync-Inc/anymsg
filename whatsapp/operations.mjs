// Explicit operations in the existing authenticated client. Never raw RPC/evaluate.
// A submitted mutation is NOT a confirmed effect and must never be retried blindly.
import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {allows,asId,chatId} from './capture.mjs';

const CHAT=/^[a-zA-Z0-9_.:-]{1,140}@(c\.us|g\.us|lid)$/;
const USER=/^\d{1,30}@(c\.us|lid)$/;
const MAX_FILE=32*1024**2;
const MAX_RESULT=64*1024;
const SPECS={
  chat_info:[],list_participants:['offset','limit'],get_reactions:['offset','limit'],get_message_info:[],
  get_profile:[],list_contacts:['offset','limit'],get_contact:['user_id'],list_blocked:['offset','limit'],
  get_pinned_messages:['offset','limit'],send_file:['attachment_id','sha256','mode','caption'],
  forward_message:['source_message_id','source_revision'],send_poll:['question','options','multiple'],
  send_contact:['contact_ids'],send_location:['latitude','longitude','description'],edit_message:['text'],
  delete_message:['revoke'],send_reaction:['emoji'],pin_message:['seconds'],unpin_message:[],
  mark_read:[],mark_unread:[],archive_chat:[],unarchive_chat:[],mute_chat:['seconds'],unmute_chat:[],
  pin_chat:[],unpin_chat:[],star_message:[],unstar_message:[],create_group:['title','user_ids'],
  rename_group:['title'],set_group_description:['description'],add_participant:['user_id'],
  remove_participant:['user_id'],promote_participant:['user_id'],demote_participant:['user_id'],
  leave_group:[],get_invite_link:[],revoke_invite_link:[],add_contact:['phone','first_name','last_name'],
  delete_contact:['user_id'],block_contact:['user_id'],unblock_contact:['user_id'],
  update_profile:['name','status'],get_labels:['offset','limit'],set_chat_labels:['label_ids'],
};
const GROUP=new Set(['list_participants','rename_group','set_group_description','add_participant',
  'remove_participant','promote_participant','demote_participant','leave_group','get_invite_link','revoke_invite_link']);
const GLOBAL=new Set(['get_profile','list_contacts','list_blocked','get_contact','block_contact','unblock_contact',
  'delete_contact','create_group','add_contact','update_profile','get_labels']);
const MESSAGE=new Set(['get_reactions','get_message_info','edit_message','send_reaction','pin_message',
  'unpin_message','star_message','unstar_message']);
const READ=new Set(['chat_info','list_participants','get_reactions','get_message_info','get_profile',
  'list_contacts','get_contact','list_blocked','get_pinned_messages','get_labels']);
const safeString=(value,max=512)=>typeof value==='string'?value.slice(0,max):null;
const safeBool=value=>typeof value==='boolean'?value:null;
const safeNumber=value=>typeof value==='number' && Number.isFinite(value) && value>=0?value:null;
const idOf=value=>{try{return asId(value);}catch{return null;}};
const cleanId=value=>{const id=idOf(value);return id && CHAT.test(id)?id:null;};
function invalid(code='operation_invalid_parameters'){throw new Error(code);}
function string(value,max,{empty=false}={}) {
  if(typeof value!=='string' || value.length>max || !value.isWellFormed() ||
    /[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/.test(value) || (!empty && !value.trim()))invalid();
  return value;
}
function user(value){if(typeof value!=='string' || !USER.test(value))invalid();return value;}
function users(values,max=100){
  if(!Array.isArray(values) || !values.length || values.length>max)invalid();
  const result=values.map(user);if(new Set(result).size!==result.length)invalid();return result;
}
function integer(value,min,max){if(!Number.isSafeInteger(value) || value<min || value>max)invalid();return value;}
function page(params,items){
  const offset=params.offset??0,limit=params.limit??50;
  integer(offset,0,100000);integer(limit,1,100);
  return {items:items.slice(offset,offset+limit),offset,limit,has_more:items.length>offset+limit,
    next_offset:items.length>offset+limit?offset+limit:null,coverage:'provider_observation'};
}
function contact(value){return {id:cleanId(value?.id),name:safeString(value?.name),
  push_name:safeString(value?.pushname),short_name:safeString(value?.shortName),
  is_business:safeBool(value?.isBusiness),is_my_contact:safeBool(value?.isMyContact),
  is_blocked:safeBool(value?.isBlocked),is_self:safeBool(value?.isMe)};}
function message(value){return {provider_message_id:idOf(value?.id),provider_chat:cleanId(value?.id?.remote) ||
  cleanId(value?.fromMe?value?.to:value?.from),body:safeString(value?.body,256),
  body_truncated:typeof value?.body==='string' && value.body.length>256,
  from_me:safeBool(value?.fromMe),timestamp:safeNumber(value?.timestamp),type:safeString(value?.type,64),
  has_media:safeBool(value?.hasMedia),is_starred:safeBool(value?.isStarred)};}
function output(outcome,fields={}){
  const result={outcome,data:{},...fields};
  if(Buffer.byteLength(JSON.stringify(result))>MAX_RESULT) {
    if(outcome==='confirmed')throw new Error('operation_result_too_large');
    return {outcome:'unknown',data:{code:'operation_result_too_large'}};
  }
  return result;
}

export const OPERATION_ERRORS=new Set(['operation_invalid_parameters','operation_disabled','operation_expired',
  'operation_target_invalid','operation_target_unavailable','operation_unsupported','operation_query_failed',
  'operation_result_too_large','registered_file_invalid']);

export async function executeOperation(client,job,currentPolicy,dependencies={}) {
  if(!job || typeof job!=='object' || !Object.hasOwn(SPECS,job.operation))invalid('operation_unsupported');
  const op=job.operation,p=job.parameters??{};
  if(!p || typeof p!=='object' || Array.isArray(p) || Object.keys(p).some(k=>!SPECS[op].includes(k)))invalid();
  if(p.offset!==undefined)integer(p.offset,0,100000);
  if(p.limit!==undefined)integer(p.limit,1,100);
  const target=job.provider_chat??'';
  if(target && (typeof target!=='string' || !CHAT.test(target)))invalid('operation_target_invalid');
  if(!GLOBAL.has(op) && !target)invalid('operation_target_invalid');
  if(GROUP.has(op) && !target.endsWith('@g.us'))invalid('operation_target_invalid');
  function authorize(extraChat='') {
    const policy=currentPolicy();
    if(!policy?.enabled || !Array.isArray(policy.operations_enabled) || !policy.operations_enabled.includes(op))
      invalid('operation_disabled');
    if(!Array.isArray(policy.include_chats) || !Array.isArray(policy.exclude_chats) ||
      (target && !allows(policy,target)) || (extraChat && !allows(policy,extraChat)))invalid('operation_target_invalid');
    if(!Number.isFinite(job.expires_at) || Date.now()/1000>=job.expires_at)invalid('operation_expired');
    return policy;
  }
  authorize();
  // Upstream delete(true) silently falls back to local deletion when revoke is
  // unavailable. Do not invoke that API without a separately validated contract.
  if(op==='delete_message')invalid('operation_unsupported');
  let mutating=false;
  async function mutate(fn,extraChat='') {
    authorize(extraChat);mutating=true;
    try{return await fn();}catch(error){
      if(error.before_provider_call)throw error;
      throw new Error('provider_operation_uncertain');
    }
  }
  function method(object,name) {
    if(typeof object?.[name]!=='function'){
      const error=new Error('operation_unsupported');error.before_provider_call=true;throw error;
    }
    return object[name].bind(object);
  }
  async function getChat() {
    const c=await method(client,'getChatById')(target);
    if(!c || cleanId(c.id)!==target || (GROUP.has(op) && c.isGroup!==true))invalid('operation_target_unavailable');
    return c;
  }
  function validMessageId(id,chat) {
    if(typeof id!=='string' || id.length>256 || !/^[a-zA-Z0-9_.:@-]+$/.test(id) ||
      (!id.startsWith(`true_${chat}_`) && !id.startsWith(`false_${chat}_`)))invalid('operation_target_invalid');
    return id;
  }
  async function getMessage(id=job.provider_message_id,chat=target) {
    validMessageId(id,chat);
    const m=await method(client,'getMessageById')(id);
    if(!m)invalid('operation_target_unavailable');
    let actual;try{actual=chatId(m);}catch{invalid('operation_target_invalid');}
    if(idOf(m.id)!==id || actual!==chat || m.type==='revoked')invalid('operation_target_invalid');
    return m;
  }
  async function getContact(id,{checkPolicy=true}={}) {
    user(id);authorize(checkPolicy?id:'');
    const c=await method(client,'getContactById')(id);
    if(!c || cleanId(c.id)!==id)invalid('operation_target_unavailable');
    return c;
  }
  async function library(){return dependencies.library || (await import('whatsapp-web.js')).default;}
  function confirmedMessage(m,expectedId=null,expectedText=null) {
    let actual;try{actual=chatId(m);}catch{return output('unknown',{data:{code:'operation_receipt_unverified'}});}
    const id=idOf(m?.id);
    if(!m?.fromMe || actual!==target || !id || !id.startsWith(`true_${target}_`) ||
      id.length>256 || !/^[a-zA-Z0-9_.:@-]+$/.test(id) || (expectedId && id!==expectedId) ||
      (expectedText!==null && m.body!==expectedText))return output('unknown',{data:{code:'operation_receipt_unverified'}});
    return output('confirmed',{provider_message_id:id});
  }
  function confirmedRead(data,extraChat='') {
    // A policy can change while an awaited provider read is in progress.
    // Recheck it before releasing that observation to the caller.
    authorize(extraChat);return output('confirmed',{data});
  }
  const sendOptions={sendSeen:false,linkPreview:false,waitUntilMsgSent:true,ignoreQuoteErrors:false};
  try {
    if(op==='chat_info'){
      const c=await getChat();return confirmedRead({id:target,name:safeString(c.name),
        is_group:safeBool(c.isGroup),is_read_only:safeBool(c.isReadOnly),is_locked:safeBool(c.isLocked),
        is_muted:safeBool(c.isMuted),mute_expiration:safeNumber(c.muteExpiration),
        is_archived:safeBool(c.archived),is_pinned:safeBool(c.pinned),unread_count:safeNumber(c.unreadCount),
        timestamp:safeNumber(c.timestamp),description:safeString(c.description,2048),
        owner_id:cleanId(c.owner),coverage:'provider_observation'});
    }
    if(op==='list_participants'){
      const c=await getChat(),values=Array.isArray(c.participants)?c.participants:[];
      return confirmedRead({...page(p,values.map(v=>({id:cleanId(v.id),
        is_admin:safeBool(v.isAdmin),is_owner:safeBool(v.isSuperAdmin)}))),
        state:Array.isArray(c.participants)?'observed':'unknown'});
    }
    if(op==='get_profile')return confirmedRead({id:cleanId(client.info?.wid),
      name:safeString(client.info?.pushname),platform:safeString(client.info?.platform,64),coverage:'cached_self'});
    if(op==='list_contacts' || op==='list_blocked') {
      const values=await method(client,op==='list_contacts'?'getContacts':'getBlockedContacts')(),policy=authorize();
      const allowed=Array.isArray(values)?values.filter(v=>cleanId(v?.id) && allows(policy,cleanId(v.id))):[];
      return confirmedRead({...page(p,allowed.map(contact)),state:Array.isArray(values)?'observed':'unknown'});
    }
    if(op==='get_contact')return confirmedRead(contact(await getContact(p.user_id)),p.user_id);
    if(op==='get_pinned_messages') {
      const values=await method(client,'getPinnedMessages')(target);
      const visible=(Array.isArray(values)?values:[]).filter(m=>{try{return chatId(m)===target;}catch{return false;}});
      return confirmedRead({...page(p,visible.map(message)),state:Array.isArray(values)?'observed':'unknown'});
    }
    if(op==='get_reactions') {
      const m=await getMessage(),values=await method(m,'getReactions')();const items=[];
      const self=cleanId(client.info?.wid);
      for(const group of Array.isArray(values)?values:[]) {
        for(const actor of Array.isArray(group.senders)?group.senders:[]) {
          const actorID=cleanId(actor.senderId);
          // A group's hasReactionByMe flag does not identify each listed actor.
          // Distinct LID/phone namespaces cannot be equated without alias proof.
          const isSelf=self && actorID && self.split('@')[1]===actorID.split('@')[1]?self===actorID:null;
          items.push({emoji:safeString(group.id,64),actor_id:actorID,
            timestamp:safeNumber(actor.timestamp),removed:actor.reaction==='',
            is_by_self:isSelf,group_has_reaction_by_self:safeBool(group.hasReactionByMe)});
        }
      }
      return confirmedRead({...page(p,items),state:Array.isArray(values)?'observed':'unknown'});
    }
    if(op==='get_message_info') {
      const m=await getMessage(),info=await method(m,'getInfo')();const data={state:info?'observed':'unknown'};
      for(const key of ['delivery','read','played']) {
        const values=Array.isArray(info?.[key])?info[key]:[];
        data[key]=values.slice(0,100).map(v=>({id:cleanId(v.id),timestamp:safeNumber(v.t)}));
        data[key+'_truncated']=values.length>100;
        data[key+'_remaining']=safeNumber(info?.[key+'Remaining']);
      }
      return confirmedRead(data);
    }
    if(op==='send_file') {
      if(!['document','photo','video','audio','voice','sticker'].includes(p.mode))invalid();
      const caption=string(p.caption??'',1024,{empty:true});
      const root=job.trusted_blob_directory,digest=job.file_digest,size=job.file_bytes,file=job.registered_file_path;
      if(typeof root!=='string' || !path.isAbsolute(root) || typeof file!=='string' ||
        typeof digest!=='string' || !/^[a-f0-9]{64}$/.test(digest) || !Number.isSafeInteger(size) ||
        size<1 || size>MAX_FILE || (p.sha256!==undefined && p.sha256!==digest) ||
        path.resolve(file)!==path.join(path.resolve(root),digest))invalid('registered_file_invalid');
      const mime=string(job.file_mime??'application/octet-stream',128);
      const name=string(job.file_name??`attachment-${digest.slice(0,12)}`,255);
      if(!/^[a-z0-9!#$&^_.+-]+\/[a-z0-9!#$&^_.+-]+$/i.test(mime) || /[\/\\]/.test(name))invalid('registered_file_invalid');
      const mimeFamily={photo:'image/',video:'video/',audio:'audio/',voice:'audio/',sticker:'image/'}[p.mode];
      if(mimeFamily && !mime.startsWith(mimeFamily))invalid('registered_file_invalid');
      let bytes,fd;
      try{
        if(fs.realpathSync(root)!==path.resolve(root) || !fs.lstatSync(root).isDirectory())invalid('registered_file_invalid');
        fd=fs.openSync(file,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW);
        const info=fs.fstatSync(fd);if(!info.isFile() || info.size!==size)invalid('registered_file_invalid');
        // Bounded fd reads prevent a concurrent growth from expanding a base64
        // buffer beyond the declared limit. Never follow a pathname after open.
        const bounded=Buffer.alloc(size+1);let received=0;
        while(received<bounded.length){const n=fs.readSync(fd,bounded,received,bounded.length-received,null);if(!n)break;received+=n;}
        bytes=bounded.subarray(0,received);
        if(bytes.length!==size || createHash('sha256').update(bytes).digest('hex')!==digest)invalid('registered_file_invalid');
      }catch{invalid('registered_file_invalid');}finally{if(fd!==undefined)fs.closeSync(fd);}
      const {MessageMedia}=await library();
      const media=new MessageMedia(mime,bytes.toString('base64'),name,size);bytes=undefined;
      const options={...sendOptions,caption,sendMediaAsDocument:p.mode==='document',
        sendAudioAsVoice:p.mode==='voice',sendMediaAsSticker:p.mode==='sticker'};
      return confirmedMessage(await mutate(()=>method(client,'sendMessage')(target,media,options)));
    }
    if(op==='send_poll') {
      const question=string(p.question,255),options=p.options;
      if(!Array.isArray(options) || options.length<2 || options.length>10 ||
        typeof p.multiple!=='boolean')invalid();
      const clean=options.map(v=>string(v,100));if(new Set(clean).size!==clean.length)invalid();
      const {Poll}=await library(),poll=new Poll(question,clean,{allowMultipleAnswers:p.multiple});
      return confirmedMessage(await mutate(()=>method(client,'sendMessage')(target,poll,sendOptions)));
    }
    if(op==='send_contact') {
      const ids=users(p.contact_ids,20),contacts=[];
      for(const id of ids)contacts.push(await getContact(id,{checkPolicy:false}));
      authorize();
      return confirmedMessage(await mutate(()=>method(client,'sendMessage')(target,contacts,sendOptions)));
    }
    if(op==='send_location') {
      if(typeof p.latitude!=='number' || !Number.isFinite(p.latitude) || p.latitude< -90 || p.latitude>90 ||
        typeof p.longitude!=='number' || !Number.isFinite(p.longitude) || p.longitude< -180 || p.longitude>180)invalid();
      const description=string(p.description??'',255,{empty:true}),{Location}=await library();
      const location=new Location(p.latitude,p.longitude,{name:description});
      return confirmedMessage(await mutate(()=>method(client,'sendMessage')(target,location,sendOptions)));
    }
    if(op==='forward_message') {
      if(typeof job.source_provider_chat!=='string' || !CHAT.test(job.source_provider_chat))invalid();
      authorize(job.source_provider_chat);const m=await getMessage(job.source_provider_message,job.source_provider_chat);
      await mutate(()=>method(m,'forward')(target),job.source_provider_chat);return output('submitted');
    }
    if(MESSAGE.has(op)) {
      if(op==='edit_message')string(p.text,4000);
      if(op==='send_reaction')string(p.emoji,64,{empty:true});
      if(op==='pin_message')integer(p.seconds??86400,1,2147483647);
      const m=await getMessage();
      if(op==='edit_message') {
        if(m.fromMe!==true)invalid('operation_target_invalid');
        const returned=await mutate(()=>method(m,'edit')(p.text,{linkPreview:false}));
        return returned?confirmedMessage(returned,job.provider_message_id,p.text):output('submitted');
      }
      if(op==='send_reaction')await mutate(()=>method(client,'sendReaction')(job.provider_message_id,p.emoji));
      else if(op==='pin_message')await mutate(()=>method(m,'pin')(p.seconds??86400));
      else await mutate(()=>method(m,({unpin_message:'unpin',star_message:'star',unstar_message:'unstar'})[op])());
      return output('submitted');
    }
    if(op==='mark_read') {
      await getMessage();await mutate(()=>method(client,'sendSeen')(target));
      return output('submitted',{data:{scope:'whole_chat'}});
    }
    if(op==='create_group') {
      string(p.title,128);const ids=users(p.user_ids,20);
      // Group administration must not implicitly send a private invitation when
      // WhatsApp declines adding a member. That is a separate unapproved send.
      const result=await mutate(()=>method(client,'createGroup')(p.title,ids,{autoSendInviteV4:false}));
      const created=cleanId(result?.gid);
      // Provider-created group may lie outside existing scope. Do not disclose its
      // identity or participants until the operator authorizes that new chat.
      return output('submitted',created && allows(authorize(),created)?{data:{provider_chat:created}}:{});
    }
    if(op==='add_contact') {
      if(typeof p.phone!=='string' || !/^\+?[1-9]\d{4,19}$/.test(p.phone))invalid();
      const phone=p.phone.replace(/^\+/,'');
      string(p.first_name,64);string(p.last_name??'',64,{empty:true});authorize(phone+'@c.us');
      // Upstream accepts the international phone without '+'. Do not opt into
      // synchronizing the contact to the owner's phone address book.
      await mutate(()=>method(client,'saveOrEditAddressbookContact')(phone,p.first_name,p.last_name??'',false),phone+'@c.us');
      return output('submitted');
    }
    if(op==='delete_contact') {
      user(p.user_id);if(!p.user_id.endsWith('@c.us'))invalid('operation_unsupported');authorize(p.user_id);
      await mutate(()=>method(client,'deleteAddressbookContact')(p.user_id.split('@')[0]),p.user_id);return output('submitted');
    }
    if(op==='block_contact' || op==='unblock_contact') {
      const c=await getContact(p.user_id);
      await mutate(()=>method(c,op==='block_contact'?'block':'unblock')(),p.user_id);return output('submitted');
    }
    if(op==='update_profile') {
      if(p.name===undefined && p.status===undefined)invalid();
      if(p.name!==undefined)string(p.name,64);
      if(p.status!==undefined)string(p.status,139,{empty:true});
      // Preflight every method before starting a possible two-field mutation.
      const name=p.name!==undefined?method(client,'setDisplayName'):null;
      const status=p.status!==undefined?method(client,'setStatus'):null;
      if(name)await mutate(()=>name(p.name));
      if(status)await mutate(()=>status(p.status));
      return output('submitted');
    }
    if(op==='get_labels' || op==='set_chat_labels') {
      const own=cleanId(client.info?.wid);if(!own)invalid('operation_unsupported');
      const self=await method(client,'getContactById')(own);if(self?.isBusiness!==true)invalid('operation_unsupported');
      if(op==='get_labels'){
        const values=await method(client,'getLabels')();
        return confirmedRead(page(p,(Array.isArray(values)?values:[]).map(v=>({
          id:typeof v.id==='number'?String(v.id):safeString(v.id,64),name:safeString(v.name,128),
          color:safeNumber(v.hexColor??v.color)}))));
      }
      if(!Array.isArray(p.label_ids) || p.label_ids.length>20 || p.label_ids.some(v=>
        typeof v!=='string' || !/^[a-zA-Z0-9_-]{1,64}$/.test(v)) || new Set(p.label_ids).size!==p.label_ids.length)invalid();
      const c=await getChat();await mutate(()=>method(c,'changeLabels')(p.label_ids));return output('submitted');
    }
    if(op==='mute_chat')integer(p.seconds??86400,1,2147483647);
    if(op==='rename_group')string(p.title,128);
    if(op==='set_group_description')string(p.description,255,{empty:true});
    if(['add_participant','remove_participant','promote_participant','demote_participant'].includes(op))user(p.user_id);
    const c=await getChat();
    if(op==='get_invite_link') {
      const code=await mutate(()=>method(c,'getInviteCode')());
      return typeof code==='string' && /^[a-zA-Z0-9_-]{1,128}$/.test(code)?
        output('submitted',{data:{invite_link:'https://chat.whatsapp.com/'+code}}):output('submitted');
    }
    const methodName={mark_unread:'markUnread',archive_chat:'archive',unarchive_chat:'unarchive',
      mute_chat:'mute',unmute_chat:'unmute',pin_chat:'pin',unpin_chat:'unpin',rename_group:'setSubject',
      set_group_description:'setDescription',add_participant:'addParticipants',remove_participant:'removeParticipants',
      promote_participant:'promoteParticipants',demote_participant:'demoteParticipants',leave_group:'leave',
      revoke_invite_link:'revokeInvite'}[op];
    if(!methodName)invalid('operation_unsupported');
    let args=[];
    if(op==='mute_chat')args=[new Date(Date.now()+(p.seconds??86400)*1000)];
    if(op==='rename_group')args=[p.title];
    if(op==='set_group_description')args=[p.description];
    if(['add_participant','remove_participant','promote_participant','demote_participant'].includes(op)) {
      args=[[p.user_id]];
      if(op==='add_participant')args.push({autoSendInviteV4:false});
    }
    await mutate(()=>method(c,methodName)(...args));return output('submitted');
  }catch(error) {
    if(mutating && !error.before_provider_call)return output('unknown',{data:{code:'provider_operation_uncertain'}});
    if(OPERATION_ERRORS.has(error.message))throw error;
    throw new Error(READ.has(op)?'operation_query_failed':'operation_target_unavailable');
  }
}
