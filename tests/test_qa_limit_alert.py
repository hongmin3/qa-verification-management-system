"""Validates: REQ-QAINTEL-042, REQ-QAINTEL-043, REQ-QAINTEL-049 (사용률 기준 호출 제어, 한도 알림, 이어 하기).

실제 Claude·Polarion·SMTP 를 쓰지 않는다. 주소는 모두 example.test 의 합성 값이다.
"""
import json
from dataclasses import replace
from datetime import timedelta, timezone

import pytest

from app.core import claude_usage, notifier
from app.modules.daily_qa import claude_limits, scheduled_jobs
from app.modules.daily_qa.agent_runner import FakeRunner, RunnerOutcome
from app.modules.daily_qa.claude_limits import KIND_SESSION, KIND_WEEKLY
from app.modules.daily_qa.pipeline import STATE_CATCHUP_AFTER, STATE_CLAUDE_LIMIT
from app.modules.daily_qa.report import build_email
from tests.daily_qa_fixtures import issue_item, srs_item
from tests.qa_intel_harness import DAY1, DAY2, Harness, complete_rules
from tests.test_qa_workflow_settings import workbook

FAR_EPOCH = 4102444800          # 2100-01-01 (사용률 창이 아직 초기화되지 않은 것으로 보이게 한다)
FAR_KST = '2100-01-01 09:00'


def freeze_clock(monkeypatch, moment):
    """파이프라인이 보는 "지금"을 `moment`로 고정한다(실행 날짜는 하네스가 따로 넘긴다)."""
    from datetime import datetime as real_datetime
    from app.modules.daily_qa import pipeline

    class FrozenClock(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)
    monkeypatch.setattr(pipeline, 'datetime', FrozenClock)


@pytest.fixture
def usage_file(tmp_path, monkeypatch):
    path = tmp_path / 'claude_usage.json'
    monkeypatch.setattr(claude_usage, 'state_path', lambda settings=None: path)
    return path


@pytest.fixture
def fake_smtp(monkeypatch):
    sent = []
    monkeypatch.setattr(notifier, 'load_email_settings', lambda: notifier.EmailSettings(
        host='smtp.example.test', port=25, use_tls=True, sender='bot@example.test', recipients=('ops@example.test',),
        username='', password='', cooldown_minutes=180, enabled=True))
    monkeypatch.setattr(notifier, '_send', lambda config, message: sent.append(message))
    monkeypatch.setattr(notifier, 'admin_recipients', lambda settings=None: ('admin@example.test',))

    class Box:
        pass
    box = Box()
    box.sent = sent
    return box


def make_world(tmp_path, targets):
    """절약 모드 제품. 준비 실행 한 번 뒤 새 이슈 `targets`건이 대기 이벤트로 쌓여 있다(작업 하나에 대상 하나)."""
    h = Harness(tmp_path, batch_size=1, max_tasks_per_run=30)
    h.cfg = replace(h.cfg, savings_enabled=True)
    path = workbook(tmp_path / 'Checklist.xlsx', ['V1.1.0'])
    h.inputs.tc_paths = [path]
    h.inputs.checklist_template = path
    h.rules = complete_rules(tmp_path)
    h.srs = [srs_item('VP-10', '01', '사양')]
    h.issues = [issue_item('VP-100', '2026-09-28', ['VP-10'])]
    h.run(DAY1, trigger='prepare', rules_state=h.rules)
    h.issues += [issue_item(f'VP-2{i:02d}', '2026-09-30', ['VP-10']) for i in range(targets)]
    return h


def pending_count(h):
    """아직 한 번도 분석을 시작하지 않은 대기 대상. (가짜 Claude 는 결과 내용을 쓰지 않아 시작한 대상은 실패로 기록된다.)"""
    return len([e for e in h.events() if e['analysis_required'] and e['analysis_status'] == 'pending' and e['attempts'] == 0])


def run_pipeline(h, usage_after_each=None):
    """호출마다 `usage_after_each[i]`(5시간 사용률 %, None 이면 사용률 없음)를 돌려주는 가짜 Claude 로 예약 실행."""
    counter = {'n': 0}
    values = usage_after_each or [None]

    def produce(task, run):
        value = values[min(counter['n'], len(values) - 1)]
        counter['n'] += 1
        h.rate_limit = None if value is None else {'unifiedWindows': {'five_hour': {'utilization': value / 100, 'resetsAt': FAR_EPOCH}}}
        return h._produce(task, run)
    return h.run(DAY2, trigger='scheduled', rules_state=h.rules, runner=FakeRunner(produce))


