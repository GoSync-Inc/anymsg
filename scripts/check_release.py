"""Fail closed on version mismatch or unsafe distribution members (no network)."""
import json
from pathlib import Path
import re
import sys
import tarfile
import tomllib
import zipfile

root = Path(__file__).resolve().parents[1]
version = tomllib.loads((root/'pyproject.toml').read_text())['project']['version']
if len(sys.argv)>1:
    assert re.fullmatch(r'\d+\.\d+\.\d+(?:[a-z]+\d+)?',sys.argv[1])
    assert version == sys.argv[1], 'release version differs'
assert json.loads((root/'packages/npm/package.json').read_text())['version'] == version
assert f"__version__ = '{version}'" in (root/'messaging_service/__init__.py').read_text() or f'__version__ = "{version}"' in (root/'messaging_service/__init__.py').read_text()
assert version in (root/'anymsg/__init__.py').read_text()

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
        for path in (root/folder).rglob('*'):
            if path.suffix in ('.sql','.py'):
                assert z.read(path.relative_to(root).as_posix()) == path.read_bytes()
with tarfile.open(sdist) as t:
    safe(t.getnames())
    for required in ('Dockerfile','media_benchmark.py','LICENSE','SETUP.md','licenses/age-LICENSE'):
        assert f'anymsg-{version}/{required}' in t.getnames(), required
with tarfile.open(root/'dist'/f'anymsg-{version}.tgz') as t:
    safe(t.getnames())
    assert json.load(t.extractfile('package/package.json'))['version'] == version
    for member in t.getnames():
        assert member in {'package/package.json','package/README.md','package/LICENSE','package/THIRD_PARTY_NOTICES.md'} or member.startswith(('package/lib/','package/bin/')),member
print(f'anymsg {version}: distribution versions, inventories and immutable SQL passed')
