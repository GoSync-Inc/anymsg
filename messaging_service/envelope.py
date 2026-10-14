"""Validated provider-independent input. No network or session ownership here."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from uuid import UUID, uuid5

NAMESPACE = UUID('5d833db7-c8ae-4c82-bfe3-e0e4d498d4da')
MAX_ENVELOPE = 512 * 1024


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def stable_id(kind, *parts):
    return kind + '_' + uuid5(NAMESPACE, canonical(parts).decode()).hex


def utc(value):
    if not isinstance(value, str): raise ValueError('invalid_timestamp')
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if dt.tzinfo is None: raise ValueError()
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError: raise ValueError('invalid_timestamp') from None


def text(value, maximum, name, empty=False):
    if not isinstance(value, str) or '\x00' in value or len(value)>maximum or (not empty and not value):
        raise ValueError('invalid_'+name)
    return value


def validate(data):
    if not isinstance(data, dict): raise ValueError('invalid_envelope')
    if len(canonical(data))>MAX_ENVELOPE: raise ValueError('envelope_too_large')
    allowed={'account','platform','chat','chat_title','provider_id','sender','body','sent_at',
             'updated_at','deleted','direction','origin','media','sender_profile','metadata'}
    if set(data)-allowed: raise ValueError('unknown_envelope_fields')
    result={k:text(data.get(k),n,k,empty=k in ('body','sender','chat_title')) for k,n in
            [('account',64),('chat',160),('chat_title',512),('provider_id',256),('sender',160),('body',65536)]}
    if not re.fullmatch(r'[a-zA-Z0-9_.-]+',result['account']): raise ValueError('invalid_account')
    for key, choices in [('platform',('telegram','whatsapp')),('direction',('incoming','outgoing')),
                         ('origin',('live','backfill','reconcile','fixture'))]:
        if data.get(key) not in choices: raise ValueError('invalid_'+key)
        result[key]=data[key]
    if type(data.get('deleted')) is not bool: raise ValueError('invalid_deleted')
    result['deleted']=data['deleted']
    result['sent_at']=utc(data.get('sent_at')); result['updated_at']=utc(data.get('updated_at'))
    if result['updated_at']<result['sent_at']: raise ValueError('invalid_update_time')
    media=data.get('media',[])
    if not isinstance(media,list) or len(media)>32: raise ValueError('invalid_media')
    result['media']=[]
    if 'sender_profile' in data:
        profile=data['sender_profile']
        if not isinstance(profile,dict) or set(profile)-{'display_name','username'}:
            raise ValueError('invalid_sender_profile')
        result['sender_profile']={key:text(profile.get(key,''),maximum,key,empty=True)
                                  for key,maximum in [('display_name',512),('username',128)]}
        result['sender_profile']['username']=result['sender_profile']['username'].lstrip('@')
    if 'metadata' in data:
        metadata=data['metadata']
        if not isinstance(metadata,dict) or set(metadata)-{'reply_to_provider_id','forwarded','media_kind','mentions_owner','reply_to_owner','sender_is_bot','chat_type'}:
            raise ValueError('invalid_metadata')
        result['metadata']={}
        for key,value in metadata.items():
            if key in ('forwarded','mentions_owner','reply_to_owner','sender_is_bot'):
                if value is not None and type(value) is not bool:raise ValueError('invalid_metadata')
            elif value is not None:
                text(value,256 if key=='reply_to_provider_id' else 32,key)
                if key=='media_kind' and value not in ('photo','video','audio','voice','document','sticker','animation','other'):
                    raise ValueError('invalid_metadata')
                if key=='chat_type' and value not in ('private','group','channel','unknown'):
                    raise ValueError('invalid_metadata')
            result['metadata'][key]=value
    seen=set()
    for item in media:
        if not isinstance(item,dict) or set(item)-{'provider_id','filename','mime','bytes'}:
            raise ValueError('invalid_media')
        mid=text(item.get('provider_id'),256,'media_id')
        if mid in seen: raise ValueError('duplicate_media_id')
        seen.add(mid)
        size=item.get('bytes')
        if size is not None and (type(size) is not int or not 0<=size<=8*1024**3):
            raise ValueError('invalid_media_size')
        # Display-only names never become filesystem paths or response header injection.
        name=text(item.get('filename'),255,'filename')
        name=re.sub(r'[\x00-\x1f\x7f/\\]', '_',name)
        mime=text(item.get('mime'),127,'mime')
        if not re.fullmatch(r'[\w.+-]+/[\w.+-]+',mime): raise ValueError('invalid_mime')
        result['media'].append({'provider_id':mid,'filename':name,'mime':mime,'bytes':size})
    if result['deleted']:
        result['body']=''; result['media']=[]
    return result


def fingerprint(data):
    # A retry/backfill observation does not itself create a new message revision.
    content={k:v for k,v in data.items() if k not in ('origin','updated_at','chat_title','sender_profile','metadata')}
    return hashlib.sha256(canonical(content)).hexdigest()