def limit_runner(kind, reset):
    limit = claude_limits.LimitInfo(kind=kind, reset_at=reset.astimezone(timezone.utc).isoformat(timespec='seconds'),
                                    message='limit reached', detected_at=reset.isoformat())
    return FakeRunner(lambda task, run: RunnerOutcome(False, 1, 0.0, 'limit reached', limit=limit))


def run_pipeline_hitting_limit(h, kind, times=1):
    reset = DAY2 + timedelta(hours=2)
    for index in range(times):
        if index:
            h.store.set_state(h.cfg.state_key(STATE_CLAUDE_LIMIT), '')
            h.issues.append(issue_item(f'VP-3{index:02d}', '2026-09-30', ['VP-10']))
        h.run(DAY2 + timedelta(seconds=index), trigger='scheduled', rules_state=h.rules, runner=limit_runner(kind, reset))


# --- REQ-QAINTEL-043 ---------------------------------------------------------------

def test_runs_all_targets_when_usage_is_low(tmp_path, usage_file, fake_smtp):
    h = make_world(tmp_path, 8)
    run_pipeline(h, usage_after_each=[7.0] * 8)
    assert len(h.calls) == 8 and pending_count(h) == 0 and fake_smtp.sent == []


def test_stops_before_call_at_five_hour_threshold(tmp_path, usage_file, fake_smtp):
    h = make_world(tmp_path, 8)
    out = run_pipeline(h, usage_after_each=[40.0, 79.0, 80.0])
    assert len(h.calls) == 3 and pending_count(h) == 5
    assert out['summary']['usage_guard']['reason'] == '사용률 80% 도달'


def test_weekly_threshold_stops_calls(tmp_path, usage_file, fake_smtp):
    usage_file.write_text(json.dumps({'windows': {'five_hour': {'used_percent': 10.0, 'resets_at': FAR_KST},
                                                  'seven_day': {'used_percent': 90.0, 'resets_at': FAR_KST}},
                                      'measured_at': '2026-10-15 09:00'}), encoding='utf-8')
    h = make_world(tmp_path, 3)
    out = run_pipeline(h, usage_after_each=[5.0])
    assert len(h.calls) == 0 and pending_count(h) == 3
    assert out['summary']['usage_guard']['reason'] == '주간 사용률 90% 도달'


def test_unknown_usage_does_not_block(tmp_path, usage_file, fake_smtp):
    h = make_world(tmp_path, 2)
    out = run_pipeline(h, usage_after_each=[None, None])
    assert len(h.calls) == 2 and 'usage_guard' not in out['summary']
    subject, text, _ = h.summary_sent[0][:3]
    assert '사용률 미확인' in text


def test_stale_saved_reading_is_ignored(tmp_path, usage_file, fake_smtp):
    """저장값의 창이 이미 초기화됐으면(초기화 시각이 지났으면) 그 값으로 막지 않는다."""
    usage_file.write_text(json.dumps({'windows': {'five_hour': {'used_percent': 95.0, 'resets_at': '2026-01-01 09:00'}},
                                      'measured_at': '2026-01-01 05:00'}), encoding='utf-8')
    h = make_world(tmp_path, 2)
    run_pipeline(h, usage_after_each=[7.0, 7.0])
    assert len(h.calls) == 2


def test_call_count_caps_are_not_read(tmp_path, usage_file, fake_smtp):
    from app.modules.daily_qa.settings import DailyQaSettings
    assert not hasattr(DailyQaSettings, 'savings_per_run') and 'savings_per_run' not in DailyQaSettings.__dataclass_fields__
    assert 'savings_per_day' not in DailyQaSettings.__dataclass_fields__


# --- REQ-QAINTEL-049 알림 ------------------------------------------------------------

def test_session_limit_sends_one_admin_mail_per_episode(tmp_path, usage_file, fake_smtp):
    h = make_world(tmp_path, 3)
    run_pipeline_hitting_limit(h, KIND_SESSION, times=2)
    assert [m['To'] for m in fake_smtp.sent] == ['admin@example.test']
    message = fake_smtp.sent[0]
    assert message['Subject'].startswith('[QA Agent] VXvue Claude 세션(5시간)')
    assert '초기화 ' in message['Subject']
    body = message.get_content()
    assert '실행 번호' in body and '남은 대기 건수' in body


