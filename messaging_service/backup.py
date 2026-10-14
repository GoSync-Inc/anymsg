"""Operator-only consistent PG dump + referenced immutable blobs and verification.

pg_dump receives an exported snapshot from the same transaction used for the
manifest. Never mount this operator capability into the public gateway.
"""
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from psycopg import sql

from .blobs import Blob,BlobWriter
from .envelope import canonical
from .spool import fsync_dir
from .store import serial

# Explicit tables and deterministic ordering; no SQL/identifiers supplied by agents.
TABLES={'probe_metadata':'name','archive_migrations':'version','accounts':'id','principals':'id',
    'api_keys':'digest','chats':'id','messages':'id','attachments':'id','feed_counter':'id',
    'events':'id','consumers':'principal,name,scope_version','deliveries':'digest','download_grants':'digest',
    'account_policies':'account','collector_leases':'account','source_status':'account',
    'ingest_receipts':'digest','message_revisions':'message,revision','media_jobs':'id'}
V3_TABLES={'collector_chats':'account,provider_chat','provider_tombstones':'account,provider_chat,provider_id'}
V4_TABLES={'whatsapp_checkpoints':'account,provider_chat'}
V5_TABLES={'audit_log':'occurred_at,id'}
V6_TABLES={'review_counter':'id','reviews':'position','review_sources':'review,message'}
V8_TABLES={'sender_profiles':'id'}
V9_TABLES={'attachment_text':'attachment'}
V14_TABLES={'collector_health':'account'}
V16_TABLES={'attachment_text_chunks':'attachment,chunk_index','attachment_enrichment_chunks':'attachment,chunk_index'}
V18_TABLES={'processing_profiles':'account,kind'}
V19_TABLES={'whatsapp_identity_observations':'account,source_message,identity_id,pn,lid,provenance'}
V20_TABLES={'processing_budget':'day,account,kind,profile_hash',
           'processing_control':'account,kind,profile_hash','processing_resource_leases':'resource'}
V21_TABLES={'media_reservations':'job'}
V22_TABLES={'outgoing_messages':'id'}
V23_TABLES={'message_receipts':'id','receipt_event_jobs':'receipt','receipt_event_publications':'message'}
V17_TABLES={'message_conflicts':'receipt_digest'}
V15_TABLES={'message_metadata':'message','attachment_enrichment':'attachment','enrichment_budget':'day'}


def database_manifest(c):
    tables={}
    version=int(c.execute("SELECT value FROM probe_metadata WHERE name='schema_version'").fetchone()['value'])
    if version not in range(2,24): raise ValueError('unsupported_backup_schema')
    for name,order in {**TABLES,**(V3_TABLES if version>=3 else {}),**(V4_TABLES if version>=4 else {}),
                       **(V5_TABLES if version>=5 else {}),**(V6_TABLES if version>=6 else {}),
                       **(V8_TABLES if version>=8 else {}),**(V9_TABLES if version>=9 else {}),
                       **(V14_TABLES if version>=14 else {}),**(V15_TABLES if version>=15 else {}),**(V16_TABLES if version>=16 else {}),**(V17_TABLES if version>=17 else {}),**(V18_TABLES if version>=18 else {}),
                       **(V19_TABLES if version>=19 else {}),**(V20_TABLES if version>=20 else {}),
                       **(V21_TABLES if version>=21 else {}),**(V22_TABLES if version>=22 else {}),
                       **(V23_TABLES if version>=23 else {})}.items():
        query=sql.SQL('SELECT row_to_json(t) AS record FROM {} t ORDER BY {}').format(
            sql.Identifier(name),sql.SQL(',').join(sql.Identifier(part) for part in order.split(',')))
        sha=hashlib.sha256(); count=0
        with c.cursor(name='backup_'+name) as cursor:
            cursor.execute(query)
            for row in cursor:
                sha.update(canonical(row['record'])+b'\n'); count+=1
        tables[name]={'rows':count,'sha256':sha.hexdigest()}
    return tables


