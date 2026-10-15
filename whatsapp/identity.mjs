// Cache-only counterpart of WEBJS 1.34.7 enforceLidAndPnRetrieval. Unlike the
// public getContactLidAndPhone API, this never invokes queryWidExists.
export function cachedIdentityObservations(ids) {
  const result=[];
  try {
    const factory=window.require('WAWebWidFactory');
    const contacts=window.require('WAWebApiContact');
    for(const identity_id of new Set(ids.slice(0,200))) {
      if(typeof identity_id!=='string' || !/^\d{1,20}(?::\d{1,5})?@(c\.us|lid)$/.test(identity_id))continue;
      try {
        const wid=factory.createWid(identity_id.replace(/:\d+(?=@)/,''));
        const pn=wid.server==='lid'?contacts.getPhoneNumber(wid):wid;
        const lid=wid.server==='lid'?wid:contacts.getCurrentLid(wid);
        if(!/^\d{1,20}@c\.us$/.test(pn?._serialized || '') || !/^\d{1,20}@lid$/.test(lid?._serialized || ''))continue;
        result.push({identity_id,pn:pn._serialized,lid:lid._serialized,
          provenance:'webjs_contact_cache_v1',observed_at:new Date().toISOString()});
      }catch{} // Absence of a mapping is not a message or transport failure.
    }
  }catch{}
  return result;
}

export class IdentityCache {
  constructor(maxEntries=10000){this.values=new Map();this.maxEntries=maxEntries;}
  remember(observations){
    for(const value of observations){
      this.values.delete(value.identity_id);this.values.set(value.identity_id,value);
      while(this.values.size>this.maxEntries)this.values.delete(this.values.keys().next().value);
    }
  }
  forEvent(message){
    return [...new Set([message.senderId,message.chatId])].map(id=>this.values.get(id)).filter(Boolean);
  }
}

// Receipt verification must check both cache directions. One observation, equal
// digits or a network lookup is not proof that the requested PN is this LID.
export function cachedBidirectionalPair({pn,lid}) {
  if(!/^\d{1,20}@c\.us$/.test(pn || '') || !/^\d{1,20}@lid$/.test(lid || ''))return false;
  try {
    const factory=window.require('WAWebWidFactory'),contacts=window.require('WAWebApiContact');
    return contacts.getCurrentLid(factory.createWid(pn))?._serialized===lid &&
      contacts.getPhoneNumber(factory.createWid(lid))?._serialized===pn;
  }catch{return false;}
}
