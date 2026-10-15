"""Scoped archive head facts and explicitly heuristic conversation suggestions.

A database read is not a provider synchronization. Source timestamps retain their
observed resolution, and neither hash IDs nor ingest order resolve WhatsApp ties.
"""
from datetime import datetime, timedelta

from .policy import scope_filter


HEAD_FRESHNESS = timedelta(minutes=5)
HEAD_LIMIT = 64


def _instant(value):
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def _evidence(messages):
    return [{'message_id':m['id'], 'revision':m['revision']} for m in messages]


def _snapshot(message):
    if message is None:
        return None
    snapshot={key:message.get(key) for key in ('id', 'revision', 'direction', 'sender',
        'sender_name', 'username', 'sent_at', 'body', 'kind', 'display_text',
        'order_basis', 'order_status', 'timestamp_precision')} | {
            'provider_message_id':message.get('provider_id')}
    for key,limit in (('body',1000),('display_text',1200)):
        value=snapshot.get(key)
        snapshot[key+'_truncated']=isinstance(value,str) and len(value)>limit
        if snapshot[key+'_truncated']:
            snapshot[key]=value[:limit]
    return snapshot


def _meaningful(message):
    return not message.get('is_service') and (
        bool((message.get('body') or '').strip()) or
        message.get('kind') not in (None, 'unknown', 'system', 'text') or
        bool(message.get('attachments')))


def _unstable(messages):
    if any((m.get('evidence') or {}).get('revision_changed_during_read') for m in messages):
        return 'message_revision_changed_during_read'
    if any((m.get('evidence') or {}).get('active_conflicts',0) for m in messages):
        return 'message_version_conflict'
    return None


def derive_state(messages, *, platform, chat_type, as_of, history,
        self_chat=False, attention=None, truncated=False, meaningful_fallback=None):
    """Derive from newest-first messages selected with the shared order resolver.

    The argument contains the actual authorized chat head, never a context page.
    Equal-time WhatsApp candidates remain a set unless verified source evidence
    supplies an order. A same-actor set still establishes who last spoke.
    """
    observed = [m for m in messages if not m.get('is_service') and not m.get('deleted')]
    significant = [m for m in observed if _meaningful(m)]
    if not significant and meaningful_fallback is not None:
        significant = [meaningful_fallback]
    latest = significant[0] if significant else None
    top = observed[0] if observed else None
    # Only compare source times. first_observed_at cannot establish chronology.
    tie = [m for m in significant if m['sent_at']==latest['sent_at']] if latest else []
    head_tie=[m for m in observed if m['sent_at']==top['sent_at']] if top else []
    order_candidates=head_tie
    source_order = platform=='telegram' and bool(order_candidates) and all(
        str(m.get('provider_id') or '').isascii() and
        str(m.get('provider_id') or '').isdigit() and len(str(m['provider_id']))<=18 for m in order_candidates)
    verified = bool(order_candidates) and all(m.get('order_basis')=='verified_provider_order' and
        m.get('order_status')=='known' for m in order_candidates)
    ambiguous = len(head_tie)>1 and not (source_order or verified)
    unclassified=bool(top) and (not _meaningful(top) or not (source_order or verified) and
        any(not _meaningful(m) for m in head_tie))
    candidates = tie if ambiguous else ([latest] if latest else [])
    directions = {m.get('direction') for m in candidates}
    actor = 'owner' if directions=={'outgoing'} else 'peer' if directions=={'incoming'} else 'unknown'
    code = 'last_archived_message_outgoing' if actor=='owner' else 'last_archived_message_incoming'
    action = 'await_peer' if actor=='owner' else 'review_incoming' if actor=='peer' else 'unclear'
    if not observed:
        actor='unknown'; action='unclear'; code='no_archived_messages'
    elif unclassified:
        actor='unknown'; action='unclear'; code='unclassified_latest_message'; candidates=head_tie
    elif ambiguous and actor=='unknown':
        action='unclear'; code='ambiguous_message_order'
    if truncated:
        actor='unknown'; action='unclear'; code='head_candidates_truncated'
    unstable=_unstable(candidates)
    if unstable:
        actor='unknown'; action='unclear'; code=unstable

    scanned = _instant(history.get('last_head_scan_at'))
    if scanned is None:
        freshness='unknown'; uncertainty='provider_head_not_verified'
    elif scanned>as_of:
        freshness='invalid_observation'; uncertainty='provider_head_not_verified'
    elif as_of-scanned>HEAD_FRESHNESS:
        freshness='stale'; uncertainty='provider_head_stale'
    elif top and scanned<_instant(top['sent_at']):
        freshness='behind_latest_message'; uncertainty='provider_head_not_verified'
    else:
        freshness='recently_verified'; uncertainty=None
    gaps=list(history.get('gaps') or [])
    if gaps:
        uncertainty='history_has_gaps'
    if history.get('last_error_code'):
        uncertainty=history['last_error_code']
    if uncertainty and code not in ('no_archived_messages', 'unclassified_latest_message',
            'ambiguous_message_order', 'head_candidates_truncated',
            'message_revision_changed_during_read', 'message_version_conflict'):
        action='unclear'; code=uncertainty
    result = {'latest_known_meaningful_message':_snapshot(latest) if not ambiguous else None,
        'latest_known_meaningful_messages':[_snapshot(m) for m in candidates if _meaningful(m)],
        'last_turn':actor, 'suggested_action':action,
        'basis':{'code':code, 'evidence':_evidence(candidates), 'heuristic':True},
        'as_of':as_of, 'provider_head_verified_at':scanned,
        'head_freshness':freshness, 'order_status':'ambiguous' if ambiguous else 'known' if latest else 'unknown',
        'coverage':{'gaps':gaps, 'history_state':history.get('state'),
            'last_error_code':history.get('last_error_code'), 'full_history_verified':False},
        'scope':'authorized_chat_head', 'contract_version':1}
    if chat_type=='channel' or self_chat:
        result.update(last_turn='not_applicable',suggested_action='not_applicable')
        result['basis']['code']='channel_or_self_chat'
    elif chat_type=='group':
        selected=list(attention or [])
        target=selected[0] if selected else None
        attention_ambiguous=any(m.get('attention_status')=='ambiguous' for m in selected)
        attention_unstable=_unstable(selected) or unstable
        group_action='review_incoming' if target else 'not_applicable'
        group_code='explicit_owner_attention' if target else 'no_observed_owner_attention'
        if target and (attention_ambiguous or uncertainty or attention_unstable):
            group_action='unclear'
            group_code=attention_unstable or ('ambiguous_attention_reply_order' if attention_ambiguous else uncertainty)
        result.update(last_turn='not_applicable',suggested_action=group_action)
        result['basis']={'code':group_code, 'evidence':_evidence(selected), 'heuristic':True}
        result['owner_attention']={'last_turn':'peer' if target and not (attention_ambiguous or attention_unstable) else
            'unknown' if attention_ambiguous or attention_unstable else 'not_applicable',
            'suggested_action':group_action, 'target_message':_snapshot(target),
            'evidence':_evidence(selected), 'thread_basis':'observed_mentions_and_replies',
            'scope':'authorized_explicit_owner_threads', 'truncated':len(selected)>16}
    elif chat_type not in ('private',):
        result['suggested_action']='unclear'
        result['basis']['code']='chat_type_unknown'
    return result


