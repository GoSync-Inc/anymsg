"""Opt-in text outbox. Only the existing fenced collector can dispatch it.

No server approval gate: the acting agent must obtain exact owner approval.
Ambiguous sends are terminal unknown, never automatically retried.
"""
import asyncio
import hashlib
import re

from .envelope import canonical, stable_id
from .policy import audit, lock_principal, targets
from .store import APIError, serial


class SendRejected(RuntimeError):
    """A local rejection before invoking the provider send operation."""


def valid_text(text):
    if not isinstance(text, str) or not text.strip() or any(
            ord(c)<32 and c not in '\n\r\t' or 0xD800<=ord(c)<=0xDFFF for c in text):
        raise APIError('invalid_message_text')
    if len(text.encode('utf-16-le'))//2>4000:
        raise APIError('invalid_message_text')


def permitted(p, account, chat):
    return account in p['accounts'] and (p['chat_ids'] is None or chat in p['chat_ids'])


def result(row,c=None):
    from .receipts import for_reference,unknown_evidence
    receipt=for_reference(c,row['account'],row['provider_chat'],row['provider_message_id']) \
        if c is not None and row['status']=='sent' and row['provider_message_id'] else unknown_evidence()
    return serial({
        'send_id':row['id'],'status':row['status'],'account':row['account'],
        'chat_id':row['chat'],'provider_chat_id':row['provider_chat'],
        'provider_message_id':row['provider_message_id'],
        'message_id':stable_id('msg',row['account'],row['provider_chat'],row['provider_message_id'])
            if row['provider_message_id'] else None,
        'created_at':row['created_at'],'expires_at':row['expires_at'],
        'finished_at':row['finished_at'],'error_code':row['error_code'],
        'agent_send':{'principal':row['principal'],'credential_id':row['key_id'],
            'request_id':row['request_id'],'approval_mode':'acting_agent',
            'owner_approval_verified_by_server':False},
        'delivery_receipt':receipt,
        'recipient_delivery_confirmed':receipt['recipient_delivery_confirmed'],'automatic_retry':False,
        'delivered_at':receipt['delivered_at'],'read_at':receipt['read_at'],'played_at':receipt['played_at'],
        'next_action':'get_send_status' if row['status'] in ('queued','dispatching') else
            'verify_history_before_any_new_send' if row['status']=='unknown' else 'none',
    })


