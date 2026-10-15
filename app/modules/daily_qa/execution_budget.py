"""호출 기록과 사용률 기준 호출 제어 (REQ-QAINTEL-043). 제품과 프로세스가 같은 기록을 쓴다."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
KST=ZoneInfo('Asia/Seoul')
WINDOWS=(('five_hour','사용률'),('seven_day','주간 사용률'))

def _day(now):
    if now.tzinfo is None: now=now.replace(tzinfo=KST)
    return now.astimezone(KST).date().isoformat()

def reserve_call(storage, run_id: str, product: str, now: datetime) -> str:
    return storage.qa_reserve_call(run_id,product,_day(now))

def finish_call(storage, reservation_id: str, outcome: dict) -> None:
    storage.qa_finish_call(reservation_id,outcome)

def usage(storage, now: datetime) -> dict:
    return storage.qa_call_usage(_day(now))

def _reset_passed(text, now: datetime) -> bool:
    """`2026-10-15 09:00`(한국 시간) 초기화 시각이 지났으면 그 창은 새로 시작된 것이다. 시각을 못 읽으면 False."""
    try:
        return datetime.strptime(str(text or '').strip()[:16], '%Y-%m-%d %H:%M').replace(tzinfo=KST) <= now
    except ValueError:
        return False

def may_call(reading_now: dict, cfg, now: datetime | None = None) -> tuple[bool, str]:
    """호출 직전 확인. `(호출해도 됨, 멈춘 이유)`. 사용률을 모르면 막지 않는다.

    5시간 사용률이 `cfg.usage_guard_five_hour_percent` 이상이거나 주간 사용률이
    `cfg.usage_guard_seven_day_percent` 이상이면 멈춘다. 초기화 시각이 이미 지난 창의 값은 없는 것으로 본다.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None: now=now.replace(tzinfo=KST)
    for window, label in WINDOWS:
        value=(reading_now or {}).get(window)
        if isinstance(value,bool) or not isinstance(value,(int,float)): continue
        if _reset_passed((reading_now or {}).get(f'{window}_resets_kst'), now): continue
        limit=getattr(cfg,f'usage_guard_{window}_percent')
        if value >= limit:
            return False, f'{label} {value:g}% 도달'
    return True, ''

class BudgetExhausted(Exception):
    """사용률 기준에 닿아 새 호출을 하지 않는다."""
