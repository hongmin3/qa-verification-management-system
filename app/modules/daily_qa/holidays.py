"""공휴일 판정 (SPEC REQ-QAINTEL-001).

로컬 표(`config/holidays/<이름>.yaml`)만 본다. 외부 AI·웹 검색을 쓰지 않으므로 같은 날짜에는 늘 같은
답이 나온다. 표에 없는 해는 고정 공휴일만 보고 경고를 남긴다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

logger = logging.getLogger("regression_analyzer")

KST = ZoneInfo("Asia/Seoul")
DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


@dataclass(frozen=True)
class HolidayCheck:
    holiday: bool
    name: str = ""
    table_missing: bool = False


@lru_cache(maxsize=8)
def _table(path: str) -> dict:
    file = Path(path)
    if not file.is_file():
        return {}
    return yaml.safe_load(file.read_text(encoding="utf-8")) or {}


def table_path(root: Path, calendar: str = "kr") -> Path:
    return root / "config" / "holidays" / f"{calendar}.yaml"


def check(day: date, root: Path, calendar: str = "kr", extra: tuple[str, ...] = ()) -> HolidayCheck:
    iso = day.isoformat()
    if iso in extra:
        return HolidayCheck(True, "추가 휴일(daily_qa.schedule.extra_holidays)")
    table = _table(str(table_path(root, calendar)))
    years = table.get("years") or {}
    year = years.get(day.year) or years.get(str(day.year))
    if year is not None:
        name = year.get(iso)
        return HolidayCheck(bool(name), str(name or ""))
    name = (table.get("fixed") or {}).get(day.strftime("%m-%d"))
    return HolidayCheck(bool(name), str(name or ""), table_missing=True)


def parse_days(spec: str) -> set[int]:
    """APScheduler 요일 표기(`mon-fri`, `mon,wed`)를 요일 번호(월=0)로 바꾼다."""
    result: set[int] = set()
    for part in (spec or "mon-fri").lower().split(","):
        part = part.strip()
        if part in ("*", ""):
            return set(range(7))
        if "-" in part:
            start, _, end = part.partition("-")
            first, last = DAY_NAMES.index(start[:3]), DAY_NAMES.index(end[:3])
            result.update(range(first, last + 1))
        else:
            result.add(DAY_NAMES.index(part[:3]))
    return result


def next_run(now: datetime, root: Path, day_of_week: str = "mon-fri", at: str = "07:30", calendar: str = "kr",
             extra: tuple[str, ...] = (), skip_holidays: bool = True) -> datetime | None:
    """다음 자동 실행 시각(한국 시간). 요일이 맞고 공휴일이 아닌 가장 가까운 날이다."""
    hour, _, minute = at.partition(":")
    run_time = time(int(hour or 7), int(minute or 30))
    local = now.astimezone(KST)
    days = parse_days(day_of_week)
    for offset in range(0, 40):
        day = (local + timedelta(days=offset)).date()
        candidate = datetime.combine(day, run_time, tzinfo=KST)
        if candidate <= local or day.weekday() not in days:
            continue
        if skip_holidays and check(day, root, calendar, extra).holiday:
            continue
        return candidate
    return None
