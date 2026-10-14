// Serialized by Puppeteer into the existing page. Only the senders of the bounded
// message page are looked up in its already loaded cache; no contact-book fetch.
export function cachedSenderNames(ids) {
  const result={};
  try {
    const contacts=window.require('WAWebCollections').Contact;
    const getters=window.require('WAWebContactGetters');
    for(const id of new Set(ids.slice(0,100))) {
      if(typeof id!=='string' || !/^[a-zA-Z0-9_.:-]{1,140}@(c.us|lid)$/.test(id))continue;
      try {
        const contact=contacts.get(id);
        if(!contact)continue;
        const name=getters.getName(contact) || getters.getPushname(contact);
        if(typeof name==='string' && name && !name.includes('\0'))result[id]=name.slice(0,512);
      }catch{} // Missing optional metadata must not block message collection.
    }
  }catch{}
  return result;
}

// Only the authenticated account's two provider namespaces, from local cache.
// No contact/address-book fetch; absence leaves LID mentions unknown.
export function cachedOwnerIds() {
  try {
    const prefs=window.require('WAWebUserPrefsMeUser');
    return [prefs.getMaybeMePnUser?.(),prefs.getMaybeMeLidUser?.()]
      .map(wid=>wid?._serialized).filter(id=>typeof id==='string');
  }catch{return [];}
}
