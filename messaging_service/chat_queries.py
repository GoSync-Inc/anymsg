"""Scoped catalogue and evidence-backed, deliberately heuristic reply queue."""
from .policy import scope_filter,targets
from .search_query import coverage,cursor_binding,decode_cursor,encode_cursor,filter_values,valid_text


def meaningful(alias='m',metadata='md'):
    return f'''NOT {alias}.deleted AND NOT coalesce({metadata}.metadata->'is_service'='true'::jsonb,false)
        AND (length(btrim({alias}.body))>0 OR {metadata}.metadata->>'media_kind' IS NOT NULL
          OR EXISTS(SELECT 1 FROM media_jobs f WHERE f.message={alias}.id AND f.state<>'superseded')
          OR EXISTS(SELECT 1 FROM attachments f WHERE f.message={alias}.id AND f.active))'''


def number(alias):
    return f"CASE WHEN {alias}.provider_id ~ '^[0-9]{{1,18}}$' THEN {alias}.provider_id::bigint END"


def list_chats(store,c,p,after='',limit=100,*,accounts=None,query='',chat_types=None,
        order='id',exclude_channels=False,awaiting_reply=False,from_time='',to_time='',
        include_coverage=True,awaiting_reply_days=14,exclude_chats=None,exclude_senders=None,
        only_questions=False,only_unread=False,selected_chat=None,include_dismissed=False):
    from .store import APIError,bounded,date_bound,serial
    bounded(limit);valid_text(after,4096);valid_text(query,200)
    if order=='last_activity_desc':order='activity'
    if order not in ('id','activity','priority') or any(type(v) is not bool for v in
            (exclude_channels,awaiting_reply,include_coverage,only_questions,only_unread)):
        raise APIError('invalid_filter')
    if type(awaiting_reply_days) is not int or not 0<=awaiting_reply_days<=365:
        raise APIError('invalid_filter')
    if awaiting_reply and order=='id':order='activity'
    if order=='priority' and not awaiting_reply:raise APIError('invalid_filter')
    c.execute('SET LOCAL jit=off')
    accounts=filter_values(accounts);chat_types=filter_values(chat_types)
    exclude_chats=filter_values(exclude_chats);exclude_senders=filter_values(exclude_senders)
    if chat_types is not None and any(t not in ('private','group','channel','unknown') for t in chat_types):
        raise APIError('invalid_filter')
    lower,upper=date_bound(from_time),date_bound(to_time)
    if lower and upper and lower>=upper:raise APIError('invalid_date_range')
    scope,args=scope_filter(p,'c',chat_column='id');clauses=[scope]
    if selected_chat:clauses.append('c.id=%s');args.append(selected_chat)
    if accounts is not None:clauses.append('c.account=ANY(%s)');args.append(accounts)
    if query:
        clauses.append("c.title ILIKE %s ESCAPE E'\\\\'")
        args.append('%'+query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%')
    if chat_types is not None:clauses.append("coalesce(c.chat_type,'unknown')=ANY(%s)");args.append(chat_types)
    if exclude_channels:clauses.append("c.chat_type IS DISTINCT FROM 'channel'")
    if exclude_chats is not None:clauses.append('NOT c.id=ANY(%s)');args.append(exclude_chats)
    if exclude_senders is not None:
        clauses.append('(attention.sender IS NULL OR NOT attention.sender=ANY(%s))');args.append(exclude_senders)
    binding=cursor_binding(p,'chats',{'accounts':accounts,'query':query,'types':chat_types,'order':order,
        'exclude_channels':exclude_channels,'awaiting_reply':awaiting_reply,'awaiting_reply_days':awaiting_reply_days,
        'from':lower,'to':upper,'exclude_chats':exclude_chats,'exclude_senders':exclude_senders,
        'only_questions':only_questions,'only_unread':only_unread})
    pos=decode_cursor(after,binding) if after and order!='id' else None
    now=c.execute('SELECT CURRENT_TIMESTAMP AS t').fetchone()['t'];score_at=now
    if pos and order=='priority':
        if set(pos)!={'id','at','score','as_of'} or type(pos['score']) is not int:raise APIError('invalid_cursor')
        score_at=date_bound(pos['as_of'])
        if score_at is None or score_at>now:raise APIError('invalid_cursor')
    age='true' if not awaiting_reply_days else f"attention.sent_at>=CURRENT_TIMESTAMP-interval '{awaiting_reply_days} days'"
    attention_age='true' if not awaiting_reply_days else f"m.sent_at>=CURRENT_TIMESTAMP-interval '{awaiting_reply_days} days'"
    dismissed='(d.message=attention.id AND d.revision=attention.revision AND d.incoming_head=incoming.id)'
    pending="(attention.id IS NOT NULL AND NOT coalesce(ro.is_self_chat,false) AND NOT coalesce(a.platform='telegram' AND c.provider_chat='777000',false) AND NOT coalesce(attention.is_self_chat,false) AND "+age
    if not include_dismissed:pending+=' AND NOT coalesce('+dismissed+',false)'
    pending+=')'
    fresh="(ro.observed_at>=CURRENT_TIMESTAMP-interval '5 minutes' AND ro.observed_at>=incoming.sent_at AND ro.unread_count IS NOT NULL)"
    unread='CASE WHEN '+fresh+' THEN ro.unread_count>0 END'
    score="(CASE WHEN c.chat_type='private' THEN 100 ELSE 25 END + CASE WHEN attention.question THEN 25 ELSE 0 END + CASE WHEN attention.known_contact THEN 10 ELSE 0 END + least(20,greatest(0,floor(extract(epoch FROM (score_clock.at-attention.sent_at))/86400)::integer)))"
    if awaiting_reply:clauses.append(pending)
    if only_questions:clauses.append('attention.question')
    for bound,op in [(lower,'>='),(upper,'<')]:
        if bound:clauses.append('last.sent_at'+op+'%s');args.append(bound)
    after_owner=f'''(own_last.sent_at IS NULL OR m.sent_at>own_last.sent_at OR
        (a.platform='telegram' AND m.sent_at=own_last.sent_at AND {number('m')}>own_last.numeric_id))'''
    ordered=f"m.sent_at DESC,{number('m')} DESC NULLS LAST,m.id DESC"
    select_message=f'''SELECT m.id,m.provider_id,m.revision,m.sender,m.sent_at,m.direction,
        coalesce(nullif(left(m.body,160),''),'['||coalesce(md.metadata->>'media_kind','attachment')||']') AS snippet,
        md.metadata->'is_self_chat'='true'::jsonb AS is_self_chat,
        md.metadata->'sender_is_contact'='true'::jsonb AS known_contact,
        (position('?' in m.body)>0 OR position('？' in m.body)>0) AS question,
        CASE WHEN c.chat_type='private' THEN 'last_archived_message_incoming'
          WHEN md.metadata->'mentions_owner'='true'::jsonb THEN 'owner_mention' ELSE 'reply_to_owner_message' END AS basis
        FROM messages m LEFT JOIN message_metadata md ON md.message=m.id AND md.revision=m.revision
        WHERE m.account=c.account AND m.chat=c.id AND {meaningful()}'''
    attention_filter='''(c.chat_type='private' AND NOT coalesce(md.metadata->'sender_is_bot'='true'::jsonb,false)
        OR c.chat_type='group' AND (md.metadata->'mentions_owner'='true'::jsonb
          OR md.metadata->'reply_to_owner'='true'::jsonb OR EXISTS(
            SELECT 1 FROM messages quoted WHERE quoted.account=m.account AND quoted.chat=m.chat
              AND NOT quoted.deleted AND quoted.direction='outgoing' AND
              (quoted.provider_id=md.metadata->>'reply_to_provider_id' OR (right(c.provider_chat,5)='@g.us'
                AND left(quoted.provider_id,length('true_'||c.provider_chat||'_'))='true_'||c.provider_chat||'_'
                AND split_part(substr(quoted.provider_id,length('true_'||c.provider_chat||'_')+1),'_',1)
                  =md.metadata->>'reply_to_provider_id')))))'''
    base=f''' FROM chats c JOIN accounts a ON a.id=c.account
      CROSS JOIN (SELECT %s::timestamptz AS at) score_clock
      LEFT JOIN LATERAL (SELECT m.sent_at,m.id,m.direction,left(m.body,160) AS snippet FROM messages m
        WHERE m.account=c.account AND m.chat=c.id AND NOT m.deleted ORDER BY {ordered} LIMIT 1) last ON true
      LEFT JOIN LATERAL (SELECT m.sent_at,{number('m')} AS numeric_id FROM messages m
        LEFT JOIN message_metadata md ON md.message=m.id AND md.revision=m.revision
        WHERE m.account=c.account AND m.chat=c.id AND m.direction='outgoing' AND {meaningful()}
        ORDER BY {ordered} LIMIT 1) own_last ON true
      LEFT JOIN LATERAL ({select_message} AND m.direction='incoming'
        ORDER BY {ordered} LIMIT 1) incoming ON true
      LEFT JOIN LATERAL ({select_message} AND m.direction='incoming' AND {after_owner} AND {attention_age}
        AND {attention_filter} ORDER BY {ordered} LIMIT 1) attention ON true
      LEFT JOIN sender_profiles sp ON sp.account=c.account AND sp.chat=c.id AND sp.sender=attention.sender
      LEFT JOIN chat_read_observations ro ON ro.account=c.account AND ro.chat=c.id
      LEFT JOIN reply_dismissals d ON d.key_id=%s AND d.chat=c.id WHERE '''
    base_args=[score_at,p['key_id']]
    totals=c.execute('SELECT count(*) AS n,count(*) FILTER(WHERE '+unread+' IS NULL) AS unknown'+base+
        ' AND '.join(clauses),[*base_args,*args]).fetchone() if awaiting_reply else None
    if only_unread:clauses.append(unread+' IS TRUE')
    total=c.execute('SELECT count(*) AS n'+base+' AND '.join(clauses),[*base_args,*args]).fetchone()['n'] if awaiting_reply and only_unread else (totals['n'] if totals else None)
    if after:
        cid=after if order=='id' else pos.get('id');valid_text(cid,empty=False)
        if not c.execute('SELECT 1 FROM chats c WHERE c.id=%s AND '+scope,[cid,*scope_filter(p,'c',chat_column='id')[1]]).fetchone():raise APIError('not_found',404)
        if order=='id':clauses.append('c.id>%s');args.append(cid)
        else:
            if order=='activity' and set(pos)!={'id','at'}:raise APIError('invalid_cursor')
            at=None if pos['at'] is None else date_bound(pos['at'])
            if order=='activity':
                clauses.append("(coalesce(last.sent_at,'-infinity'),c.id)<(coalesce(%s::timestamptz,'-infinity'),%s)");args.extend([at,cid])
            else:
                clauses.append('(-'+score+',attention.sent_at,c.id)>(-%s,%s,%s)');args.extend([pos['score'],at,cid])
    sort={'id':'c.id','activity':"coalesce(last.sent_at,'-infinity') DESC,c.id DESC",
        'priority':score+' DESC,attention.sent_at,c.id'}[order]
    rows=c.execute('''SELECT c.id,c.account,c.title,c.provider_chat AS provider_chat_id,c.chat_type AS type,
        last.sent_at AS last_activity,last.id AS last_message_id,last.direction AS last_message_direction,
        last.snippet AS last_message_snippet,ro.unread_count AS provider_unread_count,ro.unread_count AS unread_count,
        '''+unread+''' AS provider_unread,ro.observed_at AS read_state_observed_at,ro.basis AS read_state_basis,
        CASE WHEN '''+fresh+''' THEN 'observed' WHEN ro.observed_at IS NULL THEN 'unknown' ELSE 'stale' END AS read_state,
        CASE WHEN '''+fresh+''' AND attention.id IS NOT NULL THEN
          CASE WHEN ro.read_inbox_max_id IS NOT NULL AND attention.provider_id ~ '^[0-9]{1,18}$'
            THEN attention.provider_id::bigint<=ro.read_inbox_max_id
          END END AS read_by_owner,
        NULL::boolean AS muted,NULL::boolean AS archived,'''+pending+''' AS awaiting_reply,
        attention.basis AS awaiting_reply_basis,attention.id AS awaiting_reply_message_id,
        attention.revision AS awaiting_reply_revision,attention.provider_id AS awaiting_reply_provider_message_id,
        attention.sent_at AS awaiting_reply_sent_at,attention.snippet AS awaiting_reply_snippet,
        attention.sender AS awaiting_reply_sender,sp.display_name AS awaiting_reply_sender_name,
        sp.username AS awaiting_reply_username,attention.question AS awaiting_reply_has_question,
        attention.known_contact AS awaiting_reply_known_contact,'''+score+''' AS awaiting_reply_priority,
        array_remove(ARRAY[CASE WHEN c.chat_type='private' THEN 'private_chat' ELSE 'group_attention' END,
          CASE WHEN attention.question THEN 'question_mark' END,CASE WHEN attention.known_contact THEN 'provider_contact' END,
          'waiting_age_capped_20_days'],NULL) AS awaiting_reply_priority_reasons,
        incoming.id AS _incoming_head,own_last.sent_at AS _own_sent_at,own_last.numeric_id AS _own_numeric_id,
        a.platform AS _platform,coalesce('''+dismissed+''',false) AS awaiting_reply_dismissed,
        'heuristic_not_obligation' AS awaiting_reply_confidence'''+base+' AND '.join(clauses)+
        ' ORDER BY '+sort+' LIMIT %s',[*base_args,*args,limit+1]).fetchall()
    items=rows[:limit];cursor=None
    if len(rows)>limit:
        row=items[-1];position={'id':row['id'],'at':row['last_activity']}
        if order=='priority':position.update(at=row['awaiting_reply_sent_at'],score=row['awaiting_reply_priority'],as_of=score_at)
        cursor=row['id'] if order=='id' else encode_cursor(binding,position)
    # Compute run counts only for the returned page, never for every chat.
    for row in items:
        if row['awaiting_reply']:
            row['awaiting_reply_incoming_count']=c.execute(f'''SELECT count(*) AS n FROM messages m
                LEFT JOIN message_metadata md ON md.message=m.id AND md.revision=m.revision
                WHERE m.account=%s AND m.chat=%s AND m.direction='incoming' AND {meaningful()}
                  AND NOT coalesce(md.metadata->'sender_is_bot'='true'::jsonb,false)
                  AND (%s::timestamptz IS NULL OR m.sent_at>%s::timestamptz OR
                    (%s='telegram' AND m.sent_at=%s::timestamptz AND {number('m')} >%s::bigint))''',
                (row['account'],row['id'],row['_own_sent_at'],row['_own_sent_at'],
                 row['_platform'],row['_own_sent_at'],row['_own_numeric_id'])).fetchone()['n']
        else:
            for key in tuple(row):
                if key.startswith('awaiting_reply_'):row[key]=None
        for key in ('_own_sent_at','_own_numeric_id','_platform'):row.pop(key,None)
        if not include_dismissed:row.pop('_incoming_head',None)
    targets(p,[r['id'] for r in items])
    if include_coverage:
        from .chat_evidence import chat_statuses
        statuses=chat_statuses(c,p,[r['id'] for r in items])
        for row in items:row['sync']=statuses[row['id']]
    return serial({'items':items,'next_cursor':cursor,'order':order,'awaiting_reply_total':total,
        'awaiting_reply_days':awaiting_reply_days if awaiting_reply else None,
        'read_state_unknown_total':totals['unknown'] if totals else None,
        'only_unread_notice':'unknown_or_stale_read_state_excluded' if only_unread else None,
        'bot_filter_coverage':'observed_sender_flags_only' if awaiting_reply else None,
        'awaiting_reply_scope':'private_and_explicit_group_attention' if awaiting_reply else None,
        'group_attention_coverage':'observed_mentions_and_replies_only',
        'catalog_coverage':'archived_chats_only','provider_read_state':'per_chat_observation_or_unknown',
        **coverage(store,c,p,include_coverage)})
