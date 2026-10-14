"""Provider-reported names scoped to their source chat; never merge by name."""
from .policy import scope_filter, targets
from .whatsapp_identity import visible_cte,people_join


def search_people(store,c,p,query='',*,accounts=None,chats=None,after='',limit=20):
    from .store import APIError,bounded,serial
    bounded(limit)
    if not isinstance(query,str) or len(query)>200 or '\x00' in query:
        raise APIError('invalid_query')
    if not isinstance(after,str) or len(after)>256 or '\x00' in after:
        raise APIError('invalid_filter')
    where,args=scope_filter(p,'m')
    # Bind metadata to a currently visible source message, not just a person ID.
    clauses=[where,'NOT m.deleted','sp.account=m.account','sp.chat=m.chat','sp.sender=m.sender']
    for column,values in [('account',accounts),('chat',chats)]:
        if values is not None:
            if not isinstance(values,list) or len(values)>100 or any(
                not isinstance(v,str) or not 1<=len(v)<=256 or '\x00' in v for v in values):
                raise APIError('invalid_filter')
            clauses.append('m.'+column+'=ANY(%s)');args.append(sorted(set(values)))
    identity_cte,identity_args=visible_cte(p)
    prefix='WITH '+identity_cte+' '
    base=' FROM sender_profiles sp JOIN messages m ON m.id=sp.source_message'+people_join()+' WHERE '
    if after:
        if not c.execute(prefix+'SELECT 1'+base+' AND '.join(clauses)+' AND sp.id=%s',[*identity_args,*args,after]).fetchone():
            raise APIError('not_found',404)
        clauses.append('sp.id>%s');args.append(after)
    query=query.strip().lstrip('@').lower()
    if query:
        # Literal substring, not user-defined LIKE patterns or SQL. Trigram GIN
        # indexes useful name/username fragments (three or more characters).
        pattern='%'+query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
        clauses.append('(sp.search_text LIKE %s OR identity.search_text LIKE %s)');args.extend([pattern,pattern])
    rows=c.execute(prefix+'''SELECT sp.id,sp.account,sp.chat,sp.sender AS sender_id,sp.display_name,
        sp.username,sp.source_message,sp.source_sent_at,sp.observed_at,identity.metadata AS identity'''+base+
        ' AND '.join(clauses)+' ORDER BY sp.id LIMIT %s',[*identity_args,*args,limit+1]).fetchall()
    for row in rows:
        if row['identity'] is None:del row['identity']
    targets(p,[r['source_message'] for r in rows[:limit]])
    return serial({'items':rows[:limit],'next_cursor':rows[limit-1]['id'] if len(rows)>limit else None,
        'identity_model':'provider_sender_per_account_and_chat','names_are_verified_identity':False,
        'directory_coverage':'observed_provider_names_only',**store.dataset(c,p)})
