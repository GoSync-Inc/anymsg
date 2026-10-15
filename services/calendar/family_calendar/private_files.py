"""Owner-only files. Reject symlinks and existing permissive secret files."""

from pathlib import Path
import os
import stat
import tempfile

from .errors import CalendarError


def private_dir(path):
    path = Path(path)
    if path.is_symlink():
        raise CalendarError("unsafe_private_path")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir() or path.stat().st_mode & 0o077:
        raise CalendarError("unsafe_private_permissions")
    return path


def read_private(path):
    path = Path(path)
    if path.is_symlink():
        raise CalendarError("unsafe_private_path")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
            raise CalendarError("unsafe_private_permissions")
        with os.fdopen(fd, "rb", closefd=False) as file:
            return file.read()
    finally:
        os.close(fd)


def write_private(path, data):
    path = Path(path)
    private_dir(path.parent)
    if path.is_symlink():
        raise CalendarError("unsafe_private_path")
    if path.exists():
        read_private(path)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, path)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