def provider_reply_matches(reply_metadata, target='m', provider_chat='ch.provider_chat'):
    """Exact Telegram ID or scoped WhatsApp group stanza ID; aliases stay local."""
    reference=f"{reply_metadata}.metadata->>'reply_to_provider_id'"
    prefix=f"CASE WHEN left({target}.provider_id,5)='true_' THEN 'true_' ELSE 'false_' END"
    return f'''({target}.provider_id={reference} OR (right({provider_chat},5)='@g.us'
        AND (left({target}.provider_id,length('true_'||{provider_chat}||'_'))='true_'||{provider_chat}||'_'
          OR left({target}.provider_id,length('false_'||{provider_chat}||'_'))='false_'||{provider_chat}||'_')
        AND split_part(substr({target}.provider_id,length(({prefix})||{provider_chat}||'_')+1),'_',1)={reference}))'''


def group_attention_sql(alias='m', metadata='md', chat_alias='c'):
    return f'''({metadata}.metadata->'mentions_owner'='true'::jsonb OR
        {metadata}.metadata->'reply_to_owner'='true'::jsonb OR EXISTS(
          SELECT 1 FROM messages quoted WHERE quoted.account={alias}.account AND quoted.chat={alias}.chat
            AND NOT quoted.deleted AND quoted.direction='outgoing' AND
            {provider_reply_matches(metadata, 'quoted', chat_alias+'.provider_chat')}))'''


