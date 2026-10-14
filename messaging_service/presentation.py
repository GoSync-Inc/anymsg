"""Bounded agent summaries; full evidence remains available by explicit opt-in."""
MESSAGE_FIELDS = ('id','message','account','chat','sender','sent_at','revision','direction',
    'provider_message_id','kind','origin','historical','is_backfill','event_sequence','sender_name','username',
    'chat_title','chat_type','reply_to_provider_id','mentions_owner','reply_to_owner','forwarded','media_kind','body_truncated')
FILE_FIELDS = ('attachment_id','id','message_id','account','chat','revision','sha256','mime',
    'bytes','display_name','download_status','extraction_status','enrichment_status',
    'truncated','error_code','title','document_type','snippet','match_type',
    'matched_content','match_sources','message_context','processing_profile','error_detail','attempts','next_due')


def options(method, arguments):
    arguments=dict(arguments)
    detail=arguments.pop('detail','compact')
    # Explicit legacy options remain valid; otherwise compact is the default.
    if 'compact' in arguments:
        if arguments['compact'] is None:arguments['compact']=detail=='compact'
        else:detail='compact' if arguments['compact'] else 'full'
    if 'include_coverage' in arguments and arguments['include_coverage'] is None:
        arguments['include_coverage']=detail=='full'
    return arguments,detail


def file_summary(row):
    result={k:row[k] for k in FILE_FIELDS if k in row and row[k] is not None}
    if row.get('excerpt'):result['snippet']=row['excerpt']
    for key in ('display_name','title','snippet'):
        if key in result:
            value=result[key]
            match=value.find('[[') if key=='snippet' else -1
            start=max(0,match-40) if match>=160 else 0
            result[key]=value[start:start+160]
    return result


def message_summary(row):
    result={k:row[k] for k in MESSAGE_FIELDS if k in row and row[k] is not None}
    for key,maximum in [('sender_name',64),('username',64),('chat_title',72)]:
        if key in result:result[key]=result[key][:maximum]
    snippet=row.get('snippet') or row.get('body') or ''
    files=row.get('attachments') or []
    if not snippet and (files or row.get('media_kind')):
        snippet='['+(row.get('media_kind') or 'attachment')+']'
        if files:snippet+=' '+(files[0].get('display_name') or files[0].get('filename') or '')
        result['display_text_is_media_label']=True
    match=snippet.find('[[')
    start=max(0,match-32) if match>=96 else 0
    result['snippet']=snippet[start:start+96]
    if not result.get('body_truncated'):result.pop('body_truncated',None)
    evidence=row.get('evidence') or {}
    # Never silently remove the reasons that make a result uncertain.
    result['integrity']={'provider_currentness_verified':False,
        **{k:evidence[k] for k in ('active_conflicts','revision_changed_during_read') if evidence.get(k)}}
    if files:
        result['attachments']=[file_summary(f) for f in files[:2]]
        result['attachment_count']=len(files)
        if len(files)>2:result['attachments_truncated']=True
    return result


def render(method, result, detail):
    if detail=='full' or method not in ('list_chats','search','get_messages','get_updates',
        'get_context','search_files','get_file_text','get_file_segments'):return result
    result=dict(result)
    result.pop('sources',None)
    if method in ('search','get_messages','get_updates'):
        result['items']=[message_summary(row) for row in result['items']]
    elif method=='get_context':
        result['message']=message_summary(result['message'])
        result['context']=[message_summary(row) for row in result['context']]
        for key in ('attachments','pending_attachments'):
            result[key]=[file_summary(row) for row in result[key]]
        result.pop('chat_sync',None)
    elif method=='list_chats':
        result['items']=[{k:v for k,v in row.items() if v is not None and k!='sync'}
            for row in result['items']]
        for row in result['items']:
            row.pop('sync',None)
            row.pop('awaiting_reply_confidence',None)
            if not result.get('awaiting_reply_scope'):
                for key in tuple(row):
                    if key.startswith('awaiting_reply_'):row.pop(key,None)
            row['title']=row['title'][:64]
            if result.get('awaiting_reply_scope') and row.get('last_message_snippet'):
                row['last_message_snippet']=row['last_message_snippet'][:80]
            else:row.pop('last_message_snippet',None)
            if row.get('awaiting_reply_snippet'):
                row['awaiting_reply_snippet']=row['awaiting_reply_snippet'][:80]
        result['awaiting_reply_notice']='heuristic_not_obligation'
        if result.get('awaiting_reply_scope') is None:
            result.pop('awaiting_reply_scope',None)
            result.pop('awaiting_reply_total',None)
            result.pop('group_attention_coverage',None)
    elif method=='search_files':
        result['items']=[file_summary(row) for row in result['items']]
    if method in ('search_files','get_file_text','get_file_segments'):
        result.pop('status',None)  # Legacy extraction alias remains in full mode.
    result['detail']='compact'
    return result
