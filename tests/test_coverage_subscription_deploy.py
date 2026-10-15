"""Render opt-in deployment without opening secrets or starting any service."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

@pytest.mark.skipif(
    shutil.which('docker') is None and not os.environ.get('CI'),
    reason='Compose rendering needs the host Docker CLI; the isolated test image intentionally has no Docker CLI or socket.',
)
def test_subscription_overlay_is_opt_in_and_has_bounded_runtime_authority():
    assert shutil.which('docker') is not None, 'CI must provide the Docker CLI for the Compose deployment proof'
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, 'PROBE_PUBLIC_URL': 'https://example.invalid',
           'PROBE_ALLOWED_HOSTS': 'example.invalid'}

    def render(*files):
        command = ['docker', 'compose']
        for file in files:
            command += ['-f', str(root / 'deploy' / file)]
        result = subprocess.run(command + ['config', '--no-env-resolution', '--format', 'json'],
                                env=env, text=True, capture_output=True, check=True, timeout=20)
        return json.loads(result.stdout)

    assert 'subscriptions' not in render('compose.yaml')['services']
    config = render('compose.yaml', 'compose.subscriptions.yaml')
    service = config['services']['subscriptions']
    assert service['build']['target'] == 'runtime'
    assert service['command'] == ['python', '-m', 'messaging_service.subscription_worker']
    assert service['init'] is True and service['restart'] == 'unless-stopped'
    assert service['depends_on']['db']['condition'] == 'service_healthy'
    assert set(service['networks']) == {'backend'}
    assert config['networks']['backend']['internal'] is True
    assert not service.get('ports')
    assert service['environment'] == {'DATABASE_URL_FILE': '/run/secrets/database_url'}
    assert len(service['volumes']) == 1
    secret = service['volumes'][0]
    assert secret['type'] == 'bind' and secret['read_only'] is True
    assert Path(secret['source']) == root / 'deploy/private/subscriptions.dsn'
    assert secret['target'] == '/run/secrets/database_url'
    assert secret['bind']['create_host_path'] is False
    assert service['read_only'] is True and service['user'] == '10001:10001'
    assert service['cap_drop'] == ['ALL']
    assert 'no-new-privileges:true' in service['security_opt']
    assert service['tmpfs'] == ['/tmp:size=16m,noexec,nosuid']
    assert int(service['mem_limit']) == 256 * 1024**2
    assert service['cpu_shares'] == 512 and service['pids_limit'] == 32
    assert service['stop_grace_period'] == '15s'
    assert service['logging'] == {'driver': 'json-file',
                                   'options': {'max-size': '5m', 'max-file': '2'}}