def enqueue(c, p, account, chat_id, text, request_id, reply_to):
    valid_text(text)
    if not isinstance(request_id,str) or not re.fullmatch(r'[a-zA-Z0-9_.:-]{8,128}',request_id):
        raise APIError('invalid_request_id')
    if not all(isinstance(v,str) and 1<=len(v)<=256 for v in (account,chat_id)):
        raise APIError('not_found',404)
    if not permitted(p,account,chat_id):raise APIError('not_found',404)
    chat=c.execute('SELECT * FROM chats WHERE id=%s AND account=%s',(chat_id,account)).fetchone()
    if not chat or not chat['provider_chat']:raise APIError('not_found',404)
    reply_id=None
    if reply_to:
        source=c.execute('SELECT provider_id FROM messages WHERE id=%s AND chat=%s AND account=%s AND NOT deleted',
            (reply_to,chat_id,account)).fetchone()
        if not source:raise APIError('not_found',404)
        reply_id=source['provider_id']
    request_hash=hashlib.sha256(canonical([account,chat_id,text,reply_id])).hexdigest()
    c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('outgoing:'+p['id'],))
    existing=c.execute('SELECT * FROM outgoing_messages WHERE principal=%s AND request_id=%s',
        (p['id'],request_id)).fetchone()
    targets(p,[chat_id])
    if existing:
        if existing['request_hash']!=request_hash:raise APIError('idempotency_conflict',409)
        return result(existing,c)
    policy=c.execute('SELECT * FROM account_policies WHERE account=%s',(account,)).fetchone()
    if not policy or not policy['enabled'] or not policy['send_enabled']:
        raise APIError('sending_disabled',409)
    from .collector_state import CollectorState
    if not CollectorState.allows(policy,chat['provider_chat']):raise APIError('not_found',404)
    connected=c.execute('''SELECT l.expires_at>clock_timestamp() AS active,h.operations
        FROM collector_leases l JOIN collector_health h USING(account) WHERE l.account=%s''',
        (account,)).fetchone()
    if not connected or not connected['active'] or connected['operations'].get('connection',{}).get('state')!='connected':
        raise APIError('sender_not_ready',409)
    c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('outgoing-account:'+account,))
    count=c.execute("SELECT count(*) AS n FROM outgoing_messages WHERE account=%s AND created_at>clock_timestamp()-interval '1 minute'",
        (account,)).fetchone()['n']
    if count>=60:raise APIError('rate_limited',429)
    row=c.execute('''INSERT INTO outgoing_messages(principal,key_id,scope_version,request_id,request_hash,
        account,chat,provider_chat,body,reply_provider_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
        (p['id'],p['key_id'],p['scope_version'],request_id,request_hash,account,chat_id,chat['provider_chat'],text,reply_id)).fetchone()
    targets(p,[chat_id,row['id']])
    return result(row,c)


def status(c,p,send_id):
    if not isinstance(send_id,str) or not re.fullmatch(r'send_[a-f0-9]{32}',send_id):
        raise APIError('not_found',404)
    row=c.execute('SELECT * FROM outgoing_messages WHERE id=%s AND principal=%s',(send_id,p['id'])).fetchone()
    if not row or not permitted(p,row['account'],row['chat']):raise APIError('not_found',404)
    observed=c.execute('SELECT clock_timestamp() AS now').fetchone()['now']
    if row['status']=='queued' and row['expires_at']<=observed:
        row={**row,'status':'expired','error_code':'send_expired'}
    elif row['status']=='dispatching':
        lease=c.execute('SELECT epoch,expires_at FROM collector_leases WHERE account=%s',(row['account'],)).fetchone()
        if not lease or lease['epoch']!=row['lease_epoch'] or lease['expires_at']<=observed:
            row={**row,'status':'unknown','error_code':'sender_interrupted'}
    targets(p,[row['chat'],row['id']])
    return result(row,c)


class OutgoingWorker:
    def __init__(self,state,send,gate=None):
        self.state,self.send,self.gate=state,send,gate

    def claim(self):
        st=self.state
        with st.archive.store.pool.connection() as c:
            st.archive.fence(c,st.account,st.lease)
            c.execute("""UPDATE outgoing_messages SET status='unknown',error_code='sender_interrupted',finished_at=clock_timestamp()
                WHERE account=%s AND status='dispatching' AND lease_epoch IS DISTINCT FROM %s""",
                (st.account,st.lease[1]))
            row=c.execute("""SELECT * FROM outgoing_messages WHERE account=%s AND status='queued'
                ORDER BY created_at,id LIMIT 1 FOR UPDATE SKIP LOCKED""",(st.account,)).fetchone()
            if not row:return None
            lock_principal(c,row['principal'],shared=True)
            access=c.execute('''SELECT p.*,k.revoked,k.expires_at AS key_expires,clock_timestamp() AS observed
                FROM principals p JOIN api_keys k ON k.principal=p.id WHERE p.id=%s AND k.id=%s''',
                (row['principal'],row['key_id'])).fetchone()
            policy=c.execute('SELECT * FROM account_policies WHERE account=%s',(st.account,)).fetchone()
            from .collector_state import CollectorState
            reason=None
            observed=c.execute('SELECT clock_timestamp() AS now').fetchone()['now']
            if row['expires_at']<=observed:reason='send_expired'
            elif (not access or not access['enabled'] or access['revoked'] or
                    access['key_expires']<=access['observed'] or 'send' not in access['permissions'] or
                    row['scope_version']!=access['scope_version'] or not permitted(access,st.account,row['chat'])):
                reason='send_access_changed'
            elif not policy or not policy['enabled'] or not policy['send_enabled'] or not CollectorState.allows(policy,row['provider_chat']):
                reason='sending_disabled'
            if reason:
                c.execute("UPDATE outgoing_messages SET status=%s,error_code=%s,finished_at=clock_timestamp() WHERE id=%s",
                    ('expired' if reason=='send_expired' else 'cancelled',reason,row['id']))
                audit(c,{'id':row['principal'],'key_id':row['key_id']},'send_dispatch','denied',reason)
                return {'cancelled':True}
            c.execute("UPDATE outgoing_messages SET status='dispatching',started_at=clock_timestamp(),lease_epoch=%s WHERE id=%s",
                (st.lease[1],row['id']))
            audit(c,{'id':row['principal'],'key_id':row['key_id'],'_audit_targets':[row['id'],row['chat']]},'send_dispatch','ok')
            row['expires_at']=row['expires_at'].timestamp()
            return row

    def finish(self,job,status,provider_id=None,code=None):
        st=self.state
        with st.archive.store.pool.connection() as c:
            st.archive.fence(c,st.account,st.lease)
            c.execute('''UPDATE outgoing_messages SET status=%s,provider_message_id=%s,error_code=%s,
                finished_at=clock_timestamp() WHERE id=%s AND status='dispatching' AND lease_epoch=%s''',
                (status,provider_id,code,job['id'],st.lease[1]))

    async def once(self):
        if await asyncio.to_thread(self.state.cooldown_remaining)>0:return False
        if self.gate is not None:
            # Wait for the sending lane before claiming. WhatsApp background
            # history/media have a separate pipe and never hold this gate.
            async with self.gate:return await self._once()
        return await self._once()

    async def _once(self):
        job=await asyncio.to_thread(self.claim)
        if not job:return False
        if job.get('cancelled'):return True
        try:
            receipt=await asyncio.wait_for(self.send(job),30)
            provider_id=receipt.get('provider_message_id') if isinstance(receipt,dict) else None
            if not isinstance(provider_id,str) or not 1<=len(provider_id)<=256:
                raise RuntimeError('send_receipt_missing')
        except SendRejected as exc:
            code=str(exc) if str(exc) in ('send_expired','sending_disabled','send_target_invalid') else 'send_rejected'
            await asyncio.to_thread(self.finish,job,'expired' if code=='send_expired' else 'failed',None,code)
        except Exception:
            await asyncio.to_thread(self.finish,job,'unknown',None,'send_result_unknown')
        else:
            await asyncio.to_thread(self.finish,job,'sent',provider_id)
        return True
