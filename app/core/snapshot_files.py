"""Publish a completed directory without replacing an existing snapshot."""
from pathlib import Path
import time


def publish_directory(source, destination):
    source, destination = Path(source), Path(destination)
    if source.resolve().parent != destination.resolve().parent:
        raise ValueError('자료 판은 같은 저장 폴더 안에서만 확정합니다.')
    for attempt in range(8):
        if destination.exists():
            raise FileExistsError('이미 존재하는 자료 판을 덮어쓰지 않습니다.')
        try:
            return source.rename(destination)
        except PermissionError as exc:
            # Observed on Windows when renaming a freshly indexed directory.
            # Other permission failures remain failures; never delete the target.
            if getattr(exc, 'winerror', None) not in (5, 32, 33) or attempt == 7 or not source.is_dir() or destination.exists():
                raise
            time.sleep(0.025 * (attempt + 1))
