"""Private on-disk permissions for SQLite data and backups on POSIX hosts."""
import os
from pathlib import Path

POSIX=os.name=='posix'


def private_directory(path):
    path=Path(path);path.mkdir(parents=True,exist_ok=True)
    if POSIX:os.chmod(path,0o700)
    return path


def private_file(path):
    path=Path(path)
    if POSIX and path.exists():os.chmod(path,0o600)
    return path


def private_sqlite(path):
    path=Path(path);private_directory(path.parent)
    for suffix in ('','-wal','-shm','-journal'):private_file(Path(str(path)+suffix))


def require_private_sqlite(path):
    if not POSIX:return
    path=Path(path)
    if path.parent.stat().st_mode&0o077 or path.stat().st_mode&0o077:raise PermissionError('개인 기록 저장소 권한 설정이 필요합니다.')
