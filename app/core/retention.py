"""오래된 백업·실행 폴더·AI 응답 저장본 지우기 (OPEN_QUESTIONS 8-16).

지우지 않으면 매일 백업 ZIP 과 일일 QA 실행 폴더가 쌓이고, AI 응답 저장본(`ai_cache`)이 계속 커진다.
보관 일수는 `config.yaml` 의 `retention.*` 에서 읽는다. 0 이면 그 종류는 지우지 않는다.

지우는 대상은 이름 형식으로 가린다. 다른 이름의 파일·폴더는 날짜와 관계없이 건드리지 않는다.

| 대상 | 이름 형식 | 설정 키 | 기본 |
|---|---|---|---|
| 백업 ZIP | `qa-backup-YYYYMMDDTHHMMSSZ.zip` | `retention.backup_days` | 30 |
| AI 응답 저장본 | `ai_cache.created_at` | `retention.ai_cache_days` | 180 |
| 일일 QA 실행 폴더 | `output/daily_qa/YYYYMMDD-HHMMSS/` | `retention.daily_qa_run_days` | 365 |
| 일일 QA 작업 폴더의 실행 입력 | `<작업 폴더>/runs/YYYYMMDD-HHMMSS/` | `retention.daily_qa_workspace_run_days` | 30 |

일일 QA 스냅샷(`data/daily_qa/snapshots/`)은 지우지 않는다. 전날·지난주 비교 기준이라 다시 만들 수 없다.
"""

from __future__ import annotations

import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_DAYS = {
    "backup_days": 30,
    "ai_cache_days": 180,
    # 실행 폴더는 감사 목적이라 길게 둔다. 작업 폴더의 입력은 실행 폴더에 사본(sent/)이 있어 짧게 둔다.
    "daily_qa_run_days": 365,
    "daily_qa_workspace_run_days": 30,
}

_BACKUP_NAME = re.compile(r"^qa-backup-(\d{8}T\d{6}Z)\.zip$")
_RUN_NAME = re.compile(r"^(\d{8}-\d{6})$")


def retention_days(settings) -> dict[str, int]:
    """보관 일수 설정. 없거나 잘못된 값이면 기본값, 음수는 0(지우지 않음)으로 본다."""
    days: dict[str, int] = {}
    for key, default in DEFAULT_DAYS.items():
        raw = settings.get(f"retention.{key}", default)
        try:
            days[key] = max(0, int(raw))
        except (TypeError, ValueError):
            days[key] = default
    return days


def _cutoff(keep_days: int, now: datetime | None) -> datetime:
    return (now or datetime.now(timezone.utc)) - timedelta(days=keep_days)


def prune_backups(directory: Path, keep_days: int, now: datetime | None = None) -> list[Path]:
    """보관 일수를 넘은 백업 ZIP 을 지운다. 가장 새 백업 하나는 날짜와 관계없이 남긴다."""
    if keep_days <= 0 or not directory.is_dir():
        return []
    backups: list[tuple[datetime, Path]] = []
    for path in directory.iterdir():
        match = _BACKUP_NAME.match(path.name)
        if not match or not path.is_file():
            continue
        created = datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        backups.append((created, path))
    backups.sort()
    cutoff = _cutoff(keep_days, now)
    removed = []
    for created, path in backups[:-1]:
        if created < cutoff:
            path.unlink(missing_ok=True)
            removed.append(path)
    return removed


def prune_run_folders(base: Path, keep_days: int, now: datetime | None = None) -> list[Path]:
    """`YYYYMMDD-HHMMSS` 이름의 실행 폴더 가운데 보관 일수를 넘은 것을 통째로 지운다."""
    if keep_days <= 0 or not base.is_dir():
        return []
    cutoff = _cutoff(keep_days, now)
    removed = []
    for path in sorted(base.iterdir()):
        match = _RUN_NAME.match(path.name)
        if not match or not path.is_dir() or path.is_symlink():
            continue
        # 실행 번호는 실행한 컴퓨터의 현지 시각이다. 하루 단위 보관이라 시간대 차이는 무시한다.
        started = datetime.strptime(match.group(1), "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
        if started < cutoff:
            shutil.rmtree(path)
            removed.append(path)
    return removed


def prune_ai_cache(storage, keep_days: int, now: datetime | None = None) -> int:
    """보관 일수를 넘은 AI 응답 저장본 줄을 지운다. 지워도 같은 요청이 오면 다시 부르고 새로 저장한다."""
    if keep_days <= 0:
        return 0
    return storage.delete_ai_cache_before(_cutoff(keep_days, now).isoformat())


def _daily_qa_workspace(settings) -> Path:
    raw = str(settings.get("daily_qa.workspace_dir", "") or "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".qa-daily-workspace"


def run_retention(settings, backup_dir: Path, storage=None, now: datetime | None = None) -> dict:
    """설정된 보관 일수로 네 가지를 정리하고 무엇을 지웠는지 돌려준다."""
    from app.core.storage import Storage

    days = retention_days(settings)
    result = {
        "backups": [path.name for path in prune_backups(backup_dir, days["backup_days"], now)],
        "daily_qa_runs": [path.name for path in prune_run_folders(settings.root / "output" / "daily_qa", days["daily_qa_run_days"], now)],
        "daily_qa_workspace_runs": [
            path.name for path in prune_run_folders(_daily_qa_workspace(settings) / "runs", days["daily_qa_workspace_run_days"], now)
        ],
        "ai_cache_rows": 0,
    }
    if days["ai_cache_days"] > 0:
        storage = storage or Storage(db_path=settings.root / "data" / "app.db")
        result["ai_cache_rows"] = prune_ai_cache(storage, days["ai_cache_days"], now)
    return result
