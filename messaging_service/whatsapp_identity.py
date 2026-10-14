"""Account-local, provider-cache namespace observations; names are never proof."""
import re

from .envelope import utc,stable_id
from .policy import scope_filter

PROVENANCE='webjs_contact_cache_v1'


def personal_id(value):
    match=re.fullmatch(r'([0-9]{1,20})(?::[0-9]{1,5})?@(c\.us|lid)',value) if isinstance(value,str) else None
    return match[1]+'@'+match[2] if match else None


def observations(value,envelope):
    if not isinstance(value,list) or len(value)>2:raise ValueError('invalid_identity_observations')
    result=[]
    for item in value:
        if not isinstance(item,dict) or set(item)!={'identity_id','pn','lid','provenance','observed_at'}:
            raise ValueError('invalid_identity_observation')
        identity=personal_id(item['identity_id'])
        if envelope['platform']!='whatsapp' or item['identity_id'] not in (envelope['sender'],envelope['chat']):
            raise ValueError('identity_source_mismatch')
        if (not isinstance(item['pn'],str) or not isinstance(item['lid'],str) or
            personal_id(item['pn'])!=item['pn'] or not item['pn'].endswith('@c.us') or
            personal_id(item['lid'])!=item['lid'] or not item['lid'].endswith('@lid') or
            identity not in (item['pn'],item['lid']) or item['provenance']!=PROVENANCE):
            raise ValueError('invalid_identity_observation')
        result.append({**item,'observed_at':utc(item['observed_at'])})
    return result


def record(archive,envelope,values,lease):
    if not values:return
    with archive.store.pool.connection() as c:
        archive.fence(c,envelope['account'],lease)
        mid=stable_id('msg',envelope['account'],envelope['chat'],envelope['provider_id'])
        # The independently committed message may have changed since this capture.
        row=c.execute('SELECT m.account,m.sender,m.deleted,ch.provider_chat FROM messages m '
            'JOIN chats ch ON ch.id=m.chat WHERE m.id=%s',(mid,)).fetchone()
        if not row or row['deleted'] or row['account']!=envelope['account']:return
        if envelope['sender'] and any(v['identity_id']==row['sender'] for v in values):
            # Verified namespace data can provide a directory entry even when the
            # provider has no display name. Do not replace a name observation.
            c.execute('''INSERT INTO sender_profiles(id,account,chat,sender,source_message,source_sent_at)
                SELECT %s,m.account,m.chat,m.sender,m.id,m.sent_at FROM messages m WHERE m.id=%s
                ON CONFLICT(account,chat,sender) DO NOTHING''',
                (stable_id('person',envelope['account'],stable_id('chat',envelope['account'],envelope['chat']),row['sender']),mid))
        for item in values:
            if item['identity_id'] not in (row['sender'],row['provider_chat']):continue
            c.execute('''INSERT INTO whatsapp_identity_observations(account,source_message,identity_id,
                pn,lid,provenance,first_observed_at,observed_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(account,source_message,identity_id,pn,lid,provenance) DO UPDATE
                SET first_observed_at=least(whatsapp_identity_observations.first_observed_at,excluded.first_observed_at),
                    observed_at=greatest(whatsapp_identity_observations.observed_at,excluded.observed_at)''',
                (envelope['account'],mid,item['identity_id'],item['pn'],item['lid'],item['provenance'],
                 item['observed_at'],item['observed_at']))


def visible_cte(p):
    """Source message authorizes exposure. Conflicts anywhere in account block links.

    A deleted/restricted source cannot supply a phone number. A conflicting historic
    observation remains a safety fence; it is never replaced by the last writer.
    """
    where,args=scope_filter(p,'m')
    return '''visible_whatsapp_identity AS NOT MATERIALIZED (
        SELECT wi.*,m.chat, messenger_whatsapp_personal_id(wi.identity_id) AS normalized_id,
          NOT EXISTS(SELECT 1 FROM whatsapp_identity_observations other
            WHERE other.account=wi.account AND
              ((other.pn=wi.pn AND other.lid<>wi.lid) OR (other.lid=wi.lid AND other.pn<>wi.pn))) AS verified
        FROM whatsapp_identity_observations wi JOIN messages m ON m.id=wi.source_message
        JOIN chats ic ON ic.id=m.chat JOIN accounts ia ON ia.id=m.account
        WHERE wi.account=m.account AND ia.platform='whatsapp' AND NOT m.deleted
          AND (wi.identity_id=m.sender OR wi.identity_id=ic.provider_chat) AND '''+where+')',args


def people_join():
    return ''' LEFT JOIN LATERAL (
        SELECT jsonb_build_object('state',CASE WHEN v.verified THEN 'verified' ELSE 'conflict' END,
          'confidence','provider_cache_observation','historical_identity_verified',false,
          'pn',CASE WHEN v.verified THEN v.pn END,'lid',CASE WHEN v.verified THEN v.lid END,
          'provenance',v.provenance,'observed_at',v.observed_at,'source_message',v.source_message) AS metadata,
          CASE WHEN v.verified THEN lower(v.pn || ' ' || v.lid) ELSE '' END AS search_text
        FROM visible_whatsapp_identity v WHERE v.account=sp.account AND v.chat=sp.chat
          AND v.normalized_id=messenger_whatsapp_personal_id(sp.sender)
        ORDER BY v.observed_at DESC,v.source_message,v.pn,v.lid LIMIT 1
        ) identity ON true '''


def resolve_alias(c,p,account,provider_chat_id):
    """Return one visible original chat; never derive/replace an opaque message ID."""
    identity=personal_id(provider_chat_id)
    if not identity:return None
    cte,args=visible_cte(p);where,chat_args=scope_filter(p,'c',chat_column='id')
    rows=c.execute('WITH '+cte+''' SELECT DISTINCT c.id,c.account,c.title,c.provider_chat,a.platform
        FROM chats c JOIN accounts a ON a.id=c.account
        JOIN visible_whatsapp_identity v ON v.account=c.account AND v.verified
          AND %s IN (v.pn,v.lid) AND messenger_whatsapp_personal_id(c.provider_chat) IN (v.pn,v.lid)
        WHERE c.account=%s AND a.platform='whatsapp' AND '''+where+' ORDER BY c.id LIMIT 2',
        [*args,identity,account,*chat_args]).fetchall()
    if len(rows)>1:
        from .store import APIError
        raise APIError('ambiguous_reference',409)
    return rows[0] if rows else None
