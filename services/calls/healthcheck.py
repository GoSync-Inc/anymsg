"""Authenticated local readiness probe; never prints credentials or response data."""
import os
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener


def probe():
    token = (Path(os.environ['VOICE_DATA_DIR']) / 'owner.token').read_text().strip()
    port = int(os.environ.get('VOICE_PORT', '13842'))
    request = Request(f'http://127.0.0.1:{port}/api/health', headers={'Authorization': 'Bearer ' + token})
    with build_opener(ProxyHandler({})).open(request, timeout=8) as response:
        if response.status != 200:
            raise RuntimeError('Voice readiness failed')


if __name__ == '__main__':
    try:
        probe()
    except Exception:
        raise SystemExit('Voice readiness failed') from None