def test_different_episode_sends_again(tmp_path, usage_file, fake_smtp):
    h = make_world(tmp_path, 3)
    run_pipeline_hitting_limit(h, KIND_SESSION, times=1)
    h.store.set_state(h.cfg.state_key(STATE_CLAUDE_LIMIT), '')
    h.issues.append(issue_item('VP-399', '2026-09-30', ['VP-10']))
    h.run(DAY2 + timedelta(seconds=5), trigger='scheduled', rules_state=h.rules, runner=limit_runner(KIND_SESSION, DAY2 + timedelta(hours=9)))
    assert len(fake_smtp.sent) == 2


def test_limit_alert_skipped_without_admin_email(tmp_path, usage_file, fake_smtp, monkeypatch):
    monkeypatch.setattr(notifier, 'admin_recipients', lambda settings=None: ())
    h = make_world(tmp_path, 2)
    run_pipeline_hitting_limit(h, KIND_WEEKLY)
    assert fake_smtp.sent == []


def test_usage_guard_stop_sends_one_mail_naming_the_percent(tmp_path, usage_file, fake_smtp):
    h = make_world(tmp_path, 6)
    run_pipeline(h, usage_after_each=[40.0, 82.0])
    assert len(h.calls) == 2
    assert [m['To'] for m in fake_smtp.sent] == ['admin@example.test']
    assert fake_smtp.sent[0]['Subject'].startswith('[QA Agent] VXvue Claude 사용률 82% 도달 · 초기화')


def test_mail_failure_does_not_stop_the_run(tmp_path, usage_file, fake_smtp, monkeypatch):
    def boom(config, message):
        raise OSError('smtp down')
    monkeypatch.setattr(notifier, '_send', boom)
    h = make_world(tmp_path, 3)
    out = run_pipeline(h, usage_after_each=[85.0])
    assert out['summary']['usage_guard'] and len(h.calls) == 1


# --- notifier ------------------------------------------------------------------------

class _Settings:
    def __init__(self, values, to):
        self.values = values
        self.secrets = type('S', (), {'notify_email_to': to})()

    def get(self, key, default=None):
        return self.values.get(key, default)


def test_admin_recipients_prefers_setting_then_first_notify_address():
    assert notifier.admin_recipients(_Settings({'notifications.admin_email': 'owner@example.test'}, 'a@example.test,b@example.test')) == ('owner@example.test',)
    assert notifier.admin_recipients(_Settings({}, 'a@example.test; b@example.test')) == ('a@example.test',)
    assert notifier.admin_recipients(_Settings({'notifications.admin_email': '  '}, '')) == ()


def test_notify_sends_only_to_given_recipients_once_per_key(tmp_path, monkeypatch):
    from app.core.storage import Storage
    sent = []
    monkeypatch.setattr(notifier, 'load_email_settings', lambda: notifier.EmailSettings(
        host='smtp.example.test', port=25, use_tls=True, sender='bot@example.test', recipients=('ops@example.test',),
        username='', password='', cooldown_minutes=180, enabled=True))
    monkeypatch.setattr(notifier, '_send', lambda config, message: sent.append(message))
    storage = Storage(tmp_path / 'n.db')
    for _ in range(2):
        notifier.notify(notifier.KIND_CLAUDE_USAGE_LIMIT, '본문', storage=storage, recipients=('admin@example.test',),
                        subject=notifier.mail_subject('VXvue', 'Claude 시험'), dedupe_key='SESSION:x')
    assert [m['To'] for m in sent] == ['admin@example.test'] and sent[0]['Subject'] == '[QA Agent] VXvue Claude 시험'


# --- 이어 하기 -----------------------------------------------------------------------

def _catchup_env(h, monkeypatch):
    monkeypatch.setattr(scheduled_jobs, 'configured_products', lambda settings=None: ['VXvue'])
    monkeypatch.setattr(scheduled_jobs, 'load', lambda product=None: h.cfg)
    monkeypatch.setattr(scheduled_jobs, 'schedule_settings', lambda settings=None: {
        'enabled': True, 'day_of_week': 'mon-fri', 'time': '04:00', 'skip_holidays': True, 'calendar': 'kr', 'extra': (), 'run_missed': True})


