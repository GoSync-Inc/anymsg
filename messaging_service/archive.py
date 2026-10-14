"""Durable ingestion and worker leases. Used only by operator/collector DB roles."""
from datetime import datetime
import hashlib
import re
import secrets

from psycopg.types.json import Jsonb

from .envelope import canonical, fingerprint, stable_id, validate
from .store import serial
from .incoming_formatting import known as known_formatting, unknown as unknown_formatting


class Archive:
    def __init__(self,store): self.store=store

    def set_sending(self,account,enabled):
        """Operator-only opt-in; never changes account discovery or agent scopes."""
        if type(enabled) is not bool:raise ValueError('invalid_send_policy')
        from .policy import audit
        with self.store.pool.connection() as c:
            changed=c.execute('UPDATE account_policies SET send_enabled=%s WHERE account=%s RETURNING account',
                (enabled,account)).fetchone()
            if not changed:raise ValueError('unknown_account')
            audit(c,None,'set_sending','ok')

    def register(self,account,platform,label,*,synthetic=True,include_chats=(),exclude_chats=(),discover=False):
        if not re.fullmatch(r'[a-zA-Z0-9_.-]{1,64}',account) or platform not in ('telegram','whatsapp'):
            raise ValueError('invalid_account')
        if not discover and not include_chats: raise ValueError('empty_collection_policy')
        with self.store.pool.connection() as c:
            c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('archive:'+account,))
            existing=c.execute('SELECT * FROM accounts WHERE id=%s FOR UPDATE',(account,)).fetchone()
            if existing and (existing['platform']!=platform or existing['synthetic']!=synthetic):
                raise ValueError('account_identity_change')
            c.execute('INSERT INTO accounts(id,platform,label,synthetic) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                      (account,platform,label,synthetic))
            c.execute('''INSERT INTO account_policies(account,enabled,include_chats,exclude_chats,discover_chats)
                VALUES(%s,true,%s,%s,%s) ON CONFLICT(account) DO UPDATE SET enabled=true,
                include_chats=excluded.include_chats,exclude_chats=excluded.exclude_chats,
                discover_chats=excluded.discover_chats''',(account,list(include_chats),list(exclude_chats),discover))
            c.execute('INSERT INTO source_status(account) VALUES(%s) ON CONFLICT DO NOTHING',(account,))

    def acquire(self,account,owner,ttl=60):
        if not 10<=ttl<=300 or not re.fullmatch(r'[a-zA-Z0-9_.-]{1,64}',owner):
            raise ValueError('invalid_lease')
        with self.store.pool.connection() as c:
            row=c.execute('''INSERT INTO collector_leases(account,owner,epoch,expires_at)
                VALUES(%s,%s,1,clock_timestamp()+%s*interval '1 second') ON CONFLICT(account)
                DO UPDATE SET owner=excluded.owner,epoch=collector_leases.epoch+1,expires_at=excluded.expires_at
                WHERE collector_leases.expires_at<clock_timestamp() RETURNING *''',(account,owner,ttl)).fetchone()
            if not row: raise RuntimeError('collector_already_owned')
            return row['epoch']

    def renew(self,account,owner,epoch,ttl=60):
        if not 10<=ttl<=300: raise ValueError('invalid_lease')
        with self.store.pool.connection() as c:
            changed=c.execute('''UPDATE collector_leases SET expires_at=clock_timestamp()+%s*interval '1 second'
                WHERE account=%s AND owner=%s AND epoch=%s AND expires_at>clock_timestamp()''',
                (ttl,account,owner,epoch)).rowcount
            if not changed: raise RuntimeError('collector_lease_lost')

    def release(self,account,owner,epoch):
        with self.store.pool.connection() as c:
            c.execute('''UPDATE collector_leases SET expires_at=clock_timestamp()
                WHERE account=%s AND owner=%s AND epoch=%s''',(account,owner,epoch))

    @staticmethod
    def fence(c,account,lease):
        if lease is None: return  # Trusted offline replay; never exposed via agent HTTP/MCP.
        owner,epoch=lease
        row=c.execute('''SELECT 1 FROM collector_leases WHERE account=%s AND owner=%s AND epoch=%s
            AND expires_at>clock_timestamp() FOR UPDATE''',(account,owner,epoch)).fetchone()
        if not row: raise RuntimeError('collector_lease_lost')

    def ingest(self,raw,*,lease=None):
        e=validate(raw); captured=e; receipt=hashlib.sha256(canonical(e)).hexdigest()
        aid=e['account']; cid=stable_id('chat',aid,e['chat']); mid=stable_id('msg',aid,e['chat'],e['provider_id'])
        with self.store.pool.connection() as c:
            # One bounded transaction per account; no provider/network/file work while holding it.
            c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('archive:'+aid,))
            self.fence(c,aid,lease)
            policy=c.execute('''SELECT p.*,a.platform FROM account_policies p JOIN accounts a ON a.id=p.account
                WHERE account=%s''',(aid,)).fetchone()
            if not policy or not policy['enabled'] or policy['platform']!=e['platform']:
                raise ValueError('collection_disabled')
            if e['chat'] in policy['exclude_chats'] or (not policy['discover_chats'] and e['chat'] not in policy['include_chats']):
                return {'outcome':'excluded'}
            # Delete events can arrive before history imports. A chat-less
            # Telegram deletion applies only to its non-channel ID namespace.
            tombstone=c.execute('''SELECT max(t.observed_at) AS at FROM provider_tombstones t
                WHERE t.account=%s AND t.provider_id=%s AND (t.provider_chat=%s OR
                (t.provider_chat='' AND EXISTS(SELECT 1 FROM collector_chats cc
                    WHERE cc.account=t.account AND cc.provider_chat=%s AND cc.peer_kind IN ('user','chat'))))''',
                (aid,e['provider_id'],e['chat'],e['chat'])).fetchone()['at']
            if tombstone and not e['deleted']:
                e={**e,'deleted':True,'body':'','media':[],
                   'updated_at':max(tombstone,datetime.fromisoformat(e['updated_at'])).isoformat()}
                if 'formatting' in e:e['formatting']=unknown_formatting()
            previous=c.execute('SELECT outcome FROM ingest_receipts WHERE digest=%s',(receipt,)).fetchone()
            if previous: return {'outcome':'duplicate','message':mid}
            chat_type=e.get('metadata',{}).get('chat_type')
            if chat_type=='unknown':chat_type=None
            c.execute('''INSERT INTO chats(id,account,title,provider_chat,chat_type) VALUES(%s,%s,%s,%s,%s)
                ON CONFLICT(id) DO UPDATE SET
                title=CASE WHEN excluded.title<>%s AND excluded.title<>'' THEN excluded.title ELSE chats.title END,
                provider_chat=excluded.provider_chat,chat_type=coalesce(excluded.chat_type,chats.chat_type)
                WHERE (excluded.title<>%s AND excluded.title<>'' AND chats.title<>excluded.title)
                    OR chats.provider_chat IS DISTINCT FROM excluded.provider_chat
                    OR (excluded.chat_type IS NOT NULL AND chats.chat_type IS DISTINCT FROM excluded.chat_type)''',
                      (cid,aid,e['chat_title'],e['chat'],chat_type,e['chat'],e['chat']))
            old=c.execute('SELECT * FROM messages WHERE id=%s FOR UPDATE',(mid,)).fetchone()
            content=fingerprint(e); updated=datetime.fromisoformat(e['updated_at']); outcome='created'
            old_format=None
            if old:
                retained=c.execute('SELECT snapshot FROM message_revisions WHERE message=%s AND revision=%s',
                    (mid,old['revision'])).fetchone()
                old_format=retained['snapshot'].get('formatting') if retained else None
            candidate_format=e.get('formatting')
            known_candidate=known_formatting(candidate_format);known_retained=known_formatting(old_format)
            formatting_changed=known_candidate and known_retained and candidate_format!=old_format
            formatting_completed=known_candidate and not known_retained
            sender_completed=bool(old and not old['sender'] and e['sender'] and not old['deleted']
                and not e['deleted'] and e['platform']=='telegram' and e['direction']=='outgoing'
                and e.get('metadata',{}).get('chat_type')=='private'
                and e.get('metadata',{}).get('sender_identity_basis')=='authenticated_self'
                and old['provider_updated_at']==updated
                and fingerprint({**e,'sender':''})==old['content_hash']
                and not formatting_changed)
            if old:
                if old['deleted'] and not e['deleted']: outcome='tombstoned'
                elif sender_completed:outcome='edited'
                elif (old['sent_at']!=datetime.fromisoformat(e['sent_at']) or old['direction']!=e['direction']
                      or old['sender']!=e['sender']): outcome='conflict'
                elif old['content_hash']==content and not (formatting_changed or formatting_completed): outcome='unchanged'
                elif old['provider_updated_at'] and updated<old['provider_updated_at']: outcome='stale'
                elif old['content_hash']==content and formatting_completed:outcome='edited'
                elif (old['provider_updated_at']==updated and not e['deleted']): outcome='conflict'
                else: outcome='deleted' if e['deleted'] else 'edited'
            elif e['deleted']: outcome='deleted'
            # An incomplete observation of the same retained provider body must
            # not erase known formatting, including authenticated sender repair.
            inherited_format=bool(old and known_retained and not known_candidate and not e['deleted']
                and (old['content_hash']==content or sender_completed))
            if inherited_format:e={**e,'formatting':old_format}
            if outcome in ('created','edited','deleted'):
                revision=old['revision']+1 if old else 1
                c.execute('''INSERT INTO messages(id,account,chat,provider_id,sender,body,sent_at,revision,
                    deleted,direction,provider_updated_at,content_hash) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(id) DO UPDATE SET sender=excluded.sender,body=excluded.body,revision=excluded.revision,
                    deleted=excluded.deleted,provider_updated_at=excluded.provider_updated_at,content_hash=excluded.content_hash''',
                    (mid,aid,cid,e['provider_id'],e['sender'],e['body'],e['sent_at'],revision,e['deleted'],
                     e['direction'],updated,content))
                snapshot={**e,'message':mid,'provider_chat':e['chat'],'chat':cid,'revision':revision}
                c.execute('INSERT INTO message_revisions(message,revision,snapshot) VALUES(%s,%s,%s)',
                          (mid,revision,Jsonb(snapshot)))
                media_ids=[]
                for descriptor in e['media']:
                    jid=stable_id('file',mid,hashlib.sha256(canonical(descriptor)).hexdigest())
                    media_ids.append(jid)
                    c.execute('''INSERT INTO media_jobs(id,account,message,descriptor) VALUES(%s,%s,%s,%s)
                        ON CONFLICT(id) DO UPDATE SET state=CASE WHEN media_jobs.state='superseded'
                        THEN 'queued' ELSE media_jobs.state END''',(jid,aid,mid,Jsonb(descriptor)))
                c.execute("UPDATE media_jobs SET state='superseded',lease_until=NULL WHERE message=%s AND NOT(id=ANY(%s))",
                          (mid,media_ids))
                # A formatting-only revision retains the same originals. Avoid
                # no-op active updates which otherwise enqueue extraction again.
                c.execute('''UPDATE attachments SET active=(id=ANY(%s)) WHERE message=%s
                    AND active IS DISTINCT FROM (id=ANY(%s))''',(media_ids,mid,media_ids))
                self.store.append_event(c,aid,mid,'message.'+outcome,e['origin'],receipt)
            elif outcome=='unchanged':
                c.execute('UPDATE messages SET provider_updated_at=greatest(provider_updated_at,%s) WHERE id=%s',(updated,mid))
            if outcome in ('created','edited','unchanged') and not e['deleted'] and e['sender']:
                # Identity hints come only from this message's provider payload,
                # never a cross-chat contact book. Unknown metadata stays unknown.
                profile=e.get('sender_profile',{})
                if profile.get('display_name') or profile.get('username'):
                    c.execute('''INSERT INTO sender_profiles(id,account,chat,sender,display_name,username,
                        source_message,source_sent_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT(account,chat,sender) DO UPDATE SET
                        display_name=excluded.display_name,username=excluded.username,
                        source_message=excluded.source_message,source_sent_at=excluded.source_sent_at,
                        observed_at=clock_timestamp()
                        WHERE excluded.source_sent_at>=sender_profiles.source_sent_at''',
                        (stable_id('person',aid,cid,e['sender']),aid,cid,e['sender'],
                         profile.get('display_name',''),profile.get('username',''),mid,e['sent_at']))
            if outcome in ('created','edited','unchanged') and not e['deleted'] and e.get('metadata'):
                # Observations do not rewrite immutable revisions or create content conflicts.
                c.execute('''INSERT INTO message_metadata(message,revision,metadata) SELECT id,revision,%s
                    FROM messages WHERE id=%s AND (provider_updated_at IS NULL OR provider_updated_at<=%s)
                    ON CONFLICT(message) DO UPDATE SET
                    revision=excluded.revision,
                    metadata=CASE WHEN message_metadata.revision=excluded.revision
                        THEN message_metadata.metadata || jsonb_strip_nulls(excluded.metadata)
                        ELSE excluded.metadata END,
                    observed_at=clock_timestamp()''',
                    (Jsonb(e['metadata']),mid,updated))
            c.execute('INSERT INTO ingest_receipts(digest,account,envelope,outcome) VALUES(%s,%s,%s,%s)',
                      (receipt,aid,Jsonb(captured),outcome))
            if outcome=='conflict':
                c.execute('''INSERT INTO message_conflicts(receipt_digest,message,observed_revision,
                    candidate_updated_at,observed_at) VALUES(%s,%s,%s,%s,clock_timestamp())''',
                    (receipt,mid,old['revision'],updated))
            elif outcome in ('edited','deleted','unchanged'):
                # A repeated copy of the retained revision does not resolve an
                # equal/newer conflicting observation. Only later evidence does.
                c.execute('''UPDATE message_conflicts mc SET resolved_at=clock_timestamp(),
                    resolved_revision=(SELECT revision FROM messages WHERE id=%s)
                    FROM ingest_receipts r WHERE r.digest=mc.receipt_digest
                    AND mc.message=%s AND mc.resolved_at IS NULL AND mc.candidate_updated_at<%s
                    AND (NOT %s OR coalesce(r.envelope->'formatting'->>'state','unknown')='unknown'
                        OR r.envelope->'formatting'=%s::jsonb)''',
                    (mid,mid,updated,inherited_format,Jsonb(old_format)))
                if sender_completed:
                    # Only competing copies of this exact content are resolved.
                    # Unrelated equal-version contradictions stay visible.
                    candidates=c.execute('''SELECT mc.receipt_digest,mc.candidate_updated_at,r.envelope FROM message_conflicts mc
                        JOIN ingest_receipts r ON r.digest=mc.receipt_digest
                        WHERE mc.message=%s AND mc.resolved_at IS NULL
                        ORDER BY mc.observed_at,mc.receipt_digest LIMIT 100''',(mid,)).fetchall()
                    equivalent=[r['receipt_digest'] for r in candidates
                        if r['candidate_updated_at']<=updated
                        and fingerprint(validate(r['envelope'])) in (content,old['content_hash'])
                        and (not known_formatting(r['envelope'].get('formatting'))
                            or not known_formatting(e.get('formatting'))
                            or r['envelope']['formatting']==e['formatting'])]
                    if equivalent:c.execute('''UPDATE message_conflicts SET resolved_at=clock_timestamp(),
                        resolved_revision=%s WHERE message=%s AND receipt_digest=ANY(%s) AND resolved_at IS NULL''',
                        (revision,mid,equivalent))
            c.execute("""UPDATE source_status SET last_ingest_at=clock_timestamp(),status='receiving',
                detail_code=CASE WHEN detail_code='version_conflict' THEN detail_code ELSE %s END WHERE account=%s""",
                      ('version_conflict' if outcome=='conflict' else 'coverage_not_reconciled',aid))
            return {'outcome':outcome,'message':mid}

    def claim_media(self,owner,*,account=None,ttl=120,collector_lease=None,since=None,writer=None):
        from .storage_admission import StorageAdmission
        if not re.fullmatch(r'[a-zA-Z0-9_.-]{1,64}',owner) or not 10<=ttl<=600: raise ValueError('invalid_worker')
        with self.store.pool.connection() as c:
            StorageAdmission.lock_and_reap(c)
            if collector_lease is not None:
                if account is None: raise ValueError('collector_account_required')
                self.fence(c,account,collector_lease)
            c.execute("""UPDATE media_jobs SET state='failed',error_code='retry_exhausted',lease_until=NULL
                WHERE state='leased' AND attempts>=5 AND lease_until<clock_timestamp()
                AND (%s::text IS NULL OR account=%s)""",(account,account))
            job=c.execute('''SELECT j.* FROM media_jobs j JOIN account_policies p ON p.account=j.account
                JOIN messages m ON m.id=j.message AND m.account=j.account
                JOIN chats ch ON ch.id=m.chat AND ch.account=j.account
                WHERE p.enabled AND (%s::text IS NULL OR j.account=%s) AND j.attempts<5 AND
                NOT m.deleted AND NOT(ch.provider_chat=ANY(p.exclude_chats)) AND
                (p.discover_chats OR ch.provider_chat=ANY(p.include_chats)) AND
                (%s::timestamptz IS NULL OR m.sent_at>=%s) AND
                ((j.state='queued' AND j.next_due<=clock_timestamp()) OR
                 (j.state='leased' AND j.lease_until<clock_timestamp()))
                ORDER BY j.next_due,j.id FOR UPDATE OF j SKIP LOCKED LIMIT 1''',(account,account,since,since)).fetchone()
            if not job: return None
            expires=c.execute("SELECT clock_timestamp()+%s*interval '1 second' AS at",(ttl,)).fetchone()['at']
            reservation=None
            if writer is not None:
                try:reservation=StorageAdmission(writer).reserve(c,job,expires)
                except ValueError as exc:
                    if str(exc)!='media_size_limit':raise
                    c.execute("UPDATE media_jobs SET state='failed',error_code='size_limit',lease_until=NULL WHERE id=%s",
                        (job['id'],))
                    return None
                if reservation is None:
                    c.execute('''UPDATE media_jobs SET state='queued',error_code='paused_storage',lease_until=NULL,
                        next_due=clock_timestamp()+interval '60 seconds' WHERE id=%s''',(job['id'],))
                    return None
            claimed=c.execute('''UPDATE media_jobs SET state='leased',attempts=attempts+1,lease_owner=%s,
                lease_epoch=lease_epoch+1,lease_until=%s,error_code=NULL
                WHERE id=%s RETURNING *''',(owner,expires,job['id'])).fetchone()
            if reservation is not None:claimed['_storage_reservation']=reservation
            return claimed

    @staticmethod
    def job_fence(c,job):
        row=c.execute('''SELECT j.* FROM media_jobs j JOIN messages m ON m.id=j.message AND m.account=j.account
            JOIN chats ch ON ch.id=m.chat AND ch.account=j.account
            JOIN account_policies p ON p.account=j.account
            WHERE j.id=%s AND j.state='leased' AND j.lease_owner=%s
            AND j.lease_epoch=%s AND j.lease_until>clock_timestamp() AND NOT m.deleted AND p.enabled
            AND NOT(ch.provider_chat=ANY(p.exclude_chats))
            AND (p.discover_chats OR ch.provider_chat=ANY(p.include_chats)) FOR UPDATE OF j''',
            (job['id'],job['lease_owner'],job['lease_epoch'])).fetchone()
        if not row: raise RuntimeError('media_lease_lost')
        return row

    def renew_media(self,job,ttl=120,*,collector_lease=None):
        from .storage_admission import StorageAdmission
        if not 10<=ttl<=600: raise ValueError('invalid_lease')
        with self.store.pool.connection() as c:
            # Same lock order as admission: a concurrent reaper must not see
            # half of a renewed job/reservation pair and reclaim its capacity.
            StorageAdmission.lock(c)
            self.fence(c,job['account'],collector_lease)
            self.job_fence(c,job)
            row=c.execute("UPDATE media_jobs SET lease_until=clock_timestamp()+%s*interval '1 second' WHERE id=%s RETURNING lease_until",
                      (ttl,job['id'])).fetchone()
            changed=c.execute('''UPDATE media_reservations SET expires_at=%s WHERE job=%s AND lease_epoch=%s''',
                (row['lease_until'],job['id'],job['lease_epoch'])).rowcount
            if job.get('_storage_reservation') is not None and changed!=1:
                raise RuntimeError('media_lease_lost')
        if job.get('_storage_reservation') is not None:
            job['_storage_reservation'].expires_at=row['lease_until']

    def media_source(self,job,*,collector_lease=None):
        with self.store.pool.connection() as c:
            self.fence(c,job['account'],collector_lease)
            self.job_fence(c,job)
            row=c.execute('''SELECT r.snapshot FROM messages m JOIN message_revisions r
                ON r.message=m.id AND r.revision=m.revision WHERE m.id=%s AND NOT m.deleted''',
                (job['message'],)).fetchone()
            if not row: raise RuntimeError('media_lease_lost')
            return row['snapshot']

    def finish_media(self,job,blob,writer,*,collector_lease=None):
        writer.verify(blob)  # Before DB ready; hashes immutable bytes, never a remote URL.
        with self.store.pool.connection() as c:
            # Same account lock as message edits: no stale file publication after edit/delete.
            c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('archive:'+job['account'],))
            self.fence(c,job['account'],collector_lease)
            current=self.job_fence(c,job); d=current['descriptor']
            if d['bytes'] is not None and blob.bytes!=d['bytes']: raise ValueError('media_size_mismatch')
            c.execute('''INSERT INTO attachments(id,message,digest,filename,mime,bytes,active)
                VALUES(%s,%s,%s,%s,%s,%s,true) ON CONFLICT(id) DO UPDATE SET
                digest=excluded.digest,bytes=excluded.bytes,active=true''',
                (job['id'],job['message'],blob.digest,d['filename'],d['mime'],blob.bytes))
            c.execute("UPDATE media_jobs SET state='ready',error_code=NULL,lease_until=NULL WHERE id=%s",(job['id'],))
            origin=c.execute('''SELECT r.snapshot->>'origin' AS origin FROM messages m
                JOIN message_revisions r ON r.message=m.id AND r.revision=m.revision WHERE m.id=%s''',
                (job['message'],)).fetchone()['origin'] or 'reconcile'
            self.store.append_event(c,job['account'],job['message'],'attachment.ready',origin,
                                    stable_id('event',job['id'],blob.digest,str(job['lease_epoch'])))
            from .storage_admission import StorageAdmission
            StorageAdmission.release(c,job)

    def release_media_reservation(self,job):
        """Idempotent cleanup even after an edit/delete invalidates job ownership."""
        from .storage_admission import StorageAdmission
        with self.store.pool.connection() as c:StorageAdmission.release(c,job)

    def fail_media(self,job,code,*,permanent=False,retry_after=None,collector_lease=None):
        from .operational import MEDIA_ERRORS
        allowed={'unavailable','size_limit','rate_limited','network_error','disk_reserve','paused_storage','integrity_error'}
        if code not in allowed|MEDIA_ERRORS: raise ValueError('invalid_error_code')
        if retry_after is not None and (type(retry_after) is not int or not 1<=retry_after<2**31):
            raise ValueError('invalid_retry_delay')
        with self.store.pool.connection() as c:
            self.fence(c,job['account'],collector_lease)
            row=self.job_fence(c,job)
            local_pause=code in ('disk_reserve','paused_storage')
            attempts=max(0,row['attempts']-1) if local_pause else row['attempts']
            terminal=not local_pause and (permanent or attempts>=5)
            c.execute('''UPDATE media_jobs SET state=%s,error_code=%s,lease_until=NULL,attempts=%s,
                next_due=clock_timestamp()+%s*interval '1 second' WHERE id=%s''',
                ('failed' if terminal else 'queued','paused_storage' if local_pause else code,attempts,
                 60 if local_pause else max(min(3600,10*2**attempts),retry_after or 0),job['id']))
            from .storage_admission import StorageAdmission
            StorageAdmission.release(c,job)
