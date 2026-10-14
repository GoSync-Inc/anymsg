"""Scoped message queries and presentation, separate from authentication/ACK."""
from __future__ import annotations

from .policy import scope_filter, targets
from .search_query import (compile_query, coverage, cursor_binding, decode_cursor,
    encode_cursor, filter_values, output_options, shape_messages, valid_text)


JOINS = '''JOIN chats ch ON ch.id=m.chat AND ch.account=m.account
    LEFT JOIN sender_profiles sp ON sp.account=m.account AND sp.chat=m.chat AND sp.sender=m.sender
      AND EXISTS(SELECT 1 FROM messages source WHERE source.id=sp.source_message
        AND source.account=m.account AND source.chat=m.chat AND source.sender=m.sender AND NOT source.deleted)
    LEFT JOIN message_revisions mr ON mr.message=m.id AND mr.revision=m.revision
    LEFT JOIN message_metadata mm ON mm.message=m.id AND mm.revision=m.revision'''
META = '''nullif(sp.display_name,'') AS sender_name,nullif(sp.username,'') AS username,
    ch.title AS chat_title,ch.chat_type,ch.provider_chat AS provider_chat_id,m.provider_id,
    coalesce(mm.metadata,mr.snapshot->'metadata')->>'reply_to_provider_id' AS reply_to_provider_id,
    CASE WHEN jsonb_typeof(coalesce(mm.metadata,mr.snapshot->'metadata')->'forwarded')='boolean'
      THEN (coalesce(mm.metadata,mr.snapshot->'metadata')->>'forwarded')::boolean END AS forwarded,
    CASE WHEN jsonb_typeof(coalesce(mm.metadata,mr.snapshot->'metadata')->'mentions_owner')='boolean'
      THEN (coalesce(mm.metadata,mr.snapshot->'metadata')->>'mentions_owner')::boolean END AS mentions_owner,
    CASE WHEN jsonb_typeof(coalesce(mm.metadata,mr.snapshot->'metadata')->'reply_to_owner')='boolean'
      THEN (coalesce(mm.metadata,mr.snapshot->'metadata')->>'reply_to_owner')::boolean END AS reply_to_owner,
    coalesce(mm.metadata,mr.snapshot->'metadata')->>'media_kind' AS media_kind,
    coalesce(mm.metadata,mr.snapshot->'metadata')->>'sender_identity_basis' AS sender_identity_basis'''
BASE = 'm.id,m.account,m.chat,m.sender,m.body,m.sent_at,m.revision,m.direction'


def enrich(c, rows):
    """Rows were individually authorized; metadata comes from the same chat."""
    if not rows:
        return rows
    metadata=c.execute('SELECT m.id,'+META+' FROM messages m '+JOINS+' WHERE m.id=ANY(%s)',
        ([r['id'] for r in rows],)).fetchall()
    by_id={r['id']:r for r in metadata}
    for row in rows:
        row.update(by_id.get(row['id'],{}))
    from .result_evidence import decorate
    decorate(c,[r for r in rows if 'revision' in r])
    return rows


def filters(p, *, account='',chat='',sender='',accounts=None,chats=None,senders=None,
            from_time='',to_time='',direction=''):
    from .store import APIError,date_bound
    if not isinstance(direction,str) or direction not in ('','incoming','outgoing'):
        raise APIError('invalid_filter')
    lower,upper=date_bound(from_time),date_bound(to_time)
    if lower and upper and lower>=upper:
        raise APIError('invalid_date_range')
    scope,args=scope_filter(p,'m'); clauses=[scope,'NOT m.deleted']
    normalized={'direction':direction,'from_time':lower,'to_time':upper}
    for column,value in [('account',account),('chat',chat),('sender',sender)]:
        valid_text(value)
        normalized[column]=value
        if value: clauses.append('m.'+column+'=%s');args.append(value)
    for column,values in [('account',accounts),('chat',chats),('sender',senders)]:
        values=filter_values(values);normalized[column+'s']=values
        if values is not None:
            clauses.append('m.'+column+'=ANY(%s)');args.append(values)
    if direction:clauses.append('m.direction=%s');args.append(direction)
    for bound,op in [(lower,'>='),(upper,'<')]:
        if bound:clauses.append('m.sent_at'+op+'%s');args.append(bound)
    return clauses,args,normalized


