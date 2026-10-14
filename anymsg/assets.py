"""Export only generic Node sources; never install or start a messenger client."""
from __future__ import annotations

import os
from pathlib import Path
import stat


ASSET_NAMES = (
    'bounds.mjs','capture.mjs','history.mjs','identity.mjs','ipc.mjs','media.mjs',
    'navigation.mjs','pair.mjs','pairing.mjs','provider.mjs','sender.mjs','outgoing.mjs',
    'transport-state.mjs','read-state.mjs','receipts.mjs','operations.mjs','package.json','package-lock.json',
)


def source_directory():
    import messaging_service
    package=Path(messaging_service.__file__).resolve().parent
    bundled=package/'whatsapp_assets'
    if bundled.is_dir():return bundled
    return package.parent/'whatsapp'


def _parent_fd(path):
    """Walk absolute ancestors with openat/no-follow, not a single path open."""
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    try:
        for part in path.parent.parts[1:]:
            if part in ('.','..'):raise ValueError('invalid_asset_directory')
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd);fd=child
        return fd
    except BaseException:
        os.close(fd)
        raise


def export_whatsapp_assets(directory):
    path=Path(directory).absolute()
    if path.name in ('','.', '..') or '..' in path.parts:
        raise ValueError('invalid_asset_directory')
    # Read and validate the complete finite allowlist before creating anything.
    contents={}
    for name in ASSET_NAMES:
        fd=os.open(source_directory()/name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd,'rb') as source:
            info=os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size>4*1024**2:
                raise ValueError('invalid_whatsapp_asset')
            data=source.read(4*1024**2+1)
            if len(data)>4*1024**2:raise ValueError('invalid_whatsapp_asset')
            contents[name]=data
    parent=_parent_fd(path)
    try:
        # No replacement: an existing directory, file or symlink is an error.
        os.mkdir(path.name,mode=0o700,dir_fd=parent)
        target=os.open(path.name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
        try:
            for name,data in contents.items():
                fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=target)
                with os.fdopen(fd,'wb') as output:
                    output.write(data);output.flush();os.fsync(output.fileno())
            os.fsync(target)
        finally:os.close(target)
        os.fsync(parent)
    finally:os.close(parent)
    return {'directory':str(path),'files':list(ASSET_NAMES),'installed_dependencies':False}