def test_catchup_resumes_pending_in_savings_mode(tmp_path, usage_file, fake_smtp, monkeypatch):
    h = make_world(tmp_path, 3)
    reset = DAY2 + timedelta(minutes=5)
    h.run(DAY2, trigger='scheduled', rules_state=h.rules, runner=limit_runner(KIND_SESSION, reset))
    _catchup_env(h, monkeypatch)
    assert h.store.get_state(h.cfg.state_key(STATE_CATCHUP_AFTER))
    assert scheduled_jobs.catchup_due_products(DAY2 + timedelta(minutes=6)) == []
    assert scheduled_jobs.catchup_due_products(DAY2 + timedelta(minutes=11)) == ['VXvue']


def test_usage_guard_stop_also_schedules_catchup(tmp_path, usage_file, fake_smtp, monkeypatch):
    freeze_clock(monkeypatch, DAY2)
    h = make_world(tmp_path, 4)
    # 실행 시각(DAY2)은 한국 시간 16:30 이다. 5시간 창이 17:00 에 초기화된다.
    usage_file.write_text(json.dumps({'windows': {'five_hour': {'used_percent': 85.0, 'resets_at': '2026-09-30 17:00'}}}), encoding='utf-8')
    run_pipeline(h)
    assert len(h.calls) == 0 and pending_count(h) == 4
    _catchup_env(h, monkeypatch)
    assert scheduled_jobs.catchup_due_products(DAY2 + timedelta(minutes=10)) == []
    assert scheduled_jobs.catchup_due_products(DAY2 + timedelta(minutes=40)) == ['VXvue']


def test_unknown_reset_time_schedules_no_catchup(tmp_path, usage_file, fake_smtp, monkeypatch):
    h = make_world(tmp_path, 3)
    usage_file.write_text(json.dumps({'windows': {'seven_day': {'used_percent': 95.0, 'resets_at': None}}}), encoding='utf-8')
    run_pipeline(h)
    assert len(h.calls) == 0
    assert h.store.get_state(h.cfg.state_key(STATE_CATCHUP_AFTER)) in ('', None)


# --- 메일 제목 -----------------------------------------------------------------------

def test_workflow_email_subject_names_limit():
    summary = {'savings_enabled': True, 'pending_analysis_events': 3, 'claude_limit': {
        'kind': 'SESSION', 'label': 'Claude 세션(5시간) 사용량 한도', 'reset_kst': '2026-10-15 15:00'}}
    subject, _, _ = build_email('20261015-040000-vxvue', '일부 실패', {}, summary, 'https://qa.example/qa-agent/runs/x', '', 'VXvue')
    assert subject.startswith('[QA Agent] VXvue ') and subject.endswith('Claude 세션(5시간) 사용량 한도')
    stopped = {'savings_enabled': True, 'pending_analysis_events': 3, 'usage_guard': {'reason': '사용률 80% 도달', 'reset_kst': '2026-10-15 15:00'}}
    subject, text, _ = build_email('20261015-040000-vxvue', '일부 실패', {}, stopped, 'https://qa.example/qa-agent/runs/x', '', 'VXvue')
    assert subject.startswith('[QA Agent] VXvue ') and subject.endswith('사용률 80% 도달')
    assert '사용률 80% 도달' in text


# --- 수정 1차: 발송 실패 뒤 재시도, 모든 멈춤에 이어 하기 -------------------------------------

def test_failed_send_releases_the_episode_so_the_next_stop_retries(tmp_path, usage_file, fake_smtp, monkeypatch):
    state = {'fail': True}

    def flaky(config, message):
        if state['fail']:
            raise OSError('smtp down')
        fake_smtp.sent.append(message)
    monkeypatch.setattr(notifier, '_send', flaky)
    h = make_world(tmp_path, 3)
    run_pipeline_hitting_limit(h, KIND_SESSION)
    assert fake_smtp.sent == []                      # 첫 발송 실패
    state['fail'] = False
    h.store.set_state(h.cfg.state_key(STATE_CLAUDE_LIMIT), '')
    h.issues.append(issue_item('VP-398', '2026-09-30', ['VP-10']))
    h.run(DAY2 + timedelta(seconds=7), trigger='scheduled', rules_state=h.rules, runner=limit_runner(KIND_SESSION, DAY2 + timedelta(hours=2)))
    assert [m['To'] for m in fake_smtp.sent] == ['admin@example.test']      # 같은 묶음의 다음 멈춤이 정확히 한 통 보낸다
    h.store.set_state(h.cfg.state_key(STATE_CLAUDE_LIMIT), '')
    h.issues.append(issue_item('VP-397', '2026-09-30', ['VP-10']))
    h.run(DAY2 + timedelta(seconds=9), trigger='scheduled', rules_state=h.rules, runner=limit_runner(KIND_SESSION, DAY2 + timedelta(hours=2)))
    assert len(fake_smtp.sent) == 1                  # 보낸 뒤에는 다시 보내지 않는다


