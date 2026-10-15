"""Distribution and generic asset checks, without database/provider startup."""
from pathlib import Path
import os
import stat
import tarfile
import zipfile

import pytest

from anymsg import __version__
from anymsg.assets import ASSET_NAMES,export_whatsapp_assets,source_directory
from messaging_service.whatsapp_collector import whatsapp_asset


def test_receipt_provider_dependency_is_exported():
    assert 'receipts.mjs' in ASSET_NAMES
    assert (source_directory()/'receipts.mjs').is_file()


def test_facade_help_and_version_require_no_database_configuration(capsys,monkeypatch):
    from anymsg.cli import main
    monkeypatch.delenv('DATABASE_URL_FILE',raising=False)
    assert main(['--version'])==0
    assert capsys.readouterr().out.strip()==f'anymsg {__version__}'
    with pytest.raises(SystemExit) as result:main(['--help'])
    assert result.value.code==0
    help_text=capsys.readouterr().out
    assert 'whatsapp-assets --directory' in help_text and 'register-account' in help_text


def test_checkout_assets_and_explicit_private_export(tmp_path,monkeypatch):
    monkeypatch.delenv('ANYMSG_WHATSAPP_ASSETS_DIR',raising=False)
    assert whatsapp_asset('provider.mjs').name=='provider.mjs'
    destination=tmp_path/'node-sources'
    result=export_whatsapp_assets(destination)
    assert set(result['files'])==set(ASSET_NAMES) and not result['installed_dependencies']
    assert stat.S_IMODE(destination.stat().st_mode)==0o700
    assert set(p.name for p in destination.iterdir())==set(ASSET_NAMES)
    assert not (destination/'node_modules').exists()
    assert all(stat.S_IMODE((destination/name).stat().st_mode)==0o600 for name in ASSET_NAMES)
    monkeypatch.setenv('ANYMSG_WHATSAPP_ASSETS_DIR',str(destination))
    assert whatsapp_asset('provider.mjs')==destination/'provider.mjs'
    assert whatsapp_asset('pair.mjs')==destination/'pair.mjs'
    with pytest.raises(FileExistsError):export_whatsapp_assets(destination)


def test_export_refuses_destination_and_ancestor_symlinks(tmp_path):
    actual=tmp_path/'actual';actual.mkdir()
    parent=tmp_path/'parent';parent.symlink_to(actual,target_is_directory=True)
    with pytest.raises(OSError):export_whatsapp_assets(parent/'node')
    assert not (actual/'node').exists()
    final=tmp_path/'final';final.symlink_to(actual,target_is_directory=True)
    with pytest.raises(FileExistsError):export_whatsapp_assets(final)


def test_override_refuses_asset_symlinks(tmp_path,monkeypatch):
    folder=tmp_path/'sources';folder.mkdir()
    (folder/'provider.mjs').symlink_to(Path(__file__).resolve())
    monkeypatch.setenv('ANYMSG_WHATSAPP_ASSETS_DIR',str(folder))
    with pytest.raises(ValueError,match='invalid_whatsapp_asset'):whatsapp_asset('provider.mjs')
    with pytest.raises(ValueError,match='invalid_whatsapp_asset'):whatsapp_asset('../provider.mjs')


def _artifacts():
    wheel=os.environ.get('ANYMSG_TEST_WHEEL')
    sdist=os.environ.get('ANYMSG_TEST_SDIST')
    if not wheel or not sdist:pytest.skip('Set ANYMSG_TEST_WHEEL/ANYMSG_TEST_SDIST to inspect built distributions')
    return Path(wheel),Path(sdist)


def test_built_wheel_includes_immutable_sql_and_node_sources():
    wheel,_=_artifacts()
    source=Path(__file__).resolve().parents[1]
    with zipfile.ZipFile(wheel) as archive:
        names=set(archive.namelist())
        assert 'anymsg/__main__.py' in names and 'anymsg/client.py' in names
        service = source/'services/messaging'
        sql_files = list((service/'messaging_service').rglob('*.sql'))
        assert sql_files
        for path in sql_files:
            key=path.relative_to(service).as_posix()
            assert key in names and archive.read(key)==path.read_bytes()
        for name in ASSET_NAMES:
            key='messaging_service/whatsapp_assets/'+name
            assert key in names and archive.read(key)==(source/'whatsapp'/name).read_bytes()
        assert not any('node_modules/' in name or '/private/' in name or name.endswith(('.dsn','.token','.session','.pyc')) for name in names)
        metadata=archive.read(f'anymsg-{__version__}.dist-info/METADATA').decode()
        assert 'Name: anymsg' in metadata and f'Version: {__version__}' in metadata
        assert 'License-Expression: MIT' in metadata
        assert b'anymsg = anymsg.cli:main' in archive.read(f'anymsg-{__version__}.dist-info/entry_points.txt')


def test_built_sdist_keeps_build_inputs_without_runtime_data():
    _,sdist=_artifacts()
    with tarfile.open(sdist,'r:gz') as archive:
        names=set(archive.getnames())
        prefix=f'anymsg-{__version__}/'
        for expected in ['pyproject.toml','README.md','LICENSE','anymsg/client.py',
                'messaging_service/schema.sql','whatsapp/provider.mjs','whatsapp/package-lock.json']:
            assert prefix+expected in names
        assert not any('node_modules/' in name or '/private/' in name or name.endswith(('.dsn','.token','.session','.pyc')) for name in names)