def group_answer_sql(alias='m', metadata='md', chat_alias='c', platform='a.platform', *, same_time=False):
    from .conversation import meaningful_sql, numeric_order_sql
    after = f"answer.sent_at={alias}.sent_at" if same_time else f'''(answer.sent_at>{alias}.sent_at OR
        ({platform}='telegram' AND answer.sent_at={alias}.sent_at AND
          {numeric_order_sql('answer')}>{numeric_order_sql(alias)}))'''
    # A response to this incoming message or its observed owner-thread root may
    # resolve attention. An unthreaded group broadcast cannot resolve another thread.
    return f'''EXISTS(SELECT 1 FROM messages answer LEFT JOIN message_metadata amd
        ON amd.message=answer.id AND amd.revision=answer.revision
        WHERE answer.account={alias}.account AND answer.chat={alias}.chat AND
          answer.direction='outgoing' AND {meaningful_sql('answer','amd')} AND {after}
          AND ({provider_reply_matches('amd',alias,chat_alias+'.provider_chat')} OR
            ({metadata}.metadata->>'reply_to_provider_id' IS NOT NULL AND
              amd.metadata->>'reply_to_provider_id'={metadata}.metadata->>'reply_to_provider_id')))'''


def conversation_state(c,p,account,chat):
    """Resolve the actual archive head only after checking exact account/chat ACL."""
    from .store import APIError
    from .conversation import decorate_messages, meaningful_sql, order_terms, visible_sql
    from .message_queries import BASE, enrich
    from .chat_evidence import rows, history as coverage_history
    scope,args=scope_filter(p,'c',chat_column='id')
    info=c.execute('''SELECT c.id,c.account,c.chat_type,c.provider_chat,a.platform,
        coalesce(ro.is_self_chat,false) AS self_chat,CURRENT_TIMESTAMP AS as_of
        FROM chats c JOIN accounts a ON a.id=c.account LEFT JOIN chat_read_observations ro
        ON ro.account=c.account AND ro.chat=c.id WHERE c.account=%s AND c.id=%s AND '''+scope,
        [account,chat,*args]).fetchone()
    if info is None:
        raise APIError('not_found',404)
    ordering=','.join(term+' DESC' for term in order_terms('m'))
    head_query=''' FROM messages m LEFT JOIN message_metadata md ON md.message=m.id
        AND md.revision=m.revision WHERE m.account=%s AND m.chat=%s AND NOT m.deleted AND '''+visible_sql()
    observed=c.execute('WITH newest AS (SELECT m.sent_at'+head_query+
        ' ORDER BY '+ordering+' LIMIT 1) SELECT '+BASE+head_query+
        ' AND m.sent_at=(SELECT sent_at FROM newest) ORDER BY '+ordering+' LIMIT %s',
        [account,chat,account,chat,HEAD_LIMIT+1]).fetchall()
    truncated=len(observed)>HEAD_LIMIT and observed[0]['sent_at']==observed[-1]['sent_at']
    observed=observed[:HEAD_LIMIT]
    enrich(c,observed);decorate_messages(c,p,observed)
    fallback=None
    if not any(_meaningful(m) for m in observed):
        fallback=c.execute('SELECT '+BASE+''' FROM messages m LEFT JOIN message_metadata md
            ON md.message=m.id AND md.revision=m.revision WHERE m.account=%s AND m.chat=%s
            AND '''+meaningful_sql()+' ORDER BY '+ordering+' LIMIT 1',[account,chat]).fetchone()
        if fallback:
            enrich(c,[fallback]);decorate_messages(c,p,[fallback])
    attention=[]
    if info['chat_type']=='group':
        attention=c.execute('SELECT '+BASE+''', CASE WHEN a.platform='whatsapp' AND '''+
            group_answer_sql(same_time=True)+''' THEN 'ambiguous' ELSE 'open' END AS attention_status
            FROM messages m JOIN chats c ON c.id=m.chat AND c.account=m.account
            JOIN accounts a ON a.id=m.account LEFT JOIN message_metadata md ON md.message=m.id
            AND md.revision=m.revision WHERE m.account=%s AND m.chat=%s AND m.direction='incoming'
            AND '''+meaningful_sql()+' AND '+group_attention_sql()+' AND NOT '+group_answer_sql()+
            ' ORDER BY '+ordering+' LIMIT 17',[account,chat]).fetchall()
        enrich(c,attention);decorate_messages(c,p,attention)
    coverage_rows=rows(c,p,[chat],[account])
    covered=coverage_history(coverage_rows[0]) if coverage_rows else {}
    result=derive_state(observed, platform=info['platform'], chat_type=info['chat_type'],
        as_of=info['as_of'],history=covered,self_chat=info['self_chat'] or
            any(m.get('is_self_chat') is True for m in observed) or
            info['platform']=='telegram' and info['provider_chat']=='777000',
        attention=attention,truncated=truncated,meaningful_fallback=fallback)
    result.update(account=account,chat_id=chat)
    return result
