"""Render opt-in deployment without opening secrets or starting any service."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

@pytest.mark.skipif(
    shutil.which('docker') is None and not os.environ.get('CI'),
    reason='Compose rendering needs the host Docker CLI; the isolated test image intentionally has no Docker CLI or socket.',
)
def test_subscription_overlay_is_opt_in_and_has_bounded_runtime_authority(tmp_path):
    assert shutil.which('docker') is not None, 'CI must provide the Docker CLI for the Compose deployment proof'
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, 'PROBE_PUBLIC_URL': 'https://example.invalid',
           'PROBE_ALLOWED_HOSTS': 'example.invalid'}
    # Canonical rendering resolves env_file on older Compose versions. Give it
    # only a synthetic DB fixture; retain the real project directory so builds
    # and bind mounts still resolve against the original deployment template.
    base = yaml.safe_load((root / 'deploy/compose.yaml').read_text())
    assert base['services']['db']['env_file'] == './private/postgres.env'
    database_environment = {'POSTGRES_USER': 'synthetic-render-owner',
                            'POSTGRES_PASSWORD': 'synthetic-render-only',
                            'POSTGRES_DB': 'synthetic-render'}
    fixture_env = tmp_path / 'postgres.synthetic.env'
    fixture_env.write_text(''.join(f'{key}={value}\n' for key, value in database_environment.items()))
    base['services']['db']['env_file'] = str(fixture_env)
    fixture_base = tmp_path / 'compose.synthetic.yaml'
    fixture_base.write_text(yaml.safe_dump(base, sort_keys=False))

    def render(*files):
        command = ['docker', 'compose', '--project-directory', str(root / 'deploy')]
        for file in files:
            command += ['-f', str(fixture_base if file == 'compose.yaml' else root / 'deploy' / file)]
        result = subprocess.run(command + ['config', '--format', 'json'],
                                env=env, text=True, capture_output=True, timeout=20)
        assert result.returncode == 0, f'Compose render failed ({result.returncode}): {result.stderr}'
        config = json.loads(result.stdout)
        assert config['services']['db']['environment'] == database_environment
        return config

    assert 'subscriptions' not in render('compose.yaml')['services']
    config = render('compose.yaml', 'compose.subscriptions.yaml')
    service = config['services']['subscriptions']
    assert service['build']['target'] == 'runtime'
    assert Path(service['build']['context']) == root
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
