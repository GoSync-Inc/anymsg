"""Owner-read snapshots and credential-local dismissals, no provider actions."""
from datetime import datetime, timezone

from .envelope import stable_id, utc
from .policy import scope_filter, targets


def validate_observation(value):
    if not isinstance(value, dict) or set(value)-{'observed_at','unread_count','read_inbox_max_id','basis','is_self_chat'}:
        raise ValueError('invalid_read_observation')
    if value.get('basis') not in ('telegram_dialog','whatsapp_cached_chat'):
        raise ValueError('invalid_read_observation')
    at=utc(value.get('observed_at'))
    if datetime.fromisoformat(at)>datetime.now(timezone.utc):
        raise ValueError('invalid_read_observation')
    for key, maximum in [('unread_count',2**31-1),('read_inbox_max_id',2**63-1)]:
        number=value.get(key)
        if number is not None and (type(number) is not int or not 0<=number<=maximum):
            raise ValueError('invalid_read_observation')
    if value.get('is_self_chat') is not None and type(value['is_self_chat']) is not bool:
        raise ValueError('invalid_read_observation')
    if value['basis']=='whatsapp_cached_chat' and value.get('read_inbox_max_id') is not None:
        raise ValueError('invalid_read_observation')
    return {**value,'observed_at':at}


def telegram_read_capture(account,update,*,verified_self,expected_user_id):
    """Observe inbox read boundaries from the verified existing client, no RPC."""
    import re
    from telethon import types,utils
    if (not isinstance(account,str) or not re.fullmatch(r'[a-zA-Z0-9_.-]{1,64}',account)
        or type(expected_user_id) is not int or not 0<expected_user_id<2**63
        or not isinstance(verified_self,types.User) or verified_self.is_self is not True
        or verified_self.id!=expected_user_id):return None
    if isinstance(update,types.UpdateReadHistoryInbox):peer=update.peer
    elif isinstance(update,types.UpdateReadChannelInbox):peer=types.PeerChannel(update.channel_id)
    else:return None
    if not isinstance(peer,(types.PeerUser,types.PeerChat,types.PeerChannel)):return None
    peer_id=getattr(peer,'user_id',getattr(peer,'chat_id',getattr(peer,'channel_id',None)))
    if type(peer_id) is not int or not 0<peer_id<2**63:return None
    try:
        observed=validate_observation({'observed_at':datetime.now(timezone.utc).isoformat(),
            'read_inbox_max_id':update.max_id,'unread_count':update.still_unread_count,
            'basis':'telegram_dialog','is_self_chat':isinstance(peer,types.PeerUser) and peer_id==expected_user_id})
    except (ValueError,AttributeError):return None
    return {'kind':'owner_read_state','account':account,'chat':str(utils.get_peer_id(peer)),
        'observed_at':observed['observed_at'],'observation':observed}


def observe(c,account,provider_chat,value):
    value=validate_observation(value)
    platform=c.execute('SELECT platform FROM accounts WHERE id=%s',(account,)).fetchone()
    if not platform or value['basis'].split('_',1)[0]!=platform['platform']:
        raise ValueError('invalid_read_observation')
    c.execute('''INSERT INTO chat_read_observations(chat,account,observed_at,unread_count,read_inbox_max_id,basis,is_self_chat)
        VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(chat) DO UPDATE SET
        observed_at=excluded.observed_at,unread_count=excluded.unread_count,
        read_inbox_max_id=excluded.read_inbox_max_id,basis=excluded.basis,
        is_self_chat=coalesce(excluded.is_self_chat,chat_read_observations.is_self_chat)
        WHERE excluded.observed_at>chat_read_observations.observed_at''',
        (stable_id('chat',account,provider_chat),account,value['observed_at'],value.get('unread_count'),
         value.get('read_inbox_max_id'),value['basis'],value.get('is_self_chat')))


def dismiss(store,c,p,chat_id,message_id,expected_revision):
    from .chat_queries import list_chats
    from .search_query import valid_text
    from .store import APIError
    valid_text(chat_id,empty=False);valid_text(message_id,empty=False)
    if type(expected_revision) is not int or expected_revision<1:
        raise APIError('invalid_version')
    if 'read' not in p['permissions']:raise APIError('forbidden',403)
    scope,args=scope_filter(p,'c',chat_column='id')
    # Share the archive's account lock: ingest cannot change the target between
    # the compare and write. Separate credentials cannot dismiss one another.
    chat=c.execute('SELECT c.account FROM chats c WHERE c.id=%s AND '+scope,[chat_id,*args]).fetchone()
    if not chat:raise APIError('not_found',404)
    c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('archive:'+chat['account'],))
    result=list_chats(store,c,p,include_coverage=False,awaiting_reply_days=0,selected_chat=chat_id,
        include_dismissed=True)
    row=result['items'][0]
    if (not row['awaiting_reply'] or row.get('awaiting_reply_message_id')!=message_id or
        row.get('awaiting_reply_revision')!=expected_revision):
        raise APIError('reply_target_changed',409)
    c.execute('''INSERT INTO reply_dismissals(key_id,chat,message,revision,incoming_head)
        VALUES(%s,%s,%s,%s,%s) ON CONFLICT(key_id,chat) DO UPDATE SET
        message=excluded.message,revision=excluded.revision,incoming_head=excluded.incoming_head,
        dismissed_at=clock_timestamp()''',
        (p['key_id'],chat_id,message_id,expected_revision,row['_incoming_head']))
    targets(p,[chat_id])
    return {'dismissed':True,'chat_id':chat_id,'message_id':message_id,'revision':expected_revision,
        'scope':'this_credential','until':'next_meaningful_incoming_or_attention_revision',
        'provider_read_changed':False}
