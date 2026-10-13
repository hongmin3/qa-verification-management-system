"""프로세스 종료 시 OS가 해제하는 파일 잠금. 잠금 파일은 남아도 재실행할 수 있다."""
import os
from pathlib import Path


class LockHeldError(RuntimeError):
    pass


class RunLock:
    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open('a+b')
        self.handle.seek(0, 2)
        if self.handle.tell() == 0:
            self.handle.write(b'0')
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            self.handle = None
            raise LockHeldError('SRS 수집이 이미 실행 중입니다.') from exc
        return self

    def __exit__(self, *args):
        if self.handle:
            self.handle.close()
            self.handle = None
