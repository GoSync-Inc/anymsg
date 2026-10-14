"""PostgreSQL archive: one policy layer for HTTP and MCP.

No provider sessions, network fetch, arbitrary SQL or server approval gate is
exposed to agents. Opt-in sends are dispatched by existing operator collectors.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import hashlib
import os
from pathlib import Path
import re
import secrets
import time
import json
import threading
from collections import OrderedDict
from uuid import UUID

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from . import __version__
from .policy import (DEFAULT_PERMISSIONS, OPERATION_PERMISSIONS, UNSET, audit,
                     key_id, lock_principal, normalize_policy, principal_id,
                     scope_filter, targets)


class APIError(Exception):
    def __init__(self, code: str, status: int = 400):
        self.code, self.status = code, status
        super().__init__(code)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def bounded(value: int, maximum: int = 100) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise APIError('invalid_limit')
    return value


def date_bound(value: str) -> datetime | None:
    if value=='':
        return None
    if not isinstance(value,str):raise APIError('invalid_utc_date')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('timezone required')
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError):
        raise APIError('invalid_utc_date') from None


def serial(rows):
    if isinstance(rows, dict):
        return {k: serial(v) for k, v in rows.items()}
    if isinstance(rows, list):
        return [serial(v) for v in rows]
    if isinstance(rows, datetime):
        return rows.isoformat()
    if isinstance(rows, UUID):
        return str(rows)
    return rows


class Store:
    def __init__(self, dsn: str, files: Path, public_url: str):
        self._status_cache=OrderedDict()
        self._status_cache_lock=threading.Lock()
        self.files = files.resolve()
        self.public_url = public_url.rstrip('/')
        self.pool = ConnectionPool(dsn, min_size=1, max_size=6, timeout=5, open=True,
            kwargs={'row_factory': dict_row, 'connect_timeout': 5,
                    'options': '-c statement_timeout=5000 -c timezone=UTC'})
        self.pool.wait(timeout=10)

    def close(self):
        self.pool.close()

    @staticmethod
    def identity(c, token: str):
        if not isinstance(token, str) or not 32 <= len(token) <= 256:
            raise APIError('unauthorized', 401)
        return Store.identity_digest(c, digest(token))

    @staticmethod
    def identity_digest(c, key_digest, *, missing='unauthorized', status=401):
        known = c.execute('SELECT principal,id FROM api_keys WHERE digest=%s', (key_digest,)).fetchone()
        if not known:
            raise APIError(missing, status)
        lock_principal(c, known['principal'], shared=True)
        # READ COMMITTED rechecks after a concurrent operator changed access.
        # clock_timestamp also rejects credentials that expired while waiting.
        row = c.execute('''SELECT p.*, k.digest AS key_digest,k.id AS key_id FROM api_keys k
            JOIN principals p ON p.id=k.principal
            WHERE k.digest=%s AND NOT k.revoked AND k.expires_at>clock_timestamp() AND p.enabled''',
            (key_digest,)).fetchone()
        if not row:
            error = APIError(missing, status)
            error.audit_identity = {'id': known['principal'], 'key_id': known['id']}
            raise error
        return row

    @contextmanager
    def access(self, operation, resolve_identity):
        failure = None
        p = None
        with self.pool.connection() as c:
            with c.transaction():
                try:
                    with c.transaction():
                        p = resolve_identity(c)
                        permission = OPERATION_PERMISSIONS[operation] if operation else None
                        if permission and permission not in p['permissions']:
                            raise APIError('forbidden', 403)
                        yield c, p
                except Exception as error:
                    # Roll back the attempted action, but commit its denial audit
                    # outside the savepoint. Successful mutations + audit commit
                    # atomically; a broken audit sink never becomes silent success.
                    from .error_details import classified
                    code=classified(error)
                    failure = APIError(code,503) if code in ('query_timeout','service_busy','service_unavailable') else error
                    p = p or getattr(error, 'audit_identity', None)
                if operation:
                    outcome = 'ok' if failure is None else ('denied' if isinstance(failure, APIError) else 'error')
                    audit(c, p, operation, outcome,
                          failure.code if isinstance(failure, APIError) else ('operation_failed' if failure else None))
        if failure is not None:
            failure.operation=operation
            if isinstance(failure,APIError) and operation:failure.audited=True
            raise failure

    def authorized(self, token, operation=None):
        return self.access(operation, lambda c: self.identity(c, token))

    def audit_boundary(self, code):
        with self.pool.connection() as c:
            audit(c, None, 'http_boundary', 'denied', code)

    def authenticate(self, token):
        with self.authorized(token) as (_, p):
            return p['id']

    @staticmethod
    def dataset(c,p):
        if p['chat_ids'] is not None:
            # Account-wide last_ingest_at/detail may change only because of a
            # hidden chat. Do not disclose those aggregates to a chat-scoped key.
            rows=c.execute('''SELECT a.id,a.synthetic,'chat_scope_only' AS coverage,
                NULL AS status,NULL AS last_ingest_at,NULL AS detail_code FROM accounts a
                WHERE a.id=ANY(%s) AND EXISTS(SELECT 1 FROM chats c
                    WHERE c.account=a.id AND c.id=ANY(%s)) ORDER BY a.id''',
                (p['accounts'],p['chat_ids'])).fetchall()
            return {'synthetic':all(r['synthetic'] for r in rows),'sources':rows,'coverage':'chat_scope_only'}
        rows=c.execute('''SELECT a.id,a.synthetic,coalesce(s.coverage,'synthetic_fixture_only') AS coverage,
            s.status,s.last_ingest_at,s.detail_code FROM accounts a LEFT JOIN source_status s ON s.account=a.id
            WHERE a.id=ANY(%s) ORDER BY a.id''',(p['accounts'],)).fetchall()
        conflicts={r['account']:r for r in c.execute('''SELECT m.account,
            count(*) FILTER(WHERE mc.resolved_at IS NULL) AS active_conflicts,
            count(*) FILTER(WHERE mc.resolved_at IS NOT NULL) AS historical_conflicts
            FROM message_conflicts mc JOIN messages m ON m.id=mc.message
            WHERE m.account=ANY(%s) GROUP BY m.account''',(p['accounts'],))}
        for row in rows:
            row.update({k:v for k,v in conflicts.get(row['id'],{'active_conflicts':0,'historical_conflicts':0}).items() if k!='account'})
            row['detail_scope']='account_summary_not_this_result'
            if row['detail_code']=='version_conflict' and not row['active_conflicts']:
                row['detail_code']='coverage_not_reconciled'
            from .status_details import describe
            row['detail']=describe(row['detail_code'])
        return serial({'synthetic':all(r['synthetic'] for r in rows),'sources':rows,
                       'coverage':'partial_or_unknown' if any(r['status'] for r in rows) else 'synthetic_fixture_only'})

    @staticmethod
    def message(c, p, mid):
        if not isinstance(mid,str) or not 1<=len(mid)<=256:
            raise APIError('not_found',404)
        where,args=scope_filter(p)
        row = c.execute('SELECT * FROM messages WHERE id=%s AND '+where, [mid,*args]).fetchone()
        if not row:
            raise APIError('not_found', 404)

        for key in list(row):
            if key=='document' or key.startswith('document_'):row.pop(key)
        targets(p,[row['id']])
        return row

    def capabilities(self, token):
        with self.authorized(token,'capabilities') as (c, p):
            dataset=self.dataset(c,p)
            accounts = c.execute('SELECT id,platform,label,synthetic FROM accounts WHERE id=ANY(%s) ORDER BY id',
                                 ([r['id'] for r in dataset['sources']],)).fetchall()
            features={'read':['search','history','context','updates','reviews','search_people','search_files','get_file_text','get_file_occurrences','get_sync_status','get_file_segments','resolve_reference'], 'download':['attachment'], 'cursor-write':['ack'], 'send':['send_message','get_send_status']}
            if 'read' in p['permissions']:features['review-write']=['record_review']
            return {'service': 'anymsg', 'version': __version__, **dataset,
                'tool_manifest':{'version':__version__,'names':sorted(OPERATION_PERMISSIONS.keys()-{'download'}),
                    'discovery_action':'refresh_connection_if_tools_list_differs',
                    'rest_fallback':'POST /v1/{tool_name} with the same bearer and JSON parameters'},
                'principal': p['id'], 'scope_version': p['scope_version'], 'accounts': accounts,
                'credential_id':str(p['key_id']), 'permissions':p['permissions'],
                'chat_scope':{'all_in_permitted_accounts':p['chat_ids'] is None,'chat_ids':p['chat_ids']},
                'auth_mode': 'managed_bearer_keys',
                'search_options':{'filters':['accounts','chats','senders','from_time','to_time','direction'],
                    'query_modes':['all','phrase','web','exact'],'orders':['asc','desc','relevance'],
                    'languages':['auto','simple','russian','english','indonesian'],
                    'fuzzy':'explicit_bounded_fallback','compact':True,'optional_coverage':True,
                    'context':'independent_before_and_after_counts','chat_orders':['id','activity','last_activity_desc'],
                    'chat_order_default':'activity','awaiting_reply_default_days':14,
                    'awaiting_reply_bots':'exclude_observed_bot_senders_not_name_guesses',
                    'sync_status_details':['compact','full'],
                    'updates':'version_checked_filter_reset_preserves_ack_position',
                    'update_orders':['event','sent_at_within_delivered_batch'],
                    'updates_exclude_channels':True,'is_backfill':True,
                    'detail_default':'compact','details':['compact','full'],
                    'collector_operations':['connection','live_capture','history','media'],
                'connection_failures':'durable_terminal_gate_and_capped_retry','status_cache_seconds':10,'status_refresh':True,
                    'people_candidates':['transliteration','same_sender_grouping'],
                    'list_limit':100,'people_lookup':'observed_name_username_or_sender_id',
                    'indexed_content':['message_text','filename','bounded_file_text','configured_model_results'],
                    'model_processing':'optional_operator_configured_local_or_api',
                    'file_content_search':True,'file_indexing':'asynchronous_local_worker',
                    'file_search_sources':['extracted','enriched','message_context'],
                    'file_caption_provenance':'current_parent_message_revision_only',
                    'chat_coverage':True,'provider_id_lookup':'resolve_reference',
                    'empty_result_diagnostics':True,'message_conflicts':'scoped_active_and_historical'},
                'available': [feature for permission,names in features.items() if permission in p['permissions'] for feature in names],
                'sending':{'implemented':True,'approval_mode':'acting_agent_obtains_exact_owner_confirmation',
                    'server_approval_gate':False,'text_only':True,'request_id_required':True,
                    'ambiguous_result_policy':'unknown_no_automatic_retry',
                    'delivery_observations':{'telegram':'private_outbox_read_boundary',
                        'whatsapp':'message_ack_private_or_group_aggregate',
                        'provider_timestamps_available':False,'historical_coverage_guaranteed':False,
                        'event_kind':'message.status','changes_provider_read_state':False},
                    'read_permission_does_not_allow_send':True},
                'unavailable': ['guaranteed_full_history', 'drafts',
                                'owner_approval', 'project_memory', 'schedules', 'oauth'],
                'limits': {'page_size': 100, 'query_chars': 200,
                           'download_grant_seconds': 120, 'delivery_seconds': 900,
                           'review_sources':20, 'context_reviews':5,'send_text_utf16_units':4000,
                           'send_queue_ttl_seconds':600,'send_per_account_per_minute':60}}

    def send_message(self,token,account,chat_id,text,request_id,reply_to=''):
        from .outgoing import enqueue
        with self.authorized(token,'send_message') as (c,p):
            return enqueue(c,p,account,chat_id,text,request_id,reply_to)

    def get_send_status(self,token,send_id):
        from .outgoing import status
        with self.authorized(token,'get_send_status') as (c,p):
            return status(c,p,send_id)

    def list_chats(self, token, after='', limit=100, *, accounts=None,query='',chat_types=None,
                   order='id',exclude_channels=False,awaiting_reply=False,from_time='',to_time='',
                   include_coverage=True,awaiting_reply_days=14):
        from .chat_queries import list_chats
        with self.authorized(token,'list_chats') as (c,p):
            return list_chats(self,c,p,after,limit,accounts=accounts,query=query,chat_types=chat_types,
                order=order,exclude_channels=exclude_channels,awaiting_reply=awaiting_reply,
                from_time=from_time,to_time=to_time,include_coverage=include_coverage,
                awaiting_reply_days=awaiting_reply_days)

    def search_people(self,token,query='',*,accounts=None,chats=None,after='',limit=20,
                      transliterate=False,group_by_sender=False):
        from .people import search_people
        with self.authorized(token,'search_people') as (c,p):
            if type(transliterate) is not bool or type(group_by_sender) is not bool:
                raise APIError('invalid_filter')
            if transliterate or group_by_sender:
                from .people_candidates import search_people
                return search_people(self,c,p,query,accounts=accounts,chats=chats,after=after,limit=limit,
                    transliterate=transliterate,group_by_sender=group_by_sender)
            return search_people(self,c,p,query,accounts=accounts,chats=chats,after=after,limit=limit)

    def search_files(self,token,query='',account='',chat='',sender='',from_time='',to_time='',
                     after='',limit=20,*,accounts=None,chats=None,senders=None,query_mode='all',
                     order='asc',before='',direction='',filename='',search_language='auto',include_coverage=True):
        from .file_index import search_files
        with self.authorized(token,'search_files') as (c,p):
            return search_files(self,c,p,query,account,chat,sender,from_time,to_time,after,limit,
                accounts=accounts,chats=chats,senders=senders,query_mode=query_mode,order=order,
                before=before,direction=direction,filename=filename,search_language=search_language,
                include_coverage=include_coverage)

    def get_file_text(self,token,attachment_id,offset=0,limit=4000,content='extracted'):
        from .file_index import get_file_text
        with self.authorized(token,'get_file_text') as (c,p):
            return get_file_text(self,c,p,attachment_id,offset=offset,limit=limit,content=content)

    def get_file_occurrences(self,token,attachment_id,after='',limit=20):
        from .file_index import get_file_occurrences
        with self.authorized(token,'get_file_occurrences') as (c,p):
            return get_file_occurrences(self,c,p,attachment_id,after=after,limit=limit)

    def get_file_segments(self,token,attachment_id,offset=0,limit=100):
        from .file_index import get_file_segments
        with self.authorized(token,'get_file_segments') as (c,p):
            return get_file_segments(self,c,p,attachment_id,offset=offset,limit=limit)

    def get_sync_status(self,token,chat_id='',from_time='',to_time='',refresh=True):
        from .sync_status import get_sync_status
        with self.authorized(token,'get_sync_status') as (c,p):
            if type(refresh) is not bool:raise APIError('invalid_filter')
            if chat_id:
                from .chat_evidence import chat_statuses
                lower,upper=date_bound(from_time),date_bound(to_time)
                if lower and upper and lower>=upper:raise APIError('invalid_date_range')
                if not isinstance(chat_id,str) or len(chat_id)>256:raise APIError('invalid_filter')
                data=chat_statuses(c,p,[chat_id],lower,upper)
                if not data:raise APIError('not_found',404)
                targets(p,[chat_id]);return serial(data[chat_id])
            if from_time or to_time:raise APIError('chat_required')
            cache_key=(p['key_id'],p['scope_version'])
            now=time.monotonic()
            with self._status_cache_lock:
                cached=self._status_cache.get(cache_key)
                if not refresh and cached and now-cached[0]<10:
                    self._status_cache.move_to_end(cache_key)
                    result=json.loads(cached[1])
                    result['cache']={'hit':True,'age_seconds':round(now-cached[0],3),'max_age_seconds':10}
                    return result
            result=serial(get_sync_status(c,p))
            result['observed_at']=datetime.now(timezone.utc).isoformat()
            with self._status_cache_lock:
                self._status_cache[cache_key]=(time.monotonic(),json.dumps(result))
                self._status_cache.move_to_end(cache_key)
                if len(self._status_cache)>128:self._status_cache.popitem(last=False)
            result['cache']={'hit':False,'age_seconds':0,'max_age_seconds':10}
            return result

    def resolve_reference(self,token,account,provider_chat_id,provider_message_id=''):
        from .chat_evidence import resolve_chat,chat_statuses
        from .message_queries import enrich
        from .search_query import valid_text
        with self.authorized(token,'resolve_reference') as (c,p):
            chat=resolve_chat(c,p,account,provider_chat_id)
            valid_text(provider_message_id,256)
            result={'chat':chat,'sync':chat_statuses(c,p,[chat['id']])[chat['id']]}
            if provider_message_id:
                row=c.execute('SELECT id FROM messages WHERE account=%s AND chat=%s AND provider_id=%s',
                    (account,chat['id'],provider_message_id)).fetchone()
                if not row:raise APIError('not_found',404)
                message=self.message(c,p,row['id']);enrich(c,[message]);result['message']=message
            return serial(result)

    def search(self, token, query='', account='', chat='', sender='', from_time='',
               to_time='', after='', limit=20, *, accounts=None, chats=None, senders=None,
               query_mode='all', order='asc', before='', direction='', search_language='auto',
               fuzzy=False, compact=False, body_chars=None, include_coverage=True):
        with self.authorized(token,'search') as (c,p):
            return self._search(c,p,query,account,chat,sender,from_time,to_time,after,limit,
                accounts=accounts,chats=chats,senders=senders,query_mode=query_mode,
                order=order,before=before,direction=direction,search_language=search_language,
                fuzzy=fuzzy,compact=compact,body_chars=body_chars,include_coverage=include_coverage)

    def _search(self,c,p,*args,**kwargs):
        from .message_queries import search
        return search(self,c,p,*args,**kwargs)

    def get_messages(self, token, chat_id, after='', limit=20, *, before='', order='asc',
                     from_time='',to_time='',compact=False,body_chars=None,include_coverage=True):
        with self.authorized(token,'get_messages') as (c,p):
            if not isinstance(chat_id,str) or not 1<=len(chat_id)<=256:raise APIError('not_found',404)
            return self._search(c,p,chat=chat_id,after=after,limit=limit,before=before,
                order=order,from_time=from_time,to_time=to_time,compact=compact,
                body_chars=body_chars,include_coverage=include_coverage)

    def get_context(self, token, message_id, limit=10, *, before_count=None,after_count=None,
                    compact=False,body_chars=None,include_coverage=True):
        from .message_queries import enrich
        from .search_query import coverage,output_options,shape_messages
        with self.authorized(token,'get_context') as (c,p):
            bounded(limit,50)
            cap=output_options(compact,body_chars,include_coverage)
            explicit=before_count is not None or after_count is not None
            if explicit:
                before_count=5 if before_count is None else before_count
                after_count=5 if after_count is None else after_count
                if any(type(v) is not int or not 0<=v<=50 for v in (before_count,after_count)):
                    raise APIError('invalid_limit')
            anchor=self.message(c,p,message_id)
            scans=[]
            for op,sort,count in [('<','DESC',before_count if explicit else limit),
                                  ('>','ASC',after_count if explicit else limit)]:
                selected=c.execute('SELECT id,account,chat,sender,body,sent_at,revision,direction FROM messages '
                    'WHERE account=%s AND chat=%s AND NOT deleted AND (sent_at,id)'+op+'(%s,%s) '
                    'ORDER BY sent_at '+sort+',id '+sort+' LIMIT %s',
                    (anchor['account'],anchor['chat'],anchor['sent_at'],anchor['id'],count+1)).fetchall()
                scans.append((selected[:count],len(selected)>count))
            older,newer=scans[0][0],scans[1][0]
            center=[] if anchor['deleted'] else [{key:anchor[key] for key in
                ('id','account','chat','sender','body','sent_at','revision','direction')}]
            rows=older+center+newer
            if not explicit:
                rows=sorted(rows,key=lambda r:(r['id']!=anchor['id'],
                    abs((r['sent_at']-anchor['sent_at']).total_seconds())))[:limit]
            rows.sort(key=lambda r:(r['sent_at'],r['id']))
            enrich(c,rows);enrich(c,[anchor])
            shape_messages(rows,compact=compact,body_chars=cap)
            shape_messages([anchor],compact=compact,body_chars=cap)
            files=[f for f in anchor['attachments'] if f['download_status']=='ready']
            jobs=[f for f in anchor['attachments'] if f['download_status']!='ready']
            from .reviews import list_reviews
            reviews=list_reviews(c,p,message_id,limit=5)
            window={'older_cursor':rows[0]['id'] if rows else message_id,
                'newer_cursor':rows[-1]['id'] if rows else message_id}
            if explicit:
                window.update({'before_count':len(older),'after_count':len(newer),
                    'has_more_before':scans[0][1],'has_more_after':scans[1][1],
                    'requested_before':before_count,'requested_after':after_count,
                    'neighbors_coverage':'archived_messages_only'})
            from .chat_evidence import chat_statuses
            return serial({'message':anchor,'context':rows,'context_window':window,
                'attachments':files,'pending_attachments':jobs,'reviews':reviews,
                'chat_sync':chat_statuses(c,p,[anchor['chat']])[anchor['chat']] if include_coverage else None,
                **coverage(self,c,p,include_coverage)})

    def get_reviews(self, token, message_id, after='', limit=20):
        from .reviews import list_reviews
        with self.authorized(token,'get_reviews') as (c,p):
            self.message(c,p,message_id)
            return serial({**list_reviews(c,p,message_id,after,limit),**self.dataset(c,p)})

    def record_review(self, token, message_id, revision, result_ref, request_id, evidence=None):
        from .reviews import record
        with self.authorized(token,'record_review') as (c,p):
            return serial(record(c,p,message_id,revision,result_ref,request_id,evidence))

    def get_attachment(self, token, attachment_id):
        with self.authorized(token,'get_attachment') as (c, p):
            if not isinstance(attachment_id,str) or not 1<=len(attachment_id)<=256:raise APIError('not_found',404)
            where,args=scope_filter(p,'m')
            row = c.execute('''SELECT a.* FROM attachments a JOIN messages m ON m.id=a.message
                WHERE a.id=%s AND a.active AND NOT m.deleted AND '''+where,[attachment_id,*args]).fetchone()
            if not row:
                pending=c.execute('''SELECT j.id,j.state AS status,j.error_code,j.attempts,j.next_due FROM media_jobs j
                    JOIN messages m ON m.id=j.message WHERE j.id=%s AND '''+where+'''
                    AND m.account=j.account AND NOT m.deleted AND j.state NOT IN ('ready','superseded')''',
                    [attachment_id,*args]).fetchone()
                if pending:
                    from .status_details import describe
                    targets(p,[pending['id']]);return {**serial(pending),'error_detail':describe(pending['error_code']),
                        'download_status':'processing' if pending['status']=='leased' else pending['status'],
                        'extraction_status':'awaiting_download','enrichment_status':'awaiting_download',**self.dataset(c,p)}
                raise APIError('not_found', 404)
            grant = secrets.token_urlsafe(32)
            c.execute('DELETE FROM download_grants WHERE expires_at<now()')
            c.execute('''INSERT INTO download_grants VALUES (%s,%s,%s,%s,now()+interval '120 seconds')''',
                      (digest(grant), p['key_digest'], p['scope_version'], row['id']))
            targets(p,[row['id']])
            from .message_queries import enrich
            source=self.message(c,p,row['message']);enrich(c,[source])
            phases=next((f for f in source['attachments'] if f['id']==row['id']),{})
            return {**row, 'status': 'ready', 'sha256': row['digest'],
                    **{k:phases.get(k,'unknown') for k in ('download_status','extraction_status','enrichment_status')},
                    **self.dataset(c,p),
                    'download_url': f'{self.public_url}/v1/download/{grant}',
                    'download_expires_in': 120,
                    'notice': 'Temporary bearer link. Do not share or put it in public logs.'}

    def download(self, grant):
        def resolve(c):
            if not isinstance(grant,str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}',grant):raise APIError('not_found',404)
            row=c.execute('SELECT key_digest FROM download_grants WHERE digest=%s AND expires_at>clock_timestamp()',
                          (digest(grant),)).fetchone()
            if not row:raise APIError('not_found',404)
            p=self.identity_digest(c,row['key_digest'],missing='not_found',status=404)
            if 'download' not in p['permissions']:
                error=APIError('not_found',404);error.audit_identity=p;raise error
            return p
        with self.access('download',resolve) as (c,p):
            where,args=scope_filter(p,'m')
            row = c.execute('''SELECT a.* FROM download_grants g
                JOIN attachments a ON a.id=g.attachment JOIN messages m ON m.id=a.message
                WHERE g.digest=%s AND g.expires_at>clock_timestamp() AND g.key_digest=%s
                AND g.scope_version=%s AND a.active AND NOT m.deleted AND '''+where,
                [digest(grant),p['key_digest'],p['scope_version'],*args]).fetchone()
            if not row:
                raise APIError('not_found', 404)
            path = self.files/row['digest']
            if path.is_symlink() or not path.is_file() or path.stat().st_size != row['bytes']:
                raise APIError('attachment_unavailable', 503)
            targets(p,[row['id']])
            return row, path

    def get_updates(self, token, consumer, limit=20, *, accounts=None,chats=None,senders=None,
                    origins=None,kinds=None,direction='',from_time='',to_time='',compact=False,
                    body_chars=None,include_coverage=True,exclude_channels=False,order='event',
                    reset_filters=False,expected_ack_version=None):
        from .update_queries import get_updates
        with self.authorized(token,'get_updates') as (c,p):
            return get_updates(self,c,p,consumer,limit,accounts=accounts,chats=chats,senders=senders,
                origins=origins,kinds=kinds,direction=direction,from_time=from_time,to_time=to_time,
                compact=compact,body_chars=body_chars,include_coverage=include_coverage,
                exclude_channels=exclude_channels,order=order,reset_filters=reset_filters,
                expected_ack_version=expected_ack_version)

    def ack_updates(self, token, consumer, receipt, expected_version):
        with self.authorized(token,'ack_updates') as (c, p):
            if not isinstance(expected_version,int) or isinstance(expected_version,bool) or expected_version<0:
                raise APIError('invalid_version')
            if not isinstance(receipt,str) or len(receipt)>128:raise APIError('invalid_receipt')
            if not isinstance(consumer,str) or not re.fullmatch(r'[a-zA-Z0-9_.-]{1,64}',consumer):
                raise APIError('invalid_consumer')
            # Same lock order for GET and ACK; serialize concurrent ACKs of different receipts.
            state = c.execute('''SELECT * FROM consumers WHERE principal=%s AND name=%s AND scope_version=%s
                FOR UPDATE''', (p['id'], consumer, p['scope_version'])).fetchone()
            row = c.execute('''SELECT * FROM deliveries WHERE digest=%s AND principal=%s
                AND consumer=%s AND scope_version=%s AND expires_at>now()''',
                (digest(receipt), p['id'], consumer, p['scope_version'])).fetchone()
            if not state or not row or row['expected_version'] != expected_version:
                raise APIError('invalid_receipt', 409)
            if row['acked']:
                return {'acked': True, 'replayed': True, 'ack_version': state['version']}
            if state['version']!=expected_version or state['position']!=row['start_position']:
                raise APIError('stale_ack', 409)
            c.execute('''UPDATE consumers SET position=%s,version=version+1
                WHERE principal=%s AND name=%s AND scope_version=%s''',
                (row['end_position'], p['id'], consumer, p['scope_version']))
            c.execute('UPDATE deliveries SET acked=true WHERE digest=%s', (digest(receipt),))
            return {'acked': True, 'replayed': False, 'ack_version': expected_version+1}

    def migrate(self):
        with self.pool.connection() as c:
            c.execute('SELECT pg_advisory_xact_lock(74509137)')
            c.execute("SET LOCAL statement_timeout='180s'")
            c.execute(Path(__file__).with_name('schema.sql').read_text())
            version=int(c.execute("SELECT value FROM probe_metadata WHERE name='schema_version'").fetchone()['value'])
            if version>23: raise RuntimeError('database_newer_than_runtime')
            for target,name in [(2,'002_archive.sql'),(3,'003_collectors.sql'),(4,'004_whatsapp.sql'),(5,'005_access.sql'),(6,'006_reviews.sql'),(7,'007_search_indexes.sql'),(8,'008_people.sql'),(9,'009_file_index.sql'),(10,'010_search_audit.sql'),(11,'011_telegram_window.sql'),(12,'012_search.sql'),(13,'013_file_search.sql'),(14,'014_collector_health.sql'),(15,'015_enrichment.sql'),(16,'016_file_content.sql'),(17,'017_chat_evidence.sql'),(18,'018_collector_operations.sql'),(19,'019_whatsapp_identities.sql'),(20,'020_processing_admission.sql'),(21,'021_media_storage.sql'),(22,'022_outgoing.sql'),(23,'023_receipts.sql')]:
                migration=Path(__file__).with_name('migrations')/name
                sha=hashlib.sha256(migration.read_bytes()).hexdigest()
                if version<target:
                    c.execute(migration.read_text())
                    if target==17:
                        from .chat_evidence import backfill
                        backfill(c)
                    c.execute('INSERT INTO archive_migrations(version,sha256) VALUES(%s,%s)',(target,sha))
                    c.execute("UPDATE probe_metadata SET value=%s WHERE name='schema_version'",(str(target),))
                else:
                    applied=c.execute('SELECT sha256 FROM archive_migrations WHERE version=%s',(target,)).fetchone()
                    if not applied or applied['sha256']!=sha: raise RuntimeError('migration_checksum_mismatch')

    def issue_key(self, principal, accounts, days=7, *, chats=UNSET, permissions=UNSET):
        principal_id(principal)
        if type(days) is not int or not 1<=days<=30:
            raise ValueError('invalid_key_lifetime')
        token = 'pos_probe_'+secrets.token_urlsafe(32)
        with self.pool.connection() as c:
            lock_principal(c,principal)
            old = c.execute('SELECT * FROM principals WHERE id=%s FOR UPDATE', (principal,)).fetchone()
            if chats is UNSET:chats=old['chat_ids'] if old else None
            if permissions is UNSET:permissions=old['permissions'] if old else DEFAULT_PERMISSIONS
            accounts,chats,permissions=normalize_policy(c,accounts,chats,permissions)
            self._write_policy(c,principal,old,accounts,chats,permissions,old['enabled'] if old else True)
            issued=c.execute('INSERT INTO api_keys(digest,principal,expires_at) VALUES(%s,%s,%s) RETURNING id',
                (digest(token),principal,datetime.now(timezone.utc)+timedelta(days=days))).fetchone()
            audit(c,{'id':principal,'key_id':issued['id']},'issue_key','ok')
        return token

    @staticmethod
    def _write_policy(c,principal,old,accounts,chats,permissions,enabled):
        if old:
            changed=(set(old['accounts'])!=set(accounts) or old['chat_ids']!=chats or
                     set(old['permissions'])!=set(permissions) or old['enabled']!=enabled)
            if changed:
                c.execute('''UPDATE principals SET accounts=%s,chat_ids=%s,permissions=%s,enabled=%s,
                    scope_version=scope_version+1 WHERE id=%s''',(accounts,chats,permissions,enabled,principal))
        else:
            c.execute('INSERT INTO principals(id,accounts,chat_ids,permissions,enabled) VALUES(%s,%s,%s,%s,%s)',
                      (principal,accounts,chats,permissions,enabled))

    def set_access(self,principal,*,accounts=UNSET,chats=UNSET,permissions=UNSET,enabled=UNSET):
        principal_id(principal)
        with self.pool.connection() as c:
            lock_principal(c,principal)
            old=c.execute('SELECT * FROM principals WHERE id=%s FOR UPDATE',(principal,)).fetchone()
            if not old:raise ValueError('unknown_principal')
            if accounts is UNSET:accounts=old['accounts']
            if chats is UNSET:chats=old['chat_ids']
            if permissions is UNSET:permissions=old['permissions']
            if enabled is UNSET:enabled=old['enabled']
            if type(enabled) is not bool:raise ValueError('invalid_enabled')
            accounts,chats,permissions=normalize_policy(c,accounts,chats,permissions)
            self._write_policy(c,principal,old,accounts,chats,permissions,enabled)
            audit(c,{'id':principal},'set_access','ok')
            return serial(c.execute('SELECT * FROM principals WHERE id=%s',(principal,)).fetchone())

    def revoke(self, token=None, *, credential_id=None):
        if (token is None)==(credential_id is None):raise ValueError('one_key_selector_required')
        if token is not None:
            if not isinstance(token,str) or not 32<=len(token)<=256:raise ValueError('invalid_key')
            clause,selector='digest=%s',digest(token)
        else:clause,selector='id=%s',key_id(credential_id)
        with self.pool.connection() as c:
            row=c.execute('SELECT principal,id FROM api_keys WHERE '+clause,(selector,)).fetchone()
            if not row:return 0
            lock_principal(c,row['principal'])
            changed=c.execute('UPDATE api_keys SET revoked=true WHERE '+clause,(selector,)).rowcount
            audit(c,{'id':row['principal'],'key_id':row['id']},'revoke_key','ok')
            return changed

    def list_keys(self,principal):
        principal_id(principal)
        with self.pool.connection() as c:
            return serial(c.execute('SELECT id,principal,expires_at,revoked FROM api_keys WHERE principal=%s ORDER BY expires_at,id',
                                    (principal,)).fetchall())

    def audit_tail(self,principal='',limit=50):
        bounded(limit)
        if principal:principal_id(principal)
        with self.pool.connection() as c:
            return serial(c.execute('''SELECT * FROM audit_log WHERE (%s='' OR principal=%s)
                ORDER BY occurred_at DESC,id DESC LIMIT %s''',(principal,principal,limit)).fetchall())

    @staticmethod
    def append_event(c, account, message, kind='message.created', origin='fixture', event_key=None):
        from psycopg.types.json import Jsonb
        row=c.execute('''SELECT id AS message,account,chat,body,revision,sent_at,deleted,direction
            FROM messages WHERE id=%s AND account=%s''',(message,account)).fetchone()
        if not row: raise ValueError('invalid_event_message')
        value = c.execute('UPDATE feed_counter SET value=value+1 WHERE id=1 RETURNING value').fetchone()['value']
        c.execute('''INSERT INTO events(id,account,message,kind,revision,origin,snapshot,event_key)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s)''',
            (value,account,message,kind,row['revision'],origin,Jsonb(serial(row)),event_key))
        return value

    def seed(self):
        self.files.mkdir(mode=0o700, parents=True, exist_ok=True)
        content = b'PERSONAL OS SYNTHETIC PROBE\nFile integrity check: lighthouse-42\nNo personal data.\n'
        sha = hashlib.sha256(content).hexdigest()
        target = self.files/sha
        if not target.exists():
            temporary = self.files/('pending-'+secrets.token_hex(8))
            fd = os.open(temporary, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
            with os.fdopen(fd,'wb') as f:
                f.write(content); f.flush(); os.fsync(f.fileno())
            os.replace(temporary,target)
            fd = os.open(self.files, os.O_RDONLY)
            try: os.fsync(fd)
            finally: os.close(fd)
        if hashlib.sha256(target.read_bytes()).hexdigest()!=sha:
            raise ValueError('Fixture file corrupt')
        with self.pool.connection() as c:
            c.execute('SELECT value FROM feed_counter WHERE id=1 FOR UPDATE')
            for aid, platform in [('telegram-demo','telegram'),('whatsapp-demo-1','whatsapp'),('whatsapp-demo-2','whatsapp')]:
                c.execute('INSERT INTO accounts(id,platform,label) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING', (aid,platform,'Synthetic '+aid))
                c.execute('INSERT INTO chats VALUES(%s,%s,%s) ON CONFLICT DO NOTHING', (aid+'-chat',aid,'Synthetic chat'))
            for mid, aid, text, sender, day in [
                ('demo-tg-1','telegram-demo','Тестовый проект Маяк: встреча во вторник. Это вымышленные данные.','demo-person-1',1),
                ('demo-tg-2','telegram-demo','Файл lighthouse-42 приложен для проверки скачивания.','demo-person-2',2),
                ('demo-wa-1','whatsapp-demo-1','Synthetic project Atlas: budget review and planning.','demo-person-1',3),
                ('demo-private-1','whatsapp-demo-2','PRIVATE_SCOPE_SENTINEL synthetic hidden message.','demo-person-3',4)]:
                inserted = c.execute('''INSERT INTO messages(id,account,chat,provider_id,sender,body,sent_at)
                    VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id''',
                    (mid,aid,aid+'-chat',mid,sender,text,datetime(2026,10,day,12,tzinfo=timezone.utc))).fetchone()
                if inserted:
                    eid=self.append_event(c, aid, mid)
                    c.execute('''INSERT INTO message_revisions(message,revision,snapshot)
                        SELECT message,revision,snapshot FROM events WHERE id=%s''',(eid,))
            c.execute('INSERT INTO attachments(id,message,digest,filename,mime,bytes) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                      ('demo-file-1','demo-tg-2',sha,'probe-note.txt','text/plain',len(content)))
