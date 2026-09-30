"""하루 토큰 사용량과 한도 (REQ-USAGE-002). 비용 대시보드의 날짜 구분도 같은 기준을 쓴다.

"오늘"은 한국 시간 0시부터다. 화면과 429 문구가 "오늘"이라고 말하고 사용자는 한국에서 읽는다.
UTC 0시를 쓰면 한국 시간 09:00 에 사용량이 0 으로 돌아가고, 00:00~09:00 사용분이 전날에 들어간다.

DB 의 시각(`analyses.created_at`)은 그대로 UTC 로 저장한다. 비교할 때만 한국 시간 0시를 UTC 로 바꾼다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

#: 한국 시간(UTC+9). 서머타임이 없어 고정 오프셋으로 충분하다.
KST = timezone(timedelta(hours=9), "KST")


def today_start_iso(now: datetime | None = None) -> str:
    """한국 시간 오늘 0시를 UTC ISO 문자열로. `analyses.created_at` 과 문자열로 비교할 수 있다."""
    current = (now or datetime.now(timezone.utc)).astimezone(KST)
    start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc).isoformat()


def kst_text(iso: str) -> str:
    """UTC ISO 시각을 한국 시간 `YYYY-MM-DD HH:MM:SS` 로. 읽을 수 없으면 앞 19자를 그대로 쓴다."""
    try:
        moment = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return str(iso or "")[:19].replace("T", " ")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S")


def kst_date(iso: str) -> str:
    """UTC ISO 시각이 한국 시간으로 어느 날인지 (`YYYY-MM-DD`)."""
    return kst_text(iso)[:10]


def daily_token_status(storage=None, now: datetime | None = None) -> dict:
    """오늘(한국 시간) 누적 토큰과 한도. 한도 0 은 확인하지 않음이다.

    실패한 분석이 남긴 토큰도 합계에 들어간다 (`Storage.tokens_used_since`).
    """
    from app.core.config import get_settings
    from app.core.storage import Storage

    storage = storage or Storage()
    limit = int(get_settings().get("analysis.daily_token_limit", 0) or 0)
    used = storage.tokens_used_since(today_start_iso(now))
    return {"used": used, "limit": limit, "exceeded": limit > 0 and used >= limit}
