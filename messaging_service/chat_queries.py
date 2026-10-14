"""Scoped chat catalogue with opt-in activity order and conservative hints."""
from .policy import scope_filter,targets
from .search_query import (coverage,cursor_binding,decode_cursor,encode_cursor,
    filter_values,valid_text)


def list_chats(store,c,p,after='',limit=100,*,accounts=None,query='',chat_types=None,
               order='id',exclude_channels=False,awaiting_reply=False,from_time='',to_time='',
               include_coverage=True,awaiting_reply_days=14):
    from .store import APIError,bounded,date_bound,serial
    bounded(limit)
    valid_text(after,4096);valid_text(query,200)
    if order=='last_activity_desc':order='activity'
    if order not in ('id','activity') or any(type(v) is not bool for v in
            (exclude_channels,awaiting_reply,include_coverage)):
        raise APIError('invalid_filter')
    if type(awaiting_reply_days) is not int or not 0<=awaiting_reply_days<=365:
        raise APIError('invalid_filter')
    if awaiting_reply and order=='id':order='activity'
    # These bounded catalogue lookups are latency-sensitive. PostgreSQL can
    # otherwise spend seconds compiling a repeatedly parameterized lateral plan.
    c.execute('SET LOCAL jit=off')
    accounts=filter_values(accounts);chat_types=filter_values(chat_types)
    if chat_types is not None and any(t not in ('private','group','channel','unknown') for t in chat_types):
        raise APIError('invalid_filter')
    lower,upper=date_bound(from_time),date_bound(to_time)
    if lower and upper and lower>=upper:raise APIError('invalid_date_range')
    scope,args=scope_filter(p,'c',chat_column='id');clauses=[scope]
    if accounts is not None:clauses.append('c.account=ANY(%s)');args.append(accounts)
    if query:
        clauses.append("c.title ILIKE %s ESCAPE E'\\\\'")
        args.append('%'+query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%')
    if chat_types is not None:
        clauses.append("coalesce(c.chat_type,'unknown')=ANY(%s)");args.append(chat_types)
    if exclude_channels:clauses.append("c.chat_type IS DISTINCT FROM 'channel'")
    # The validated integer is an internal SQL constant. A group's unrelated new
    # chatter must not revive an old owner mention. Unknown bot metadata stays unknown.
    cutoff=f"CURRENT_TIMESTAMP-interval '{awaiting_reply_days} days'"
    private_age='true' if not awaiting_reply_days else 'last.sent_at>='+cutoff
    group_age='true' if not awaiting_reply_days else 'attention.sent_at>='+cutoff
    private_pending="(c.chat_type='private' AND last.direction='incoming' AND NOT coalesce(last.sender_is_bot,false) AND "+private_age+')'
    pending='('+private_pending+" OR (c.chat_type='group' AND attention.id IS NOT NULL AND "+group_age+'))'
    if awaiting_reply:clauses.append(pending)
    for bound,op in [(lower,'>='),(upper,'<')]:
        if bound:clauses.append('last.sent_at'+op+'%s');args.append(bound)
    binding=cursor_binding(p,'chats',{'accounts':accounts,'query':query,'types':chat_types,
        'order':order,'exclude_channels':exclude_channels,'awaiting_reply':awaiting_reply,
        'awaiting_reply_days':awaiting_reply_days,
        'from':lower,'to':upper})
    # A group is personal attention only when provider metadata identifies the
    # owner or a quoted message belongs to the owner in this exact account/chat.
    # Other participants' chatter does not clear the hint. A later owner message
    # clears it heuristically; it is not evidence that a request was fulfilled.
    base=''' FROM chats c LEFT JOIN LATERAL (
        SELECT m.sent_at,m.id,m.direction,left(m.body,160) AS snippet,
            md.metadata->'sender_is_bot'='true'::jsonb AS sender_is_bot
        FROM messages m LEFT JOIN message_metadata md ON md.message=m.id AND md.revision=m.revision
        WHERE m.account=c.account AND m.chat=c.id AND NOT m.deleted
        ORDER BY m.sent_at DESC,m.id DESC LIMIT 1) last ON true
        LEFT JOIN LATERAL (
          SELECT own.sent_at FROM messages own
          WHERE c.chat_type='group' AND own.account=c.account AND own.chat=c.id
            AND NOT own.deleted AND own.direction='outgoing'
          ORDER BY own.sent_at DESC,own.id DESC LIMIT 1) own_last ON true
        LEFT JOIN LATERAL (
          SELECT m.id,m.provider_id,m.sent_at,left(m.body,160) AS snippet,
            CASE WHEN md.metadata->'mentions_owner'='true'::jsonb THEN 'owner_mention'
              ELSE 'reply_to_owner_message' END AS basis
          FROM messages m LEFT JOIN message_metadata md ON md.message=m.id AND md.revision=m.revision
          WHERE c.chat_type='group' AND m.account=c.account AND m.chat=c.id
            AND NOT m.deleted AND m.direction='incoming'
            AND m.sent_at>=coalesce(own_last.sent_at,'-infinity'::timestamptz)
            AND (md.metadata->'mentions_owner'='true'::jsonb
              OR md.metadata->'reply_to_owner'='true'::jsonb
              OR (own_last.sent_at IS NOT NULL AND md.metadata->>'reply_to_provider_id' IS NOT NULL AND (
                EXISTS(SELECT 1 FROM messages quoted
                WHERE quoted.account=m.account AND quoted.chat=m.chat AND NOT quoted.deleted
                  AND quoted.direction='outgoing' AND quoted.provider_id=md.metadata->>'reply_to_provider_id')
                OR (right(c.provider_chat,5)='@g.us' AND EXISTS(SELECT 1 FROM messages quoted
                  WHERE quoted.account=m.account AND quoted.chat=m.chat AND NOT quoted.deleted
                    AND quoted.direction='outgoing'
                    AND left(quoted.provider_id,length('true_'||c.provider_chat||'_'))='true_'||c.provider_chat||'_'
                      AND split_part(substr(quoted.provider_id,length('true_'||c.provider_chat||'_')+1),'_',1)
                        =md.metadata->>'reply_to_provider_id')))))
          ORDER BY m.sent_at DESC,m.id DESC LIMIT 1) attention ON true WHERE '''
    total=c.execute('SELECT count(*) AS n'+base+' AND '.join(clauses),args).fetchone()['n'] if awaiting_reply else None
    if after:
        if order=='id':
            valid_text(after)
            if not c.execute('SELECT 1 FROM chats c WHERE c.id=%s AND '+scope,
                [after,*scope_filter(p,'c',chat_column='id')[1]]).fetchone():raise APIError('not_found',404)
            clauses.append('c.id>%s');args.append(after)
        else:
            pos=decode_cursor(after,binding)
            if set(pos)!={'id','at'}:raise APIError('invalid_cursor')
            valid_text(pos['id'],empty=False)
            if not c.execute('SELECT 1 FROM chats c WHERE c.id=%s AND '+scope,
                [pos['id'],*scope_filter(p,'c',chat_column='id')[1]]).fetchone():raise APIError('not_found',404)
            at=None if pos['at'] is None else date_bound(pos['at'])
            clauses.append("(coalesce(last.sent_at,'-infinity'),c.id)<(coalesce(%s::timestamptz,'-infinity'),%s)")
            args.extend([at,pos['id']])
    sort='c.id' if order=='id' else "coalesce(last.sent_at,'-infinity') DESC,c.id DESC"
    rows=c.execute('''SELECT c.id,c.account,c.title,c.provider_chat AS provider_chat_id,c.chat_type AS type,last.sent_at AS last_activity,
        last.id AS last_message_id,last.direction AS last_message_direction,last.snippet AS last_message_snippet,
        NULL::boolean AS provider_unread,NULL::integer AS provider_unread_count,
        NULL::boolean AS muted,NULL::boolean AS archived,
        '''+pending+''' AS awaiting_reply,
        CASE WHEN '''+pending+''' THEN CASE WHEN c.chat_type='private' THEN 'last_archived_message_incoming'
          ELSE attention.basis END END AS awaiting_reply_basis,
        CASE WHEN '''+pending+''' THEN CASE WHEN c.chat_type='private' THEN last.id
          ELSE attention.id END END AS awaiting_reply_message_id,
        attention.provider_id AS awaiting_reply_provider_message_id,
        attention.sent_at AS awaiting_reply_sent_at,attention.snippet AS awaiting_reply_snippet,
        'heuristic_not_obligation' AS awaiting_reply_confidence'''+base+' AND '.join(clauses)+
        ' ORDER BY '+sort+' LIMIT %s',[*args,limit+1]).fetchall()
    items=rows[:limit];cursor=None
    if len(rows)>limit:
        last=items[-1];cursor=last['id'] if order=='id' else encode_cursor(binding,{'id':last['id'],'at':last['last_activity']})
    targets(p,[r['id'] for r in items])
    if include_coverage:
        from .chat_evidence import chat_statuses
        statuses=chat_statuses(c,p,[r['id'] for r in items])
        for row in items:row['sync']=statuses[row['id']]
    return serial({'items':items,'next_cursor':cursor,'order':order,'awaiting_reply_total':total,
        'awaiting_reply_days':awaiting_reply_days if awaiting_reply else None,
        'bot_filter_coverage':'observed_sender_flags_only' if awaiting_reply else None,
        'awaiting_reply_scope':'private_and_explicit_group_attention' if awaiting_reply else None,
        'group_attention_coverage':'observed_mentions_and_replies_only',
        'catalog_coverage':'archived_chats_only','provider_read_state':'unknown',**coverage(store,c,p,include_coverage)})
