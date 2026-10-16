"""Fail closed on version mismatch or unsafe distribution members (no network)."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile

root = Path(__file__).resolve().parents[1]
service = root/'services/messaging'
version = tomllib.loads((service/'pyproject.toml').read_text())['project']['version']
if len(sys.argv)>1:
    assert re.fullmatch(r'\d+\.\d+\.\d+(?:[a-z]+\d+)?',sys.argv[1])
    assert version == sys.argv[1], 'release version differs'
assert json.loads((root/'packages/npm/package.json').read_text())['version'] == version
deedkit = root/'packages/deedkit'
assert json.loads((deedkit/'package.json').read_text())['name'] == 'deedkit'
assert json.loads((deedkit/'package.json').read_text())['version'] == version, 'deedkit version differs'
lock = json.loads((deedkit/'package-lock.json').read_text())
assert lock['version'] == lock['packages']['']['version'] == version, 'deedkit lockfile version differs'
assert f"__version__ = '{version}'" in (service/'messaging_service/__init__.py').read_text() or f'__version__ = "{version}"' in (service/'messaging_service/__init__.py').read_text()
assert version in (service/'anymsg/__init__.py').read_text()

def safe(names):
    for name in names:
        parts=Path(name).parts
        assert not name.startswith('/') and '..' not in parts
        assert not set(parts)&{'.git','.local','private','node_modules','sessions','outputs','archives','__pycache__'}
        assert not name.endswith(('.dsn','.token','.session','.sqlite','.age','.dump','.pyc','.pem'))

wheel = root/'dist'/f'anymsg-{version}-py3-none-any.whl'
sdist = root/'dist'/f'anymsg-{version}.tar.gz'
with zipfile.ZipFile(wheel) as z:
    safe(z.namelist())
    for folder in ('messaging_service','anymsg'):
        for path in (service/folder).rglob('*'):
            if path.suffix in ('.sql','.py'):
                assert z.read(path.relative_to(service).as_posix()) == path.read_bytes()
with tarfile.open(sdist) as t:
    safe(t.getnames())
    for required in ('Dockerfile','media_benchmark.py','LICENSE','SETUP.md','licenses/age-LICENSE'):
        assert f'anymsg-{version}/{required}' in t.getnames(), required
def npm_inventory(path, name, files):
    # Exactly the reviewed files, each byte-identical to the source, including
    # package.json: an added member or install script fails the check.
    with tarfile.open(path) as t:
        names = t.getnames()
        safe(names)
        assert sorted(names) == sorted(f'package/{member}' for member in files), names
        for member in files:
            assert t.getmember(f'package/{member}').isfile(), member
            assert t.extractfile(f'package/{member}').read() == (root/'packages'/name/member).read_bytes(), member
        manifest = json.load(t.extractfile('package/package.json'))
        assert manifest['name'] == name and manifest['version'] == version, path.name

with tarfile.open(root/'dist'/f'anymsg-{version}.tgz') as t:
    safe(t.getnames())
    assert json.load(t.extractfile('package/package.json'))['version'] == version
    for member in t.getnames():
        assert member in {'package/package.json','package/README.md','package/LICENSE','package/THIRD_PARTY_NOTICES.md'} or member.startswith(('package/lib/','package/bin/')),member
deedkit_files = ('package.json','README.md','LICENSE','THIRD_PARTY_NOTICES.md','lib/index.js','lib/index.d.ts','lib/tools.json','bin/deedkit.js')
built = root/'dist'/f'deedkit-{version}.tgz'
if built.is_file():
    npm_inventory(built, 'deedkit', deedkit_files)
else:
    # CI does not pack deedkit yet: pack the source offline and check that tarball instead.
    with tempfile.TemporaryDirectory() as directory:
        subprocess.run(['npm','pack','--ignore-scripts','--offline','--loglevel','warn','--pack-destination',directory],
            cwd=deedkit, check=True, stdout=subprocess.DEVNULL, env={**os.environ, 'npm_config_offline':'true'})
        npm_inventory(Path(directory)/f'deedkit-{version}.tgz', 'deedkit', deedkit_files)
print(f'anymsg and deedkit {version}: distribution versions, inventories and immutable SQL passed')
