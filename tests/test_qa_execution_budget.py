"""Validates: REQ-QAINTEL-043 (호출 기록과 사용률 기준 호출 제어)."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

from app.core.storage import Storage
from app.modules.daily_qa.execution_budget import reserve_call, finish_call, usage, may_call

NOW = datetime(2026, 10, 14, 5, tzinfo=timezone.utc)
CFG = SimpleNamespace(usage_guard_five_hour_percent=80, usage_guard_seven_day_percent=90)


def test_reservations_are_recorded_without_any_count_cap(tmp_path):
    store = Storage(tmp_path / 'app.db')

    def reserve(i):
        return reserve_call(store, str(i // 2), str(i % 2), NOW)
    with ThreadPoolExecutor(max_workers=8) as pool:
        tickets = list(pool.map(reserve, range(16)))
    assert all(tickets) and len(set(tickets)) == 16
    assert usage(store, NOW)['used'] == 16


def test_cancelled_or_unknown_call_still_counts_and_unstarted_is_returned(tmp_path):
    store = Storage(tmp_path / 'app.db')
    ticket = reserve_call(store, 'run', 'vxvue', NOW)
    finish_call(store, ticket, {'cancelled': True})
    assert usage(store, NOW)['used'] == 1
    ticket = reserve_call(store, 'run', 'vxvue', NOW)
    finish_call(store, ticket, {'cli_started': False})
    assert usage(store, NOW)['used'] == 1
    assert usage(store, NOW + timedelta(days=1))['used'] == 0


def test_may_call_below_thresholds():
    assert may_call({'five_hour': 79.9, 'seven_day': 89.9}, CFG) == (True, '')


def test_may_call_stops_at_five_hour_threshold():
    assert may_call({'five_hour': 80.0, 'seven_day': 10.0}, CFG) == (False, '사용률 80% 도달')


def test_may_call_stops_at_weekly_threshold():
    assert may_call({'five_hour': 10.0, 'seven_day': 90.0}, CFG) == (False, '주간 사용률 90% 도달')


def test_may_call_unknown_never_blocks():
    assert may_call({'five_hour': None, 'seven_day': None}, CFG) == (True, '')
    assert may_call({}, CFG) == (True, '')


def test_may_call_ignores_a_window_that_already_reset():
    reading = {'five_hour': 95.0, 'five_hour_resets_kst': '2026-10-14 08:00', 'seven_day': None}
    assert may_call(reading, CFG, now=NOW)[0] is True        # NOW 는 한국 시간 14:00 이라 08:00 초기화가 이미 지났다
    reading['five_hour_resets_kst'] = '2026-10-14 15:00'
    assert may_call(reading, CFG, now=NOW) == (False, '사용률 95% 도달')


def test_guard_settings_are_clamped(monkeypatch, tmp_path):
    from app.modules.daily_qa import settings as qa_settings
    values = {'daily_qa.usage_guard.five_hour_percent': 30, 'daily_qa.usage_guard.seven_day_percent': 'abc'}
    assert qa_settings.usage_guard_percent(lambda key, default=None: values.get(key, default), 'five_hour') == 80
    assert qa_settings.usage_guard_percent(lambda key, default=None: values.get(key, default), 'seven_day') == 90
    values['daily_qa.usage_guard.five_hour_percent'] = 100
    assert qa_settings.usage_guard_percent(lambda key, default=None: values.get(key, default), 'five_hour') == 100


def test_old_reading_without_reset_time_is_ignored():
    # NOW 는 한국 시간 2026-10-14 14:00. 5시간보다 오래된 측정값은 초기화 시각이 없어도 없는 것으로 본다.
    stale = {'five_hour': 95.0, 'five_hour_resets_kst': '', 'five_hour_measured_at': '2026-10-14 08:00',
             'seven_day': 95.0, 'seven_day_resets_kst': '', 'seven_day_measured_at': '2026-10-06 08:00'}
    assert may_call(stale, CFG, now=NOW) == (True, '')


def test_fresh_reading_without_reset_time_still_stops():
    fresh = {'five_hour': 85.0, 'five_hour_resets_kst': '', 'five_hour_measured_at': '2026-10-14 12:00', 'seven_day': 10.0}
    assert may_call(fresh, CFG, now=NOW) == (False, '사용률 85% 도달')
    weekly = {'five_hour': 10.0, 'seven_day': 95.0, 'seven_day_resets_kst': '', 'seven_day_measured_at': '2026-10-10 12:00'}
    assert may_call(weekly, CFG, now=NOW) == (False, '주간 사용률 95% 도달')
    assert may_call({'five_hour': 85.0}, CFG, now=NOW) == (False, '사용률 85% 도달')  # 측정 시각이 없으면 이번 실행의 값이다
