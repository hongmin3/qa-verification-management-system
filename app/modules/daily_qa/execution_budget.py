"""Per-call budget, shared across QA products and processes."""
from datetime import datetime
from zoneinfo import ZoneInfo
KST=ZoneInfo('Asia/Seoul')

def _day(now):
    if now.tzinfo is None: now=now.replace(tzinfo=KST)
    return now.astimezone(KST).date().isoformat()

def reserve_call(storage, run_id: str, product: str, now: datetime, per_run: int, per_day: int) -> str | None:
    return storage.qa_reserve_call(run_id,product,_day(now),max(0,per_run),max(0,per_day))

def finish_call(storage, reservation_id: str, outcome: dict) -> None:
    storage.qa_finish_call(reservation_id,outcome)

def usage(storage, now: datetime) -> dict:
    return storage.qa_call_usage(_day(now))

class BudgetExhausted(Exception):
    pass
