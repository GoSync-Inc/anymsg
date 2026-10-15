"""Optional transliteration candidates and account-local provider grouping."""
from .policy import scope_filter,targets
from .search_query import cursor_binding,decode_cursor,encode_cursor,filter_values,valid_text
from .name_variants import patterns
from .whatsapp_identity import visible_cte,people_join


def search_people(store,c,p,query='',*,accounts=None,chats=None,after='',limit=20,
                  transliterate=False,group_by_sender=False):
    from .store import APIError,bounded,serial
    bounded(limit);valid_text(query,200);valid_text(after,4096)
    if type(transliterate) is not bool or type(group_by_sender) is not bool:raise APIError('invalid_filter')
    accounts=filter_values(accounts);chats=filter_values(chats)
    query=query.strip().lstrip('@').lower()
    pattern='%'+query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
    scope,args=scope_filter(p,'m')
    clauses=[scope,'NOT m.deleted','sp.account=m.account','sp.chat=m.chat','sp.sender=m.sender']
    for column,values in [('account',accounts),('chat',chats)]:
        if values is not None:clauses.append('m.'+column+'=ANY(%s)');args.append(values)
    match='(sp.search_text LIKE q.pattern OR identity.search_text LIKE q.pattern)'
    if query:
        condition=match
        if transliterate:condition+=' OR messenger_transliterate(sp.search_text) LIKE ANY(q.variants)'
        clauses.append('('+condition+')')
    identity_cte,identity_args=visible_cte(p)
    cte='WITH '+identity_cte+''', q AS (SELECT %s::text AS pattern,%s::text[] AS variants), observed AS MATERIALIZED (
        SELECT sp.id,sp.account,sp.chat,sp.sender AS sender_id,sp.display_name,sp.username,
            sp.source_message,sp.source_sent_at,sp.observed_at,identity.metadata AS identity,
            CASE WHEN '''+match+''' THEN 'literal' ELSE 'transliteration_candidate' END AS match_type
        FROM sender_profiles sp JOIN messages m ON m.id=sp.source_message '''+people_join()+''' CROSS JOIN q
        WHERE '''+' AND '.join(clauses)+') '
    binding=cursor_binding(p,'people',{'query':query,'accounts':accounts,'chats':chats,
        'transliterate':transliterate,'group_by_sender':group_by_sender,'spelling_candidates':1})
    cursor_args=[];cursor_clause=''
    if group_by_sender:
        if after:
            pos=decode_cursor(after,binding)
            if set(pos)!= {'account','sender'}:raise APIError('invalid_cursor')
            valid_text(pos['account'],empty=False);valid_text(pos['sender'],empty=False)
            cursor_clause='WHERE (account,sender_id)>(%s,%s)';cursor_args=[pos['account'],pos['sender']]
        sql='''SELECT account,sender_id,jsonb_agg(to_jsonb(observed) ORDER BY chat,id) AS observations,
            CASE WHEN bool_or(match_type='literal') THEN 'literal' ELSE 'transliteration_candidate' END AS match_type
            FROM observed '''+cursor_clause+' GROUP BY account,sender_id ORDER BY account,sender_id LIMIT %s'
    else:
        if after:
            valid_text(after)
            if not c.execute(cte+'SELECT 1 FROM observed WHERE id=%s',[*identity_args,pattern,patterns(query),*args,after]).fetchone():
                raise APIError('not_found',404)
            cursor_clause='WHERE id>%s';cursor_args=[after]
        sql='SELECT * FROM observed '+cursor_clause+' ORDER BY id LIMIT %s'
    rows=c.execute(cte+sql,[*identity_args,pattern,patterns(query),*args,*cursor_args,limit+1]).fetchall();items=rows[:limit]
    for row in items:
        for item in row['observations'] if group_by_sender else [row]:
            if item['identity'] is None:del item['identity']
    cursor=None
    if len(rows)>limit:
        last=items[-1];cursor=encode_cursor(binding,{'account':last['account'],'sender':last['sender_id']}) if group_by_sender else last['id']
    refs=[r['source_message'] for row in items for r in (row['observations'] if group_by_sender else [row])]
    targets(p,refs)
    return serial({'items':items,'next_cursor':cursor,'identity_model':'provider_sender_per_account_and_chat',
        'names_are_verified_identity':False,'directory_coverage':'observed_provider_names_only',
        'grouping':'same_account_provider_sender' if group_by_sender else 'per_chat_observation',
        'observations_coverage':'matching_visible_profiles_only','transliteration_is_identity_proof':False,
        **store.dataset(c,p)})
