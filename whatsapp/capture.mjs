// One Node writer per directory; Python only consumes atomically published files.
// No provider credentials, keys, direct paths or raw Message objects are persisted.
import fs from 'node:fs';
import path from 'node:path';
import {createHash, randomBytes} from 'node:crypto';

export function asId(id) {
  if (typeof id==='string') return id;
  if (id?._serialized) return id._serialized;
  if (id?.id && id?.remote && typeof id.fromMe==='boolean') {
    const remote=asId(id.remote);
    if (remote) return `${id.fromMe}_${remote}_${id.id}${id.participant?'_'+asId(id.participant):''}`;
  }
  throw new Error('provider_id_unavailable');
}
export function chatId(message) {
  const remote=message.id?.remote || message._data?.id?.remote;
  const value=remote ? asId(remote) : asId(message.fromMe ? message.to : message.from);
  if (!/^[a-zA-Z0-9_.:-]{1,140}@(c.us|g.us|lid)$/.test(value)) throw new Error('unsupported_chat');
  return value;
}
function personalId(value) {
  const id=typeof value==='string'?value:value?._serialized;
  const match=typeof id==='string' && /^(\d+)(?::\d+)?@(c\.us|lid)$/.exec(id);
  return match?match[1]+'@'+match[2]:null;
}
export function ownerAttention(message,ownerIds=[]) {
  const own=new Set(ownerIds.map(personalId).filter(Boolean));
  function isOwner(id) {
    const normalized=personalId(id);
    if(!normalized || !own.size)return null;
    if(own.has(normalized))return true;
    return [...own].some(me=>me.split('@')[1]===normalized.split('@')[1])?false:null;
  }
  const raw=message._data || message;
  const mentions=message.mentionedIds ?? raw.mentionedJidList;
  const hits=Array.isArray(mentions)?mentions.map(isOwner):null;
  return {mentionsOwner:hits===null?null:hits.includes(true)?true:hits.includes(null)?null:false,
    replyToOwner:typeof raw.quotedStanzaID==='string'?isOwner(raw.quotedParticipant):null};
}
export function event(message, body=undefined, ownerIds=[]) {
  const raw=message._data || message;
  if(raw.mimetype && (typeof raw.mimetype!=='string' || /[\x00-\x1f\x7f]/.test(raw.mimetype)))
    throw new Error('invalid_provider_mime');
  const mime=raw.mimetype ? raw.mimetype.split(';',1)[0].trim().toLowerCase() : null;
  const value={chatId:chatId(message),messageId:asId(message.id),
    chatName:chatId(message),senderId:message.author || message.from || '',
    senderName:typeof raw.notifyName==='string'?raw.notifyName.slice(0,512):'',
    body:body===undefined ? message.body || '' : body,timestamp:message.timestamp,
    fromMe:Boolean(message.fromMe),hasMedia:Boolean(message.hasMedia),
    filename:raw.filename || null,mimetype:mime,
    // Only cached metadata: never fetch a quoted message/contact during capture.
    replyToId:typeof raw.quotedStanzaID==='string'?raw.quotedStanzaID:null,
    ...ownerAttention(message,ownerIds),
    forwarded:typeof message.isForwarded==='boolean'?message.isForwarded:null,
    mediaKind:({image:'photo',video:'video',audio:'audio',ptt:'voice',document:'document',sticker:'sticker',gif:'animation'})[message.type] || (message.hasMedia?'other':null),
    filesize:Number.isSafeInteger(raw.size)?raw.size:null,
    editedTimestampMs:message.latestEditSenderTimestampMs || raw.latestEditSenderTimestampMs || null};
  if (!Number.isSafeInteger(value.timestamp) || value.timestamp<=0) throw new Error('invalid_provider_time');
  if (typeof value.senderId!=='string' || typeof value.body!=='string' || value.body.length>65536)
    throw new Error('invalid_provider_message');
  return value;
}
export function cursor(e) { return [e.timestamp,e.messageId]; }
export function compare(a,b) { return a[0]-b[0] || (a[1]<b[1]?-1:a[1]>b[1]?1:0); }
export function allows(policy,chat) {
  return !policy.exclude_chats.includes(chat) && (policy.discover_chats || policy.include_chats.includes(chat));
}
export function readPolicy(directory,now=Date.now()) {
  const fd=fs.openSync(path.join(directory,'.policy'),fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW);
  try {
    const info=fs.fstatSync(fd);
    if(!info.isFile() || info.size>1024*1024 || (info.mode&0o077))throw new Error('invalid_policy');
    const saved=JSON.parse(fs.readFileSync(fd,'utf8'));
    if(!Number.isFinite(saved.expires_at) || saved.expires_at*1000<now)throw new Error('policy_expired');
    const p=saved.policy;
    if(typeof p?.discover_chats!=='boolean' || !Array.isArray(p.include_chats) || !Array.isArray(p.exclude_chats))
      throw new Error('invalid_policy');
    return p;
  }finally{fs.closeSync(fd);}
}
export class DiskCapture {
  constructor(directory,{maxBytes=256*1024**2,maxEntries=10000,reserveBytes=1024**3}={}) {
    this.directory=directory; this.maxBytes=maxBytes; this.maxEntries=maxEntries; this.reserveBytes=reserveBytes;
    fs.mkdirSync(directory,{recursive:true,mode:0o700});
    if (fs.lstatSync(directory).isSymbolicLink()) throw new Error('spool_symlink');
    this.sequence=0n;
  }
  put(value) {
    const payload=Buffer.from(JSON.stringify(value));
    if (payload.length>512*1024) throw new Error('capture_too_large');
    let bytes=0,entries=0;
    for (const name of fs.readdirSync(this.directory)) {
      try {const info=fs.lstatSync(path.join(this.directory,name));
        if(info.isSymbolicLink()) throw new Error('spool_symlink');
        if(info.isFile()) bytes+=info.size;
        if(name.endsWith('.json')) entries++;
      } catch(e) {if(e.code!=='ENOENT') throw e;}
    }
    if(bytes+payload.length>this.maxBytes || entries>=this.maxEntries) throw new Error('spool_full');
    const disk=fs.statfsSync(this.directory);
    if(disk.bavail*disk.bsize-payload.length<this.reserveBytes) throw new Error('disk_reserve');
    const now=BigInt(Date.now())*1000000n;
    this.sequence=now>this.sequence ? now : this.sequence+1n;
    const digest=createHash('sha256').update(payload).digest('hex');
    const name=`${this.sequence.toString().padStart(20,'0')}-${digest}.json`;
    const tmp=path.join(this.directory,'.pending-'+randomBytes(12).toString('hex'));
    let fd;
    try {
      fd=fs.openSync(tmp,'wx',0o600); fs.writeFileSync(fd,payload); fs.fsyncSync(fd); fs.closeSync(fd);fd=undefined;
      fs.renameSync(tmp,path.join(this.directory,name));
      const dirfd=fs.openSync(this.directory,'r'); try {fs.fsyncSync(dirfd);} finally {fs.closeSync(dirfd);}
    } finally {if(fd!==undefined)fs.closeSync(fd); try{fs.unlinkSync(tmp);}catch(e){if(e.code!=='ENOENT')throw e;}}
    return name;
  }
}