def test_notify_failure_releases_claim_but_success_keeps_it(tmp_path, monkeypatch):
    from app.core.storage import Storage
    sent, state = [], {'fail': True}

    def flaky(config, message):
        if state['fail']:
            raise OSError('smtp down')
        sent.append(message)
    monkeypatch.setattr(notifier, 'load_email_settings', lambda: notifier.EmailSettings(
        host='smtp.example.test', port=25, use_tls=True, sender='bot@example.test', recipients=('ops@example.test',),
        username='', password='', cooldown_minutes=180, enabled=True))
    monkeypatch.setattr(notifier, '_send', flaky)
    storage = Storage(tmp_path / 'n.db')
    call = lambda: notifier.notify(notifier.KIND_CLAUDE_USAGE_LIMIT, '본문', storage=storage, recipients=('admin@example.test',), dedupe_key='WEEKLY:x')
    assert call()['status'] == 'failed'
    state['fail'] = False
    assert call()['status'] == 'sent'
    assert call()['status'] == 'skipped_cooldown' and len(sent) == 1


def test_weekly_hard_limit_records_catchup_at_reset_plus_five_minutes(tmp_path, usage_file, fake_smtp):
    h = make_world(tmp_path, 2)
    reset = DAY2 + timedelta(days=3)
    h.run(DAY2, trigger='scheduled', rules_state=h.rules, runner=limit_runner(KIND_WEEKLY, reset))
    assert h.store.get_state(h.cfg.state_key(STATE_CATCHUP_AFTER)) == (reset + timedelta(minutes=5)).isoformat(timespec='seconds')


def test_weekly_utilization_stop_with_reset_three_days_away_records_catchup(tmp_path, usage_file, fake_smtp, monkeypatch):
    freeze_clock(monkeypatch, DAY2)
    h = make_world(tmp_path, 2)
    reset_kst = (DAY2 + timedelta(days=3)).astimezone(claude_usage.KST).strftime('%Y-%m-%d %H:%M')
    usage_file.write_text(json.dumps({'windows': {'seven_day': {'used_percent': 90.0, 'resets_at': reset_kst}}}), encoding='utf-8')
    run_pipeline(h)
    assert len(h.calls) == 0
    expected = (DAY2 + timedelta(days=3, minutes=5)).replace(second=0, microsecond=0).isoformat(timespec='seconds')
    assert h.store.get_state(h.cfg.state_key(STATE_CATCHUP_AFTER)) == expected


def test_window_reset_is_judged_at_the_current_time_not_the_run_start(tmp_path, usage_file, fake_smtp, monkeypatch):
    """실행을 시작한 뒤(16:30 KST)에 저장값의 초기화 시각(17:00 KST)이 지나면 그 창은 새로 시작된 것으로 본다."""
    freeze_clock(monkeypatch, DAY2 + timedelta(hours=1))
    usage_file.write_text(json.dumps({'windows': {'five_hour': {'used_percent': 10.0, 'resets_at': FAR_KST},
                                                  'seven_day': {'used_percent': 90.0, 'resets_at': '2026-09-30 17:00'}},
                                      'measured_at': '2026-09-30 10:00'}), encoding='utf-8')
    h = make_world(tmp_path, 3)
    out = run_pipeline(h, usage_after_each=[5.0])
    assert len(h.calls) == 3 and 'usage_guard' not in out['summary']


def test_old_stored_reading_without_reset_time_does_not_block_forever(tmp_path, usage_file, fake_smtp, monkeypatch):
    freeze_clock(monkeypatch, DAY2)
    usage_file.write_text(json.dumps({'windows': {'seven_day': {'used_percent': 95.0, 'resets_at': None}},
                                      'measured_at': '2026-09-20 09:00'}), encoding='utf-8')       # 7일보다 오래됐다
    h = make_world(tmp_path, 2)
    out = run_pipeline(h, usage_after_each=[5.0])
    assert len(h.calls) == 2 and 'usage_guard' not in out['summary']
