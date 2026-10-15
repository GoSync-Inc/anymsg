"""Explicit operator-launched hint dispatcher with a dedicated restricted DSN."""
import argparse
import json
import os
from pathlib import Path
import signal
import threading
from .event_delivery import dispatch_hints, register_receiver, disable_receiver
from .store import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--register-receiver', type=Path, metavar='PRIVATE_JSON')
    group.add_argument('--disable-receiver', metavar='UUID')
    args = parser.parse_args()
    dsn_file = os.environ.get('DATABASE_URL_FILE')
    if not dsn_file:
        raise SystemExit('DATABASE_URL_FILE is required')
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    store = Store(Path(dsn_file).read_text().strip(), Path('/nonexistent-originals'), 'http://unused')
    try:
        if args.register_receiver:
            info = args.register_receiver.stat()
            if info.st_mode & 0o077 or info.st_size > 8192:
                raise SystemExit('Receiver configuration must be private and bounded')
            config = json.loads(args.register_receiver.read_text())
            if not isinstance(config, dict) or set(config) != {'receiver_id','principal','key_id','endpoint','secret_file'}:
                raise SystemExit('Invalid receiver configuration')
            print(json.dumps(register_receiver(store, **config)))
            return
        if args.disable_receiver:
            print(json.dumps(disable_receiver(store, args.disable_receiver)))
            return
        while not stop.is_set():
            print(json.dumps(dispatch_hints(store)), flush=True)
            if args.once:
                break
            stop.wait(2)
    finally:
        store.close()


if __name__ == '__main__':
    main()
