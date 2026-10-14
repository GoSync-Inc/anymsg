"""Pair one WhatsApp account from an owner's terminal, under both ownership locks."""
import asyncio
from contextlib import suppress
import json
import os
from pathlib import Path
import secrets
import signal

from .archive import Archive
from .collector_state import session_lock
from .store import Store
from .telegram_pair import require_owner_terminal
from .whatsapp_collector import configuration,clear_browser_runtime_locks,whatsapp_asset


async def pair_config(path):
    require_owner_terminal()
    cfg=configuration(path)
    session=Path(cfg['session_directory']);session.mkdir(parents=True,exist_ok=True,mode=0o700)
    if session.is_symlink() or session.stat().st_mode&0o077:raise RuntimeError('private_session_required')
    with session_lock(session) as lock_fd:
        clear_browser_runtime_locks(session)
        store=Store(Path(os.environ['DATABASE_URL_FILE']).read_text().strip(),Path(os.environ['PROBE_FILES']),'http://collector.invalid')
        archive=Archive(store);owner='wa-pair-'+secrets.token_hex(12);epoch=None;process=None;tasks=[]
        try:
            with store.pool.connection() as c:
                row=c.execute('SELECT platform,synthetic FROM accounts WHERE id=%s',(cfg['account'],)).fetchone()
                if not row or row['platform']!='whatsapp' or row['synthetic']:raise RuntimeError('live_account_required')
            epoch=await asyncio.to_thread(archive.acquire,cfg['account'],owner)
            script=whatsapp_asset('pair.mjs')
            process=await asyncio.create_subprocess_exec(cfg['node_executable'],str(script),str(path),
                stdin=asyncio.subprocess.DEVNULL,stdout=asyncio.subprocess.DEVNULL,
                pass_fds=(lock_fd,),start_new_session=True) # stderr alone remains the owner's TTY.
            async def heartbeat():
                while True:
                    await asyncio.sleep(15);await asyncio.to_thread(archive.renew,cfg['account'],owner,epoch)
            stopping=asyncio.Event();loop=asyncio.get_running_loop()
            for sig in [signal.SIGTERM,signal.SIGINT]:loop.add_signal_handler(sig,stopping.set)
            finished=asyncio.create_task(process.wait());renewal=asyncio.create_task(heartbeat())
            stopped=asyncio.create_task(stopping.wait());tasks=[finished,renewal,stopped]
            done,_=await asyncio.wait(tasks,timeout=300,return_when=asyncio.FIRST_COMPLETED)
            if not done:raise RuntimeError('pairing_timeout')
            for task in done:task.result()
            if stopped in done:raise RuntimeError('pairing_cancelled')
            if finished not in done:raise RuntimeError('collector_lease_lost')
            if finished.result()!=0:raise RuntimeError('whatsapp_identity_mismatch' if finished.result()==2 else 'pairing_failed')
            await asyncio.to_thread(archive.renew,cfg['account'],owner,epoch)
            print(json.dumps({'status':'paired','collector_started':False}),flush=True)
        finally:
            for task in tasks:task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            if process:
                if process.returncode is None:
                    with suppress(ProcessLookupError):process.terminate()
                    try:await asyncio.wait_for(process.wait(),12)
                    except TimeoutError:
                        with suppress(ProcessLookupError):os.killpg(process.pid,signal.SIGKILL)
                        await process.wait()
                with suppress(ProcessLookupError):os.killpg(process.pid,signal.SIGTERM)
            if epoch is not None:
                with suppress(Exception):await asyncio.to_thread(archive.release,cfg['account'],owner,epoch)
            store.close()


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',type=Path,required=True)
    args=parser.parse_args();os.umask(0o077)
    try:asyncio.run(pair_config(args.config))
    except Exception as exc:
        safe={'owner_terminal_required','collector_disabled','session_handover_not_confirmed','private_session_required',
            'whatsapp_identity_mismatch','live_account_required','collector_already_owned','session_already_owned',
            'collector_lease_lost','pairing_timeout'}
        print(json.dumps({'status':'not_paired','code':str(exc) if str(exc) in safe else 'pairing_failed'}),flush=True)
        raise SystemExit(1) from None


if __name__=='__main__':main()
