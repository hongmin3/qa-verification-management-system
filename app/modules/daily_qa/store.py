"""일일 점검 저장 창구 (SPEC REQ-DAILY-001 · REQ-DAILY-009).

SQL 은 여기 없다. DB 접근은 `app/core/storage.py` 의 `Storage` 한 곳으로만 한다는 규칙에 따라
테이블과 쿼리는 `app/core/daily_qa_storage.py` (`Storage` 가 상속하는 믹스인)에 있다.
이 클래스는 `store.list_runs()` 를 `Storage.daily_qa_list_runs()` 로 넘기기만 한다.
"""

from __future__ import annotations

from pathlib import Path

from app.core.daily_qa_storage import REVIEW_STATUSES, RUN_STATUSES, now_iso
from app.core.storage import Storage

__all__ = ["DailyQaStore", "REVIEW_STATUSES", "RUN_STATUSES", "now_iso"]


class DailyQaStore:
    def __init__(self, db_path: Path | None = None, storage: Storage | None = None) -> None:
        self.storage = storage or Storage(db_path=db_path)

    def __getattr__(self, name: str):
        target = getattr(self.storage, f"daily_qa_{name}", None)
        if target is None:
            raise AttributeError(name)
        return target
