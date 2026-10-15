"""One isolated operator container: cold capture, resume signal, encrypt, receipt.

Only the public age recipient is mounted. No SSH key, Docker socket or decryption
identity enters this container. Host orchestration owns stop/start and delivery.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import stat
import time

from psycopg.conninfo import conninfo_to_dict

from .backup import create_snapshot,verify_bundle
from .backup_crypto import encrypt_snapshot,encrypt_snapshot_stream,snapshot_stream_bound
from .spool import fsync_dir
from .store import Store
from .telegram_collector import private_json


def write_json(path,data):
    path=Path(path)
    fd,name=tempfile.mkstemp(prefix='.status-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as out:
            out.write(json.dumps(data,separators=(',',':'))+'\n');out.flush();os.fsync(out.fileno())
        os.replace(name,path);fsync_dir(path.parent)
    finally:Path(name).unlink(missing_ok=True)


def dump_credentials(dsn,directory):
    values=conninfo_to_dict(dsn)
    if set(values)-{'host','port','user','password','dbname','sslmode','connect_timeout'}:
        raise ValueError('unsupported_dump_connection')
    values.setdefault('port','5432')
    if not all(isinstance(values.get(k),str) and values[k] and '\n' not in values[k] and '\r' not in values[k]
               for k in ('host','port','user','password','dbname')):
        raise ValueError('invalid_dump_connection')
    if ',' in values['host'] or not values['port'].isdigit():raise ValueError('invalid_dump_connection')
    def escaped(value):return value.replace('\\','\\\\').replace(':','\\:')
    password_file=Path(directory)/'pgpass'
    fd=os.open(password_file,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as out:
        out.write(':'.join(escaped(values[k]) for k in ['host','port','dbname','user','password'])+'\n')
    # SQL connection options are passed in a non-secret libpq conninfo argument;
    # password exists only in a 0600 file on the container tmpfs.
    from psycopg.conninfo import make_conninfo
    public={k:v for k,v in values.items() if k!='password'}
    return ['pg_dump','--dbname='+make_conninfo(**public)],password_file


def run(config):
    required={'id','dsn_file','files_directory','recovery_config_file','recipient_file','work_directory'}
    if set(config)-{'mode'}!=required or config.get('mode','staged_v1') not in ('staged_v1','stream_v2'):
        raise ValueError('invalid_backup_worker_config')
    from .backup_receiver import BACKUP_ID
    if not BACKUP_ID.fullmatch(config['id']):raise ValueError('invalid_backup_id')
    work=Path(config['work_directory'])
    if work.is_symlink() or not work.is_dir() or work.stat().st_mode&0o077:raise ValueError('private_work_directory_required')
    dsn=Path(config['dsn_file']).read_text().strip()
    recovery=private_json(config['recovery_config_file'])
    store=Store(dsn,Path(config['files_directory']),'http://backup.invalid')
    try:
        streaming=config.get('mode')=='stream_v2'
        captured=lambda:write_json(work/'captured.json',{'id':config['id'],'status':'capture_complete'})
        if streaming and (work/'snapshot').exists():
            manifest=verify_bundle(work/'snapshot',blob_root=store.files)
            snapshot={'status':'snapshot_created','tables':len(manifest['tables']),'blobs':len(manifest['blobs']),
                      'blob_bytes':sum(row['bytes'] for row in manifest['blobs'])}
            captured()
        else:
            with tempfile.TemporaryDirectory(prefix='pg-') as secrets_dir:
                command,password_file=dump_credentials(dsn,secrets_dir)
                previous=os.environ.get('PGPASSFILE');os.environ['PGPASSFILE']=str(password_file)
                try:
                    snapshot=create_snapshot(store,work/'snapshot',command,recovery,captured_callback=captured,
                                             reference_blobs=streaming)
                finally:
                    if previous is None:os.environ.pop('PGPASSFILE',None)
                    else:os.environ['PGPASSFILE']=previous
        if streaming:
            with (work/'snapshot/manifest.json').open('rb') as f:
                snapshot_sha=hashlib.file_digest(f,'sha256').hexdigest()
            fifo=work/'ciphertext.fifo';os.mkfifo(fifo,0o600)
            metadata={'v':2,'id':config['id'],'snapshot_sha256':snapshot_sha,
                      'max_bytes':snapshot_stream_bound(work/'snapshot',store.files),
                      'captured_at':json.loads((work/'snapshot/manifest.json').read_text())['captured_at']}
            write_json(work/'stream.json',metadata)
            # Host owns SSH and receives admission before starting the encoder.
            # A lost final receipt can short-circuit encryption on a retry.
            while not (work/'stream-start.json').exists():time.sleep(.1)
            control=private_json(work/'stream-start.json')
            if control=={'action':'skip'}:return {'status':'already_delivered','id':config['id']}
            if control!={'action':'start'}:raise ValueError('invalid_backup_stream_control')
            if not stat.S_ISFIFO(fifo.lstat().st_mode):raise ValueError('invalid_backup_stream')
            with fifo.open('wb',buffering=0) as out:
                encrypted=encrypt_snapshot_stream(work/'snapshot',out,Path(config['recipient_file']).read_text().strip(),
                                                  blob_root=store.files,expected_manifest_sha256=snapshot_sha)
            result={'v':2,'id':config['id'],'status':'encrypted',**encrypted,'snapshot':snapshot,
                    'snapshot_sha256':snapshot_sha,'created_at':datetime.now(timezone.utc).isoformat(),
                    'captured_at':metadata['captured_at']}
            write_json(work/'result.json',result)
            return result
        output=work/'bundle.tar.age'
        encrypt_snapshot(work/'snapshot',output,Path(config['recipient_file']).read_text().strip(),
                         consume_staged_blobs=True)
        with output.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
        result={'v':1,'id':config['id'],'status':'encrypted','bytes':output.stat().st_size,'sha256':digest,
                'snapshot':snapshot,'created_at':datetime.now(timezone.utc).isoformat(),
                'captured_at':json.loads((work/'snapshot/manifest.json').read_text())['captured_at']}
        write_json(work/'result.json',result)
        print(json.dumps({'status':'encrypted','id':config['id'],'bytes':result['bytes']}),flush=True)
        return result
    finally:store.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',type=Path,required=True)
    args=parser.parse_args();os.umask(0o077)
    try:run(private_json(args.config))
    except Exception:
        print('{"status":"error","code":"backup_worker_failed"}',flush=True)
        raise SystemExit(1) from None


if __name__=='__main__':main()
