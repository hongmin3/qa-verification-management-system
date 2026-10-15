"""제품별 자동 점검 정책 (SPEC REQ-QAINTEL-038)."""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta

from app.modules.daily_qa import holidays

LABELS = {"daily": "검증 기간", "weekly": "차수 대기", "off": "검증 종료"}


def _present(value: dict) -> dict:
    enabled = value.get("enabled", True)
    validation_period = value.get("validation_period", True)
    mode = "off" if not enabled else "daily" if validation_period else "weekly"
    return {"enabled": enabled, "validation_period": validation_period, "mode": mode,
            "label": LABELS[mode], "updated_at": value.get("updated_at", "")}


def get_policy(store, slug: str) -> dict:
    raw = store.get_state(f"{slug}:automation_policy")
    return _present(json.loads(raw) if raw else {})


def save_policy(store, slug: str, *, enabled: bool, validation_period: bool, changed_by: str = "") -> dict:
    if type(enabled) is not bool or type(validation_period) is not bool:
        raise ValueError("자동 점검 설정은 참·거짓 값이어야 합니다.")
    return _present(store.save_automation_policy(slug, enabled, validation_period, changed_by))


def history(store, slug: str) -> list[dict]:
    raw = store.get_state(f"{slug}:automation_policy_history")
    return list(reversed(json.loads(raw))) if raw else []


def first_business_day(day: date, cfg, schedule: dict) -> date | None:
    monday = day - timedelta(days=day.weekday())
    for offset in range(5):
        candidate = monday + timedelta(days=offset)
        if not holidays.check(candidate, cfg.root, schedule.get("calendar", "kr"), schedule.get("extra", ())).holiday:
            return candidate
    return None


def is_schedule_day(cfg, store, schedule: dict, day: date) -> bool:
    if not schedule.get("enabled", True) or not cfg.enabled:
        return False
    policy = get_policy(store, cfg.slug)
    if policy["mode"] == "off" or day.weekday() not in holidays.parse_days(schedule.get("day_of_week", "mon-fri")):
        return False
    if policy["mode"] == "weekly":
        return day == first_business_day(day, cfg, schedule)
    return not (schedule.get("skip_holidays", True) and holidays.check(
        day, cfg.root, schedule.get("calendar", "kr"), schedule.get("extra", ())).holiday)


def catchup_allowed(cfg, store, schedule: dict) -> bool:
    return bool(not cfg.savings_enabled and cfg.enabled and schedule.get("enabled", True) and get_policy(store, cfg.slug)["mode"] == "daily")


def next_run(cfg, store, schedule: dict, moment: datetime) -> datetime | None:
    if not cfg.enabled or not schedule.get("enabled", True) or get_policy(store, cfg.slug)["mode"] == "off":
        return None
    hour, _, minute = schedule.get("time", "04:00").partition(":")
    local = moment.astimezone(holidays.KST)
    for offset in range(40):
        day = local.date() + timedelta(days=offset)
        candidate = datetime.combine(day, time(int(hour or 7), int(minute or 30)), tzinfo=holidays.KST)
        if candidate > local and is_schedule_day(cfg, store, schedule, day):
            return candidate
    return None
