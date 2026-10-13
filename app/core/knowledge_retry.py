"""Persistent daily knowledge sync catch-up on the PC (REQ-SYNC-002)."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import json
import os

KST = timezone(timedelta(hours=9))


def run_due(product, target_url, *, state_dir, now=None, push=None):
    from app.core.knowledge_push import push_product
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256((product + "\n" + target_url.rstrip("/")).encode()).hexdigest()[:24]
    path, lock = state_dir / f"{key}.json", state_dir / f"{key}.lock"
    # A file lock survives process crashes; ownership is the OS lock, not existence.
    with lock.open("a+b") as handle:
        try:
            if os.name == "nt":
                import msvcrt
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return {"status": "BUSY", "detail": "동기화가 진행 중입니다."}
        try:
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                state = {}
            today = now.astimezone(KST).date()
            due_date = today if now.astimezone(KST).hour >= 8 else today - timedelta(days=1)
            # First installation before 08:00 waits for today's schedule.
            if not state and now.astimezone(KST).hour < 8:
                return {"status": "NOT_DUE", "detail": "오전 8시 실행 대기"}
            if state.get("completed_date", "") >= due_date.isoformat():
                return {"status": "NOT_DUE", "detail": "해당 일정 동기화 완료"}
            next_retry = state.get("next_retry_at")
            if next_retry and now < datetime.fromisoformat(next_retry):
                return {"status": "NOT_DUE", "detail": "15분 재시도 대기", "next_retry_at": next_retry}
            try:
                result = (push or push_product)(product, target_url)
            except Exception as exc:
                result = {"status": "FAILED", "detail": f"동기화 실패: {type(exc).__name__}"}
            state.update(last_attempt_at=now.isoformat(), status=result["status"], detail=result.get("detail", ""))
            if result["status"] == "SUCCESS":
                state.update(completed_date=due_date.isoformat(), last_success_at=now.isoformat(), next_retry_at=None)
            else:
                state["next_retry_at"] = (now + timedelta(minutes=15)).isoformat()
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            temporary.replace(path)
            return {**result, "next_retry_at": state["next_retry_at"], "last_success_at": state.get("last_success_at")}
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)