def search(store,c,p,query='',account='',chat='',sender='',from_time='',to_time='',after='',limit=20,
           *,accounts=None,chats=None,senders=None,query_mode='all',order='asc',before='',direction='',
           search_language='auto',fuzzy=False,compact=False,body_chars=None,include_coverage=True):
    from .store import APIError,bounded,date_bound,serial
    import re
    bounded(limit)
    cap=output_options(compact,body_chars,include_coverage)
    plan=compile_query(query,query_mode,search_language)
    if order not in ('asc','desc','relevance') or type(fuzzy) is not bool:
        raise APIError('invalid_filter')
    valid_text(after,4096);valid_text(before)
    if after and before or before and order=='relevance':raise APIError('invalid_filter')
    if fuzzy and (query_mode!='all' or not re.fullmatch(r'\w{3,64}',query,flags=re.UNICODE)):
        raise APIError('invalid_fuzzy_query')
    clauses,args,normalized=filters(p,account=account,chat=chat,sender=sender,accounts=accounts,
        chats=chats,senders=senders,from_time=from_time,to_time=to_time,direction=direction)
    binding=cursor_binding(p,'messages',{**normalized,'query':query,'query_mode':query_mode,
        'language':search_language,'order':order,'fuzzy':fuzzy})
    fuzzy_used=False
    if fuzzy:
        has_primary=c.execute('WITH '+plan.cte+' SELECT 1 FROM messages m CROSS JOIN search_query q WHERE '+
            ' AND '.join(clauses)+' AND '+plan.predicate('m')+' LIMIT 1',[*plan.params,*args]).fetchone()
        fuzzy_used=not bool(has_primary)
    score=plan.rank('m');match_type=plan.match_type('m')
    if fuzzy_used:
        # Fixed threshold, server-side candidate cap and statement timeout.
        # No silent union of approximate and exact results.
        c.execute("SELECT set_config('pg_trgm.word_similarity_threshold','0.6',true)")
        condition='lower(m.body) %%> lower(q.literal)'
        score='round((word_similarity(lower(q.literal),lower(m.body))*1000000)::numeric)::bigint'
        match_type="'trigram_fallback'::text"
    else:
        condition=plan.predicate('m')
    base_sql='SELECT m.id,m.sent_at,m.revision,'+score+' AS score FROM messages m CROSS JOIN search_query q WHERE '+\
        ' AND '.join(clauses)+' AND '+condition
    ctes=[plan.cte]
    if fuzzy_used:
        ctes.append('matches AS MATERIALIZED ('+base_sql+' ORDER BY score DESC,m.sent_at DESC,m.id DESC LIMIT 200)')
    elif plan.query:
        # Separate selective indexed matching from the chronological LIMIT.
        # Otherwise the planner may prefer walking the date index, expecting
        # early hits; a rare or absent query then traverses the entire archive.
        ctes.append('matches AS MATERIALIZED ('+base_sql+')')
    else:
        ctes.append('matches AS NOT MATERIALIZED ('+base_sql+')')
    cursor_clause='';cursor_args=[]
    if after or before:
        cursor=after or before
        if cursor.startswith('q1.'):
            position=decode_cursor(cursor,binding)
            if set(position)!={'id','at','score','revision','fuzzy'}:
                raise APIError('invalid_cursor')
            mid=valid_text(position['id'],empty=False)
            at=date_bound(position['at'])
            if (at is None or type(position['score']) is not int or not 0<=position['score']<=1000000
                    or type(position['revision']) is not int or type(position['fuzzy']) is not bool
                    or position['fuzzy']!=fuzzy_used):
                raise APIError('invalid_cursor')
            anchor=store.message(c,p,mid)
            if anchor['revision']!=position['revision'] or anchor['sent_at']!=at:
                raise APIError('cursor_changed',409)
            if order=='relevance':
                cursor_clause='WHERE (score,sent_at,id)<(%s,%s,%s)'
                cursor_args=[position['score'],at,mid]
            else:
                cursor_clause='WHERE (sent_at,id)'+('<' if order=='desc' else '>')+'(%s,%s)'
                cursor_args=[at,mid]
        else:
            if order=='relevance':raise APIError('invalid_cursor')
            anchor=store.message(c,p,cursor)
            cursor_clause='WHERE (sent_at,id)'+('<' if before or order=='desc' else '>')+'(%s,%s)'
            cursor_args=[anchor['sent_at'],anchor['id']]
    sort='score DESC,sent_at DESC,id DESC' if order=='relevance' else 'sent_at '+order.upper()+',id '+order.upper()
    ctes.append('page AS MATERIALIZED (SELECT * FROM matches '+cursor_clause+' ORDER BY '+sort+' LIMIT %s)')
    final_sort='page.score DESC,m.sent_at DESC,m.id DESC' if order=='relevance' else 'm.sent_at '+order.upper()+',m.id '+order.upper()
    snippet='left('+plan.snippet('m.body')+',800)'
    projection=BASE if cap is None else BASE.replace('m.body','left(m.body,'+str(cap)+') AS body,length(m.body)>'+str(cap)+' AS body_truncated')
    rows=c.execute('WITH '+','.join(ctes)+' SELECT '+projection+','+META+',page.score,'+match_type+
        ' AS match_type,'+snippet+' AS snippet FROM page JOIN messages m ON m.id=page.id '+JOINS+
        ' CROSS JOIN search_query q ORDER BY '+final_sort,[*plan.params,*args,*cursor_args,limit+1]).fetchall()
    items=rows[:limit]
    next_cursor=None
    if len(rows)>limit:
        last=items[-1]
        next_cursor=encode_cursor(binding,{'id':last['id'],'at':last['sent_at'],'score':last['score'],
            'revision':last['revision'],'fuzzy':fuzzy_used}) if order=='relevance' or fuzzy else last['id']
    shape_messages(items,compact=compact)
    from .result_evidence import decorate
    from .chat_evidence import query_evidence
    decorate(c,items)
    targets(p,[r['id'] for r in items])
    return serial({'items':items,'next_cursor':next_cursor,
        'result_evidence':query_evidence(c,p,normalized,items,paged=bool(after or before),include=include_coverage),
        'search':{'language':search_language,'query_mode':query_mode,'order':order,'fuzzy_used':fuzzy_used,
            'fuzzy_candidate_limit':200 if fuzzy_used else None},**coverage(store,c,p,include_coverage)})