def create_snapshot(store,destination,dump_command,recovery_config=None,*,captured_callback=None,reference_blobs=False):
    """Release cold-state locks after the DB dump; immutable blob copying may continue."""
    from contextlib import nullcontext
    from .recovery_state import recovery_guard
    guard=recovery_guard(store,recovery_config,destination) if recovery_config is not None else nullcontext(None)
    destination=Path(destination)
    if type(reference_blobs) is not bool:raise ValueError('invalid_blob_reference_mode')
    if destination.exists(): raise ValueError('backup_destination_exists')
    if not dump_command or not all(isinstance(x,str) for x in dump_command): raise ValueError('invalid_dump_command')
    destination.parent.mkdir(parents=True,exist_ok=True)
    temporary=Path(tempfile.mkdtemp(prefix='.backup-',dir=destination.parent))
    try:
        recovery=None
        with guard as recovery_sources:
            if recovery_sources is not None:
                from .recovery_state import snapshot_sources
                recovery=snapshot_sources(recovery_sources,temporary/'recovery')
            with store.pool.connection() as c:
                c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                c.execute('SET LOCAL statement_timeout=0')
                exported=c.execute('SELECT pg_export_snapshot() AS id,clock_timestamp() AS captured_at').fetchone()
                snapshot=exported['id'];captured_at=exported['captured_at'].isoformat()
                if recovery is not None:
                    from .recovery_state import verify_account_coverage
                    verify_account_coverage(c,{s['account'] for s in recovery['sources'] if s['kind']=='session'})
                tables=database_manifest(c)
                schema_version=int(c.execute("SELECT value FROM probe_metadata WHERE name='schema_version'").fetchone()['value'])
                blobs=c.execute('SELECT DISTINCT digest,bytes FROM attachments ORDER BY digest').fetchall()
                with (temporary/'database.dump').open('xb') as output:
                    # Command is configured by the operator, not accepted by MCP/REST. No shell.
                    process=subprocess.run([*dump_command,'--format=custom','--no-owner','--no-acl','--snapshot='+snapshot],
                        stdout=output,stderr=subprocess.PIPE,timeout=600)
                    if process.returncode: raise RuntimeError('pg_dump_failed')
                    output.flush(); os.fsync(output.fileno())
        if captured_callback is not None:captured_callback()
        target=temporary/'files'; target.mkdir(mode=0o700)
        source=BlobWriter(store.files,reserve_bytes=0)
        total=sum(b['bytes'] for b in blobs)
        if shutil.disk_usage(temporary).free-(0 if reference_blobs else total)<1024**3: raise RuntimeError('backup_disk_reserve')
        for item in blobs:
            original=source.verify(Blob(item['digest'],item['bytes']))
            if not reference_blobs:
                with original.open('rb') as src, (target/item['digest']).open('xb') as dst:
                    shutil.copyfileobj(src,dst,1024**2); dst.flush(); os.fsync(dst.fileno())
        fsync_dir(target)
        with (temporary/'database.dump').open('rb') as dump:
            dump_sha=hashlib.file_digest(dump,'sha256').hexdigest()
        manifest={'format':2 if recovery is not None else 1,'created_at':datetime.now(timezone.utc).isoformat(),
            'captured_at':captured_at,
            'schema_version':schema_version,'tables':tables,'blobs':blobs,'dump_sha256':dump_sha,
            'includes_provider_sessions':bool(recovery and any(s['kind']=='session' for s in recovery['sources'])),
            'restore_mode':'verify_then_quarantine_collectors_no_sends'}
        if recovery is not None: manifest['recovery']=recovery
        with (temporary/'manifest.json').open('x') as out:
            out.write(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n'); out.flush(); os.fsync(out.fileno())
        fsync_dir(temporary); os.rename(temporary,destination); fsync_dir(destination.parent)
        return {'status':'snapshot_created','tables':len(tables),'blobs':len(blobs),'blob_bytes':total}
    finally:
        if temporary.exists(): shutil.rmtree(temporary)  # Only this function's temporary directory.


def verify_bundle(directory,*,blob_root=None):
    from .recovery_state import read_manifest,verify_recovery
    directory=Path(directory); manifest=read_manifest(directory)
    if manifest.get('format') not in (1,2) or manifest.get('schema_version') not in range(2,24): raise ValueError('unsupported_backup')
    verify_recovery(directory,manifest)
    if (directory/'database.dump').is_symlink(): raise ValueError('invalid_backup_file')
    with (directory/'database.dump').open('rb') as dump:
        if hashlib.file_digest(dump,'sha256').hexdigest()!=manifest['dump_sha256']: raise RuntimeError('dump_corrupt')
    if (directory/'files').is_symlink(): raise ValueError('invalid_backup_file')
    writer=BlobWriter(blob_root if blob_root is not None else directory/'files',reserve_bytes=0); seen=set()
    if not isinstance(manifest['blobs'],list) or len(manifest['blobs'])>1000000:raise ValueError('invalid_backup_manifest')
    for item in manifest['blobs']:
        if (not isinstance(item,dict) or set(item)!={'digest','bytes'} or type(item['bytes']) is not int or item['bytes']<0 or
            not isinstance(item['digest'],str) or not re.fullmatch(r'[a-f0-9]{64}',item['digest']) or item['digest'] in seen):
            raise ValueError('invalid_backup_manifest')
        writer.verify(Blob(item['digest'],item['bytes'])); seen.add(item['digest'])
    # Reference capture is an operator workspace, not a self-contained backup.
    # Restore accepts only a bundle containing its exact complete original set.
    expected=set() if blob_root is not None else seen
    if {p.name for p in (directory/'files').iterdir()}!=expected: raise ValueError('backup_blob_set_mismatch')
    return manifest


def verify_snapshot(store,directory):
    directory=Path(directory); manifest=verify_bundle(directory)
    with store.pool.connection() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        tables=database_manifest(c)
    if tables!=manifest['tables']: raise RuntimeError('restored_database_mismatch')
    restored=BlobWriter(store.files,reserve_bytes=0)
    for item in manifest['blobs']:
        restored.verify(Blob(item['digest'],item['bytes']))
    return {'status':'verified','tables':len(tables),'blobs':len(manifest['blobs'])}


def quarantine_restore(store):
    """Run only on a verified isolated restore, never on the active source."""
    with store.pool.connection() as c:
        database=c.execute('SELECT current_database() AS name').fetchone()['name']
        if not database.endswith('_restore_test'): raise ValueError('not_restore_database')
        c.execute('UPDATE account_policies SET enabled=false')
        version=int(c.execute("SELECT value FROM probe_metadata WHERE name='schema_version'").fetchone()['value'])
        if version>=22:
            c.execute('UPDATE account_policies SET send_enabled=false')
            c.execute("""UPDATE outgoing_messages SET status=CASE WHEN status='queued' THEN 'cancelled' ELSE 'unknown' END,
                error_code='restored_disabled',finished_at=clock_timestamp() WHERE status IN ('queued','dispatching')""")
        # A snapshot can predate a key revocation. Never revive old credentials on restore.
        c.execute('UPDATE api_keys SET revoked=true')
        c.execute("UPDATE collector_leases SET expires_at=now()-interval '1 second'")
        c.execute("UPDATE source_status SET status='restored_disabled',coverage='unknown',detail_code='requires_reconciliation'")
