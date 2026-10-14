"""Pure normalizers for existing provider clients; these do not open sessions.

Call the durable spool before acknowledging an adapter capture. Live event wiring,
provider catch-up and single-session handover are separate deployment gates.
"""
from datetime import datetime,timezone
import re

from .envelope import validate
from .incoming_formatting import telegram as telegram_formatting, unknown as unknown_formatting


def timestamp(value):
    if isinstance(value,datetime): return value.isoformat()
    if isinstance(value,(int,float)) and not isinstance(value,bool):
        return datetime.fromtimestamp(value,timezone.utc).isoformat()
    return value


def telegram_message(account,message,*,chat_title='',origin='live',verified_self=None):
    """Telethon Message-shaped object. No entity/network lookups during capture."""
    media=[]
    file=getattr(message,'file',None)
    document=getattr(message,'document',None); photo=getattr(message,'photo',None)
    provider_media=document or photo
    if document is not None and photo is not None:
        # WebPage can expose both. Message.file prefers its photo, while this
        # archive selects the document; derive size/type/name from that object.
        from telethon.tl.custom.file import File
        file=File(provider_media)
    if file and provider_media:
        media.append({'provider_id':str(provider_media.id),'filename':file.name or f'{message.id}{file.ext or ".bin"}',
                      'mime':file.mime_type or 'application/octet-stream','bytes':file.size})
    entity=getattr(message,'sender',None)  # Telethon's cached entity; never get_sender()/network here.
    # NewMessage can belong to a chat absent from the last dialog discovery.
    # Use only the entity already attached to this message, and verify its peer.
    from telethon import types,utils
    sender_id=message.sender_id
    identity_basis=None
    # UpdateShortSentMessage has no from_id. The provider supplies only the
    # self entity it has already authenticated against the configured owner ID.
    # A missing group/channel sender may be anonymous: never infer it there.
    if isinstance(verified_self,types.User) and verified_self.is_self:
        if not sender_id and message.out and isinstance(getattr(message,'peer_id',None),types.PeerUser):
            sender_id=verified_self.id
            identity_basis='authenticated_self'
        if sender_id==verified_self.id and entity is None:
            entity=verified_self
    chat_entity=getattr(message,'chat',None)
    observed_type='private' if isinstance(getattr(message,'peer_id',None),types.PeerUser) else None
    observed_title=''
    if isinstance(chat_entity,(types.User,types.Chat,types.ChatForbidden,types.Channel,types.ChannelForbidden)) and utils.get_peer_id(chat_entity)==message.chat_id:
        if isinstance(chat_entity,types.User):
            observed_type='private'
            observed_title=' '.join(x for x in (chat_entity.first_name,chat_entity.last_name) if isinstance(x,str))
        else:
            observed_title=chat_entity.title or ''
            if isinstance(chat_entity,(types.Chat,types.ChatForbidden)):observed_type='group'
            elif getattr(chat_entity,'megagroup',False):observed_type='group'
            elif getattr(chat_entity,'broadcast',False):observed_type='channel'
    name=' '.join(x for x in [getattr(entity,'first_name',None),getattr(entity,'last_name',None)] if isinstance(x,str))
    profile={'display_name':(name or getattr(entity,'title','') or '')[:512],
             'username':(getattr(entity,'username','') or '')[:128]}
    reply=getattr(message,'reply_to',None)
    reply_id=str(getattr(reply,'reply_to_msg_id','') or '') or None
    # Replies may explicitly point into a different Telegram peer namespace.
    # Never resolve that ID against a coincidentally numbered local message.
    reply_peer=getattr(reply,'reply_to_peer_id',None)
    if reply_peer is not None:
        from telethon.utils import get_peer_id
        if get_peer_id(reply_peer)!=message.chat_id:reply_id=None
    return validate({'account':account,'platform':'telegram','chat':str(message.chat_id),
        'chat_title':(chat_title if chat_title and chat_title!=str(message.chat_id) else observed_title) or str(message.chat_id),
        'provider_id':str(message.id),
        'sender':str(sender_id or ''),'body':message.message or '',
        'formatting':telegram_formatting(message.message or '',getattr(message,'entities',None)),
        'sent_at':timestamp(message.date),'updated_at':timestamp(message.edit_date or message.date),
        'deleted':False,'direction':'outgoing' if message.out else 'incoming','origin':origin,'media':media,
        'sender_profile':profile,'metadata':{
            'chat_type':observed_type,
            'sender_identity_basis':identity_basis,
            'sender_is_bot':getattr(entity,'bot',None) if isinstance(entity,types.User) else None,
            'reply_to_provider_id':reply_id,
            'mentions_owner':getattr(message,'mentioned',None),
            'forwarded':getattr(message,'fwd_from',None) is not None,
            'media_kind':next((name for attr,name in [('voice','voice'),('video_note','video'),('sticker','sticker'),
                ('gif','animation'),('video','video'),('audio','audio'),('photo','photo'),('document','document')]
                if getattr(message,attr,None)),None)}})


def whatsapp_message(account,event,*,origin='live',change='message',observed_at=None):
    """Existing bridge event shape; edit/delete capture must supply its saved observation time.

    The legacy RAM poll is not a durable source. Server callbacks write a spool.
    """
    if change not in ('message','edit','delete'): raise ValueError('invalid_change')
    if change!='message' and observed_at is None: raise ValueError('missing_change_time')
    media=[]
    if event.get('hasMedia') and change!='delete':
        mime=event.get('mimetype') or 'application/octet-stream'
        if not isinstance(mime,str) or re.search(r'[\x00-\x1f\x7f]',mime):raise ValueError('invalid_mime')
        mime=mime.split(';',1)[0].strip().lower()
        media.append({'provider_id':str(event['messageId']),
                      'filename':event.get('filename') or str(event['messageId'])[:120]+'.bin',
                      'mime':mime,'bytes':event.get('filesize')})
    edited=event.get('editedTimestampMs')
    updated=(edited/1000 if type(edited) is int and edited>0 else None)
    if change=='delete' or (change=='edit' and updated is None): updated=observed_at
    return validate({'account':account,'platform':'whatsapp','chat':event['chatId'],
        'chat_title':event.get('chatName') or event['chatId'],'provider_id':event['messageId'],
        'sender':event.get('senderId') or '','body':event.get('body') or '',
        'formatting':event.get('formatting',unknown_formatting()),
        'sent_at':timestamp(event['timestamp']),'updated_at':timestamp(updated or event['timestamp']),
        'deleted':change=='delete','direction':'outgoing' if event.get('fromMe') else 'incoming',
        'origin':origin,'media':media,'sender_profile':{'display_name':event.get('senderName') or '',
                                                     'username':event.get('senderUsername') or ''},
        'metadata':{'reply_to_provider_id':event.get('replyToId'),
                    'mentions_owner':event.get('mentionsOwner'),
                    'reply_to_owner':event.get('replyToOwner'),
                    'forwarded':event.get('forwarded'),
                    'media_kind':event.get('mediaKind')}})
