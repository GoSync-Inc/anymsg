"""Single-owner Telegram collector. No sign-in, messages or secrets in gateway tools."""
import asyncio
from contextlib import suppress
from datetime import datetime,timezone
import errno
import json
import logging
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import time

from .adapters import telegram_message
from .archive import Archive
from .blobs import BlobWriter
from .collector_state import CaptureSpool,CollectorState,session_lock
from .store import Store
from .telegram_governor import ProviderRateLimited,RequestGovernor
from .operational import CONNECTION_ERRORS,MEDIA_ERRORS
from .storage_admission import MEDIA_RESERVE_BYTES


class ProviderUnavailable(Exception): pass


def private_json(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as source:
        info=os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode&0o077 or info.st_size>65536:
            raise ValueError('private_config_permissions_or_size')
        return json.load(source)


def configuration(path):
    cfg=private_json(path)
    fields={'enabled','account','credentials_file','session_file','permit_file','spool_directory',
            'batch_size','scan_interval_seconds','max_media_bytes','disk_reserve_bytes',
            'media_disk_reserve_bytes','max_dialogs','history_since'}
    if not isinstance(cfg,dict) or set(cfg)-fields: raise ValueError('invalid_collector_config')
    if cfg.get('enabled') is not True: raise RuntimeError('collector_disabled')
    if not re.fullmatch(r'[a-zA-Z0-9_.-]{1,64}',cfg.get('account','')): raise ValueError('invalid_account')
    for key in ['credentials_file','session_file','permit_file','spool_directory']:
        if not isinstance(cfg.get(key),str) or not Path(cfg[key]).is_absolute(): raise ValueError('invalid_private_path')
    if not cfg['session_file'].endswith('.session'): raise ValueError('invalid_session_filename')
    for key,default,lo,hi in [('batch_size',100,1,100),('scan_interval_seconds',60,10,3600),
                            ('max_media_bytes',512*1024**2,1,4*1024**3),
                            ('disk_reserve_bytes',1024**3,1024**3,100*1024**3)]:
        value=cfg.setdefault(key,default)
        if type(value) is not int or not lo<=value<=hi: raise ValueError('invalid_'+key)
    value=cfg.setdefault('media_disk_reserve_bytes',max(MEDIA_RESERVE_BYTES,cfg['disk_reserve_bytes']))
    if type(value) is not int or not cfg['disk_reserve_bytes']<=value<=100*1024**3:
        raise ValueError('invalid_media_disk_reserve_bytes')
    maximum=cfg.setdefault('max_dialogs',None)
    if maximum is not None and (type(maximum) is not int or not 1<=maximum<=10000):
        raise ValueError('invalid_max_dialogs')
    cfg['history_since']=history_bound(cfg.get('history_since'))
    auth=private_json(cfg['credentials_file']); permit=private_json(cfg['permit_file'])
    if not isinstance(auth,dict) or set(auth)!={'api_id','api_hash','expected_user_id'}:
        raise ValueError('invalid_telegram_credentials')
    if type(auth['api_id']) is not int or auth['api_id']<=0 or type(auth['expected_user_id']) is not int or auth['expected_user_id']<=0:
        raise ValueError('invalid_telegram_identity')
    if not isinstance(auth['api_hash'],str) or not re.fullmatch(r'[a-fA-F0-9]{32}',auth['api_hash']):
        raise ValueError('invalid_telegram_credentials')
    if not isinstance(permit,dict) or permit.get('account')!=cfg['account'] or permit.get('expected_user_id')!=auth['expected_user_id'] or permit.get('prior_clients_stopped') is not True:
        raise RuntimeError('session_handover_not_confirmed')
    return cfg,auth


def history_bound(value):
    if value is None: return None
    if not isinstance(value,str): raise ValueError('invalid_history_since')
    try: result=datetime.fromisoformat(value)
    except ValueError: raise ValueError('invalid_history_since') from None
    if result.tzinfo is None: raise ValueError('invalid_history_since')
    return result.astimezone(timezone.utc)


def peer_kind(message):
    peer=message.peer_id
    if getattr(peer,'channel_id',None) is not None: return 'channel'
    if getattr(peer,'chat_id',None) is not None: return 'chat'
    if getattr(peer,'user_id',None) is not None: return 'user'
    raise ValueError('unsupported_telegram_peer')


class TelethonProvider:
    def __init__(self,cfg,auth,*,receive_updates=True):
        from telethon import TelegramClient
        # Do not let library diagnostics log payloads or credentials.
        from .security_logging import redact_library_logs
        redact_library_logs()
        logger=logging.getLogger('personal_os.telegram.transport')
        self.client=TelegramClient(cfg['session_file'],auth['api_id'],auth['api_hash'],
            request_retries=2,connection_retries=3,auto_reconnect=True,
            flood_sleep_threshold=0,raise_last_call_error=True,base_logger=logger,
            catch_up=False,receive_updates=receive_updates)
        self.expected_user_id=auth['expected_user_id']
        self.governor=None

    def bind_state(self,state): self.governor=RequestGovernor(state)

    async def rpc(self,operation,phase):
        governor=getattr(self,'governor',None)
        return await governor.call(operation,phase) if governor else await operation()

    def subscribe(self,capture,deleted):
        from telethon import events
        self.client.add_event_handler(capture,events.NewMessage())
        self.client.add_event_handler(capture,events.MessageEdited())
        self.client.add_event_handler(deleted,events.MessageDeleted())

    async def connect(self):
        await self.rpc(self.client.connect,'provider_connect')
        # is_user_authorized() catches every RPCError, including FloodWait, and
        # caches False. get_me() catches only UnauthorizedError: required waits
        # must remain visible to the governor instead of asking for new pairing.
        me=await self.rpc(self.client.get_me,'provider_connect')
        if me is None:raise RuntimeError('telegram_pairing_required')
        if me.id!=self.expected_user_id: raise RuntimeError('telegram_identity_mismatch')
        await self.rpc(self.client.catch_up,'provider_connect')

    async def disconnect(self): await self.client.disconnect()

    async def wait_disconnected(self): await self.client.disconnected

    async def send_text(self,job):
        from .outgoing import SendRejected
        async def send():
            # Recheck after waiting for the shared rate/cooldown gate.
            if datetime.now(timezone.utc).timestamp()>=job['expires_at']:
                raise SendRejected('send_expired')
            message=await self.client.send_message(int(job['provider_chat']),job['body'],
                parse_mode=None,link_preview=False,
                reply_to=int(job['reply_provider_id']) if job.get('reply_provider_id') else None,
                clear_draft=False)
            if not message or str(message.chat_id)!=job['provider_chat']:
                raise RuntimeError('send_receipt_mismatch')
            return {'provider_message_id':str(message.id),'message':message}
        return await self.rpc(send,'provider_send')

    async def dialogs(self,limit):
        result=[]
        async for batch in self.dialog_batches(limit): result.extend(batch)
        return result

    async def dialog_batches(self,limit):
        # Telethon's pinned RequestIter keeps its next-page request unchanged when
        # _load_next_chunk raises FloodWait. Retry THIS iterator after the durable
        # pause, not a fresh traversal. Catalog batches already yielded stay saved.
        iterator=self.client.iter_dialogs(limit=limit).__aiter__()
        batch=[]
        while True:
            try:
                buffer=getattr(iterator,'buffer',None)
                if buffer is None or getattr(iterator,'index',0)==len(buffer):
                    dialog=await self.rpc(iterator.__anext__,'telegram_discovery')
                else:
                    # Consuming the already downloaded buffer makes no RPC.
                    dialog=await iterator.__anext__()
            except StopAsyncIteration:break
            except ProviderRateLimited:
                if batch:yield batch;batch=[]
                continue
            kind='channel' if dialog.is_channel else ('user' if dialog.is_user else 'chat')
            chat_type='private' if dialog.is_user else ('group' if dialog.is_group else 'channel')
            batch.append((str(dialog.id),kind,dialog.name or str(dialog.id),chat_type))
            if len(batch)>=100:yield batch;batch=[]
        if batch:yield batch

    async def page(self,chat,*,limit,min_id=0,before=0,reverse=False):
        async def read():
            return [m async for m in self.client.iter_messages(int(chat),limit=limit,min_id=min_id,
                                                              offset_id=before,reverse=reverse)]
        return await self.rpc(read,'telegram_history')

    async def chunks(self,source,descriptor):
        from telethon import types,utils
        from telethon.errors import FileReferenceExpiredError,FilerefUpgradeNeededError,FloodWaitError
        try:
            message=await self.rpc(lambda:self.client.get_messages(int(source['provider_chat']),
                ids=int(source['provider_id'])),'telegram_media')
            if not message: raise ProviderUnavailable('media_unavailable')
            current=telegram_message(source['account'],message)
            if descriptor not in current['media']: raise ProviderUnavailable('media_reference_changed')
            # A link preview exposes .document/.photo too, but its outer
            # MessageMediaWebPage is not a downloadable Telethon location.
            # Download the same concrete object that the adapter described.
            media=getattr(message,'document',None) or getattr(message,'photo',None)
            if media is None: raise ProviderUnavailable('media_unavailable')
            download_options={'request_size':512*1024}
            if isinstance(media,types.Photo):
                # File(Photo).size describes the heaviest representation, but
                # iter_download(Photo) blindly chooses sizes[-1]. Ordering is
                # not a guarantee of equal byte length. Keep the descriptor and
                # download the matching full representation explicitly.
                sizes=[s for s in media.sizes if isinstance(s,(types.PhotoSize,types.PhotoSizeProgressive))
                       and (descriptor['bytes'] or 0)>0 and utils._photo_size_byte_count(s)==descriptor['bytes']]
                if not sizes: raise ProviderUnavailable('media_unavailable')
                size=sizes[-1]
                location=types.InputPhotoFileLocation(id=media.id,access_hash=media.access_hash,
                    file_reference=media.file_reference,thumb_size=size.type)
                download_options.update(file_size=descriptor['bytes'],dc_id=media.dc_id)
            else: location=media
            stream=self.client.iter_download(location,**download_options)
            try:
                iterator=stream.__aiter__()
                while True:
                    try:chunk=await self.rpc(iterator.__anext__,'telegram_media')
                    except StopAsyncIteration:break
                    yield chunk
            finally: await stream.close()
        except FloodWaitError as exc: raise ProviderRateLimited(exc.seconds) from None
        except (FileReferenceExpiredError,FilerefUpgradeNeededError):
            # Even a freshly fetched preview can contain an expired reference.
            # Retry this job with a fresh message on the next bounded attempt;
            # one unavailable original must not stop every chat's collection.
            raise OSError('media_reference_expired') from None


class TelegramCollector:
    state_class=CollectorState
    coverage_code='telegram_history_partial'
    connect_timeout=60
    retry_connect=False
    def __init__(self,archive,account,provider,spool,*,batch_size=100,interval=60,max_dialogs=None,
                 writer=None,owner=None,lease_ttl=60,heartbeat_interval=15,history_since=None):
        self.archive,self.account,self.provider,self.spool=archive,account,provider,spool
        self.batch_size,self.interval,self.max_dialogs=batch_size,interval,max_dialogs
        self.history_since=history_since
        self.writer=writer or BlobWriter(archive.store.files,reserve_bytes=MEDIA_RESERVE_BYTES)
        self.owner=owner or 'telegram-'+secrets.token_hex(12)
        self.lease_ttl,self.heartbeat_interval=lease_ttl,heartbeat_interval
        self.state=None; self.stopping=asyncio.Event(); self.failed=asyncio.Event()
        self.capture_count=0; self.failure_code=None; self.policy=None
        self._dialog_cache=None; self._next_discovery=0

    def fail(self,code):
        self.failure_code=code; self.failed.set()

    async def capture(self,event):
        # Bound pending fsync work. Overload fails closed; reconciliation after
        # restart is required rather than claiming dropped callbacks succeeded.
        if self.stopping.is_set() or self.failed.is_set(): return
        if self.capture_count>=16: self.fail('capture_overload'); return
        self.capture_count+=1
        try:
            e=telegram_message(self.account,event.message)
            if not self.state.allows(self.policy,e['chat']): return
            await asyncio.to_thread(self.spool.put,{'kind':'message','peer_kind':peer_kind(event.message),'envelope':e,
                'captured_at':datetime.now(timezone.utc).isoformat()})
        except Exception: self.fail('durable_capture_failed')
        finally: self.capture_count-=1

    async def deleted(self,event):
        if self.stopping.is_set() or self.failed.is_set(): return
        if self.capture_count>=16: self.fail('capture_overload'); return
        self.capture_count+=1
        try:
            chat=str(event.chat_id) if event.chat_id is not None else ''
            if chat and not self.state.allows(self.policy,chat): return
            await asyncio.to_thread(self.spool.put,{'kind':'telegram_delete','account':self.account,
                'chat':chat,'ids':list(event.deleted_ids),'observed_at':datetime.now(timezone.utc).isoformat()})
        except Exception: self.fail('durable_capture_failed')
        finally: self.capture_count-=1

    async def commit_page(self,messages,chat,title,kind,origin):
        ids=[]
        for message in messages:
            if str(message.chat_id)!=chat: raise RuntimeError('provider_chat_mismatch')
            e=telegram_message(self.account,message,chat_title=title,origin=origin)
            item={'kind':'message','peer_kind':kind,'envelope':e}
            await asyncio.to_thread(self.spool.put,item)
            await asyncio.to_thread(self.state.apply,item)
            ids.append(message.id)
        return ids

    async def sync_chat(self,chat,kind,title):
        state=await asyncio.to_thread(self.state.chat,chat,kind)
        if not state['initialized']:
            recent=await self.provider.page(chat,limit=self.batch_size)
            kept=self.in_window(recent)
            ids=await self.commit_page(kept,chat,title,kind,'backfill')
            # Keep the cursor ABOVE skipped old messages so expanding the
            # window later does not lose the rest of this boundary page.
            boundary=min(ids) if ids else (max(m.id for m in recent)+1 if recent else None)
            await asyncio.to_thread(self.state.checkpoint,chat,high_water=max((m.id for m in recent),default=0),
                history_before=boundary,exhausted=len(recent)<self.batch_size and len(kept)==len(recent),
                initialized=True,window_complete=len(kept)<len(recent),head_complete=True)
            return
        new=await self.provider.page(chat,limit=self.batch_size,min_id=state['high_water'],reverse=True)
        await self.commit_page(self.in_window(new),chat,title,kind,'reconcile')
        if new: await asyncio.to_thread(self.state.checkpoint,chat,high_water=max(m.id for m in new))
        # Recent overlap catches edits after the library's update state advanced
        # ahead of durable capture. It cannot prove absence of older edits/deletes.
        recent=await self.provider.page(chat,limit=self.batch_size)
        await self.commit_page(self.in_window(recent),chat,title,kind,'reconcile')
        if len(new)<self.batch_size:
            await asyncio.to_thread(self.state.checkpoint,chat,head_complete=True)
        if not state['history_exhausted'] and not state['window_complete'] and state['history_before']:
            older=await self.provider.page(chat,limit=self.batch_size,before=state['history_before'])
            kept=self.in_window(older)
            ids=await self.commit_page(kept,chat,title,kind,'backfill')
            await asyncio.to_thread(self.state.checkpoint,chat,history_before=min(ids) if ids else None,
                                    exhausted=len(older)<self.batch_size and len(kept)==len(older),
                                    window_complete=len(kept)<len(older))

    def in_window(self,messages):
        return [m for m in messages if self.history_since is None or m.date>=self.history_since]

    async def sync_once(self):
        from telethon.errors import ChannelPrivateError,ChatAdminRequiredError,PeerIdInvalidError,FloodWaitError
        self.policy=await asyncio.to_thread(self.state.policy)
        if self._dialog_cache is None or time.monotonic()>=self._next_discovery:
            await asyncio.to_thread(self.state.status,'receiving','telegram_discovery')
            await asyncio.to_thread(self.state.discovery_started)
            dialogs=[]
            if hasattr(self.provider,'dialog_batches'):
                async for batch in self.provider.dialog_batches(self.max_dialogs):
                    await asyncio.to_thread(self.state.inventory,batch,self.history_since,discovery=True)
                    dialogs.extend(batch)
            else:
                # Small injected/test providers may expose only a single page.
                dialogs=await self.provider.dialogs(self.max_dialogs)
                await asyncio.to_thread(self.state.inventory,dialogs,self.history_since,discovery=True)
            await asyncio.to_thread(self.state.discovery_finished,
                                    self.max_dialogs is not None and len(dialogs)>=self.max_dialogs)
            self._dialog_cache=dialogs
            # Do not refetch an entire account catalog on every history pass.
            self._next_discovery=time.monotonic()+900
            print(json.dumps({'event':'telegram_discovery','dialogs':len(dialogs),
                'limit':self.max_dialogs,'history_since':self.history_since.isoformat() if self.history_since else None}),flush=True)
        else:dialogs=self._dialog_cache
        order=await asyncio.to_thread(self.state.inventory,dialogs,self.history_since)
        by_id={d[0]:d for d in dialogs}
        seen=set(); unavailable=False
        for chat in order:
            if chat not in by_id: continue
            chat,kind,title=by_id[chat][:3]
            if not self.state.allows(self.policy,chat): continue
            await asyncio.to_thread(self.state.attempted,chat)
            try:
                await self.sync_chat(chat,kind,title); seen.add(chat)
            except (ChannelPrivateError,ChatAdminRequiredError,PeerIdInvalidError):
                # A removed/private peer must not starve later allowed chats.
                # Rate limits, ownership loss and DB faults still propagate.
                unavailable=True
                await asyncio.to_thread(self.state.failed,chat,'provider_unavailable')
            except FloodWaitError:
                await asyncio.to_thread(self.state.failed,chat,'rate_limited')
                raise
        missing=set(self.policy['include_chats'])-set(self.policy['exclude_chats'])-seen
        code='dialog_discovery_limited' if self.max_dialogs is not None and len(dialogs)>=self.max_dialogs else 'telegram_history_partial'
        if missing or unavailable: code='configured_chat_unavailable'
        await asyncio.to_thread(self.state.status,'degraded' if missing or unavailable else 'receiving',code)

    async def reconcile_loop(self):
        from telethon.errors import FloodWaitError
        while True:
            try: await self.sync_once()
            except (FloodWaitError,ProviderRateLimited) as exc:
                if isinstance(exc,FloodWaitError):await asyncio.to_thread(self.state.defer,max(1,exc.seconds))
                await asyncio.to_thread(self.state.status,'degraded','rate_limited')
                governor=getattr(self.provider,'governor',None)
                if governor:await governor.wait()
                else:await asyncio.sleep(max(exc.seconds,1))
                continue
            await asyncio.sleep(self.interval)

    async def drain_loop(self):
        import psycopg
        from psycopg_pool import PoolTimeout
        while True:
            try:
                await self.drain_captures([self.spool])
                await asyncio.to_thread(self.state.apply_tombstones)
            except (psycopg.OperationalError,PoolTimeout):
                # Capture remains durable; the independent heartbeat stops the
                # provider if database ownership cannot be renewed in time.
                await asyncio.sleep(1)
            await asyncio.sleep(.25)

    async def drain_captures(self,spools):
        last=None;count=0
        def apply(item):
            nonlocal last,count
            self.state.apply(item);last=item;count+=1
        for spool in spools:await asyncio.to_thread(spool.drain,apply,100)
        if count or time.monotonic()>=getattr(self,'_next_capture_status',0):
            pending=await asyncio.to_thread(lambda:sum(sum(1 for _ in s.path.glob('*.json')) for s in spools))
            await asyncio.to_thread(self.state.capture_progress,last,count,min(20000,pending))
            self._next_capture_status=time.monotonic()+15

    async def heartbeat(self):
        while True:
            await asyncio.sleep(self.heartbeat_interval)
            await asyncio.to_thread(self.archive.renew,self.account,self.owner,self.state.lease[1],self.lease_ttl)
            self.policy=await asyncio.to_thread(self.state.policy)

    async def media_once(self):
        governor=getattr(self.provider,'governor',None)
        if governor:await governor.wait()
        job=await asyncio.to_thread(self.archive.claim_media,self.owner,account=self.account,
                                    collector_lease=self.state.lease,since=self.history_since,writer=self.writer)
        if not job: return False
        async def renew():
            while True:
                await asyncio.sleep(30)
                await asyncio.to_thread(self.archive.renew_media,job,collector_lease=self.state.lease)
        async def transfer():
            source=await asyncio.to_thread(self.archive.media_source,job,collector_lease=self.state.lease)
            stream=self.provider.chunks(source,job['descriptor'])
            try:
                return await self.writer.write_async(stream,expected_bytes=job['descriptor']['bytes'],
                    reservation=job.get('_storage_reservation'))
            finally:
                if hasattr(stream,'aclose'): await stream.aclose()
        renewal=asyncio.create_task(renew()); download=asyncio.create_task(transfer())
        try:
            done,_=await asyncio.wait([renewal,download],return_when=asyncio.FIRST_COMPLETED,timeout=3600)
            if not done: raise TimeoutError('media_timeout')
            if renewal in done: renewal.result()
            blob=await download
            await asyncio.to_thread(self.archive.finish_media,job,blob,self.writer,collector_lease=self.state.lease)
        except (ProviderUnavailable,ProviderRateLimited,OSError,ValueError,RuntimeError,TimeoutError) as exc:
            if str(exc)=='media_lease_lost': return True
            if str(exc)=='collector_lease_lost': raise
            if isinstance(exc,ProviderUnavailable): code,permanent='unavailable',True
            elif isinstance(exc,ProviderRateLimited): code,permanent='rate_limited',False
            elif str(exc)=='media_size_limit': code,permanent='size_limit',True
            elif str(exc)=='disk_reserve': code,permanent='disk_reserve',False
            elif isinstance(exc,OSError) and exc.errno in (errno.ENOSPC,errno.EDQUOT):
                code,permanent='disk_reserve',False
            elif str(exc) in ('media_size_mismatch','media_hash_mismatch','blob_corrupt'):
                code,permanent='integrity_error',False
            else: code,permanent='network_error',False
            if self.retry_connect and str(exc) in MEDIA_ERRORS:
                code=str(exc)
                permanent=code in ('media_unavailable','media_reference_changed','unsupported_media_type',
                    'media_size_limit','invalid_media_path')
            with suppress(RuntimeError):
                await asyncio.to_thread(self.archive.fail_media,job,code,permanent=permanent,
                    retry_after=exc.seconds if isinstance(exc,ProviderRateLimited) else None,collector_lease=self.state.lease)
        finally:
            renewal.cancel(); download.cancel()
            await asyncio.gather(renewal,download,return_exceptions=True)
            with suppress(RuntimeError,OSError):
                await asyncio.to_thread(self.archive.release_media_reservation,job)
        return True

    async def media_loop(self):
        while True:
            if not await self.media_once(): await asyncio.sleep(1)

    async def run(self):
        epoch=await asyncio.to_thread(self.archive.acquire,self.account,self.owner,self.lease_ttl)
        self.state=self.state_class(self.archive,self.account,(self.owner,epoch))
        tasks=[];failure=None
        try:
            self.policy=await asyncio.to_thread(self.state.policy)
            await asyncio.to_thread(self.state.status,'starting',self.coverage_code)
            if hasattr(self.provider,'bind_state'):self.provider.bind_state(self.state)
            self.provider.subscribe(self.capture,self.deleted)
            # Renew ownership and honor shutdown during a slow network connect.
            tasks=[asyncio.create_task(self.heartbeat()),asyncio.create_task(self.stopping.wait())]
            connecting=asyncio.create_task(self.connect_provider())
            tasks.append(connecting)
            done,_=await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
            for task in done: task.result()
            if self.stopping.is_set(): return
            if connecting not in done: raise RuntimeError('collector_lease_lost')
            tasks.remove(connecting)
            tasks.extend(asyncio.create_task(coro) for coro in
                [self.drain_loop(),self.reconcile_loop(),self.media_loop(),self.outgoing_loop(),
                 self.provider.wait_disconnected(),self.failed.wait()])
            done,_=await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
            for task in done: task.result()
            if self.failed.is_set(): raise RuntimeError(self.failure_code)
            if not self.stopping.is_set(): raise RuntimeError('provider_disconnected')
        except Exception as exc:
            failure=str(exc) if str(exc) in CONNECTION_ERRORS else 'provider_disconnected'
            with suppress(Exception):await asyncio.to_thread(self.state.connection_failed,failure)
            raise
        finally:
            self.stopping.set()
            for task in tasks: task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            with suppress(Exception): await asyncio.wait_for(self.provider.disconnect(),15)
            if failure is None:
                with suppress(Exception):
                    gate=await asyncio.to_thread(self.state.connection_gate)
                    if not gate['blocked'] and gate['remaining_seconds']<=0:
                        await asyncio.to_thread(self.state.phase,'connection','stopped','collector_shutdown')
                        await asyncio.to_thread(self.state.phase,'live_capture','stopped','collector_shutdown')
            with suppress(Exception): await asyncio.to_thread(self.state.status,'stopped','requires_reconciliation')
            with suppress(Exception): await asyncio.to_thread(self.archive.release,self.account,self.owner,epoch)

    async def outgoing_loop(self):
        from .outgoing import OutgoingWorker
        worker=OutgoingWorker(self.state,self.send_outgoing,getattr(self.provider,'send_gate',None))
        while True:
            active=await worker.once()
            await asyncio.sleep(.25 if active else 1)

    async def send_outgoing(self,job):
        receipt=await self.provider.send_text(job)
        # Send receipt is independent of archive commit. A capture failure must
        # never cause another send; reconciliation can recover the observation.
        try:
            from types import SimpleNamespace
            await self.capture(SimpleNamespace(message=receipt['message']))
        except Exception:
            self.fail('durable_capture_failed')
        return receipt

    async def connect_provider(self):
        # A persisted provider pause is not a hung connection: honor it outside
        # the network timeout while run() still watches heartbeat and shutdown.
        while True:
            gate=await asyncio.to_thread(self.state.connection_gate)
            if gate['blocked'] or gate['remaining_seconds']>0:
                try:await asyncio.wait_for(self.stopping.wait(),5 if gate['blocked'] else min(5,gate['remaining_seconds']))
                except TimeoutError:continue
                return
            governor=getattr(self.provider,'governor',None)
            if governor:await governor.wait()
            try:
                await asyncio.to_thread(self.state.phase,'connection','connecting','collector_starting')
                await asyncio.wait_for(self.provider.connect(),self.connect_timeout)
                await asyncio.to_thread(self.state.phase,'connection','connected','provider_ready')
                await asyncio.to_thread(self.state.phase,'live_capture','receiving','capture_ready')
                return
            except ProviderRateLimited:
                if not governor:raise
            except (RuntimeError,TimeoutError) as exc:
                code='provider_connect_timeout' if isinstance(exc,TimeoutError) else str(exc)
                if isinstance(exc,TimeoutError) and not self.retry_connect:raise TimeoutError(code) from None
                if code not in CONNECTION_ERRORS or code=='collector_lease_lost':raise
                await self.provider.disconnect()
                await asyncio.to_thread(self.state.connection_failed,code)


async def run_config(path):
    cfg,auth=configuration(path)
    session=Path(cfg['session_file'])
    if not session.is_file() or session.stat().st_mode&0o077: raise RuntimeError('private_session_required')
    # File ownership spans client construction, which opens the SQLite session.
    with session_lock(session):
        store=Store(Path(os.environ['DATABASE_URL_FILE']).read_text().strip(),
                    Path(os.environ['PROBE_FILES']),'http://collector.invalid')
        try:
            archive=Archive(store)
            with store.pool.connection() as c:
                row=c.execute('SELECT platform,synthetic FROM accounts WHERE id=%s',(cfg['account'],)).fetchone()
                if not row or row['platform']!='telegram' or row['synthetic']: raise RuntimeError('live_account_required')
            spool=CaptureSpool(cfg['spool_directory'],reserve_bytes=cfg['disk_reserve_bytes'])
            worker=TelegramCollector(archive,cfg['account'],TelethonProvider(cfg,auth),spool,
                batch_size=cfg['batch_size'],interval=cfg['scan_interval_seconds'],max_dialogs=cfg['max_dialogs'],
                history_since=cfg['history_since'],
                writer=BlobWriter(store.files,max_bytes=cfg['max_media_bytes'],reserve_bytes=cfg['media_disk_reserve_bytes']))
            loop=asyncio.get_running_loop()
            for sig in [signal.SIGTERM,signal.SIGINT]: loop.add_signal_handler(sig,worker.stopping.set)
            await worker.run()
        finally: store.close()


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    args=parser.parse_args()
    os.umask(0o077)
    try: asyncio.run(run_config(args.config))
    except Exception as exc:
        # Exception strings from providers can contain private requests. Emit
        # only this service's fixed error vocabulary to the supervisor.
        safe={'collector_disabled','session_handover_not_confirmed','telegram_pairing_required',
              'telegram_identity_mismatch','private_session_required','live_account_required',
              'collector_already_owned','session_already_owned','collector_lease_lost',
              'durable_capture_failed','capture_overload','collection_disabled','provider_disconnected'}
        code=str(exc) if str(exc) in safe else 'collector_failed'
        print(json.dumps({'status':'stopped','code':code}),flush=True)
        raise SystemExit(1) from None


if __name__=='__main__': main()
