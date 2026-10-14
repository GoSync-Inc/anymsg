// Self-contained browser function; local cached observations, no network or read action.
export function cachedDialogs({limit,ownerIds}) {
  function personalId(id) {
    const match=typeof id==='string' && /^(\d+)(?::\d+)?@(c\.us|lid)$/.exec(id);
    return match?match[1]+'@'+match[2]:null;
  }
  const own=new Set(ownerIds.map(personalId).filter(Boolean));
  return window.require('WAWebCollections').Chat.getModelsArray()
    .filter(c=>/^[a-zA-Z0-9_.:-]{1,140}@(c.us|g.us|lid)$/.test(c.id?._serialized || ''))
    .slice(0,limit).map(c=>{
      const peer=personalId(c.id._serialized);
      const self=c.id.server==='g.us'?false:peer && own.size?own.has(peer)?true:
        [...own].some(me=>me.split('@')[1]===peer.split('@')[1])?false:null:null;
      return [c.id._serialized,c.id.server==='g.us'?'chat':'user',c.formattedTitle || c.name || c.id._serialized,
        c.id.server==='g.us'?'group':'private',{observed_at:new Date().toISOString(),
          unread_count:Number.isSafeInteger(c.unreadCount) && c.unreadCount>=0?c.unreadCount:null,
          basis:'whatsapp_cached_chat',is_self_chat:self}];
    });
}
