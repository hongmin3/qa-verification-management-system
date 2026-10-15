"""Validates: REQ-QAINTEL-042..048. Actual pipeline and SQLite, synthetic external services."""
from dataclasses import replace
from tests.qa_intel_harness import Harness, DAY1, DAY2, complete_rules
from tests.daily_qa_fixtures import srs_item, issue_item
from tests.test_qa_workflow_settings import workbook


def prepared(tmp_path):
    h = Harness(tmp_path)
    h.cfg = replace(h.cfg, savings_enabled=True)
    path = workbook(tmp_path / 'Checklist.xlsx', ['V1.1.0', 'V2.0.0'])
    h.inputs.tc_paths = [path]; h.inputs.checklist_template = path
    rules = complete_rules(tmp_path)
    h.srs = [srs_item('VP-10', '01', '사양')]
    h.issues = [issue_item('VP-100', '2026-09-28', ['VP-10'])]
    h.run(DAY1, trigger='prepare', rules_state=rules)
    h.srs = [srs_item('VP-10', '01', '사양', text='사양 수정')]
    h.issues += [issue_item('VP-200', '2026-09-30', ['VP-10'])]
    return h, rules


def test_schedule_collects_once_analyzes_both_and_sends_one_mail(tmp_path):
    h, rules = prepared(tmp_path)
    out = h.run(DAY2, trigger='scheduled', rules_state=rules)
    assert len(h.calls) == 2 and len(h.sent) == 1
    assert {skill for skill, _ in h.calls} == {'qa-spec-coverage-analysis', 'qa-new-issue-analysis'}
    assert out['summary']['scope_targets'] == {'spec': 1, 'issues': 1}
    assert out['summary']['verification_sheet']['sheet'] == 'V1.1.0'
    for payload in h.payloads:
        for item in payload['items']:
            assert item['verification_mode'] and item['verification_sheet']['sheet'] == 'V1.1.0'
    assert all(event['analysis_status'] != 'done' for event in h.events() if event['analysis_required'])


def test_missing_required_guide_section_calls_no_ai(tmp_path):
    h, rules = prepared(tmp_path)
    rules.guide_path.write_text('# §61 only\nIncomplete rules', encoding='utf-8')
    out = h.run(DAY2, trigger='scheduled', rules_state=rules)
    assert h.calls == []
    assert out['status'] != 'SUCCESS'
    assert any('필수 QA 규칙' in stage.get('note', '') for stage in out['stages'].values())


def test_prepare_saves_target_count_and_preparation_label(tmp_path):
    h, rules = prepared(tmp_path)
    out = h.run(DAY2, trigger='prepare', rules_state=rules)
    assert out['summary']['prepared_targets'] == 2 and h.calls == []
    assert h.store.get_run(out['run_id'])['status_label'] == '분석 준비 완료'


def test_usage_threshold_after_first_call_leaves_other_scope_pending(tmp_path, monkeypatch):
    """호출 횟수 상한 대신 호출 직전 사용률로 멈춘다 (REQ-QAINTEL-043)."""
    from app.core import claude_usage
    monkeypatch.setattr(claude_usage, 'state_path', lambda settings=None: tmp_path / 'no_usage.json')
    h, rules = prepared(tmp_path)
    h.rate_limit = {'unifiedWindows': {'five_hour': {'utilization': 0.82, 'resetsAt': 4102444800}}}
    out = h.run(DAY2, trigger='scheduled', rules_state=rules)
    assert len(h.calls) == 1 and out['summary']['call_budget_exhausted']
    assert out['summary']['usage_guard']['reason'] == '사용률 82% 도달'
    assert len(h.sent) == 1 and out['status'] != 'SUCCESS'


def test_changed_answer_after_prepare_requires_new_plan(tmp_path):
    from app.modules.daily_qa import execution_plan
    from datetime import timedelta
    h, rules = prepared(tmp_path)
    out = h.run(DAY2, trigger='prepare', rules_state=rules)
    plan = h.store.storage.qa_get_plan(out['summary']['plan_id'], h.cfg.slug)
    token = execution_plan.select(h.store.storage, plan['id'], h.cfg.slug,
                                  [plan['targets'][0]['key']], plan['policy'])
    # A newly registered knowledge document changes the full evidence context.
    out = h.run(DAY2 + timedelta(seconds=1), selection_id=token, rules_state=rules,
                spec_chunks_loader=lambda: ([], {}, ['new unreadable document']))
    assert h.calls == [] and out['stages']['selection']['status'] == 'failed'


# --- 메일 단계 기록과 사용량 (REQ-MAIL-002, REQ-QAINTEL-048) ------------------------------

def _run_with_sender(h, rules, sender):
    from app.modules.daily_qa.pipeline import run_daily
    from tests.qa_intel_harness import FakePolarion, FakeRunner
    h.polarion = FakePolarion(h.srs, h.issues, h.comments, h.fail_comments)
    return run_daily(h.cfg, today=DAY2, polarion_factory=lambda: h.polarion, inputs=h.inputs, store=h.store,
                     send_email=sender, runner=FakeRunner(h._produce), rules_state=rules,
                     spec_chunks_loader=lambda: ([], {}, []), trigger='scheduled')


def test_mail_stage_note_masks_recipient_and_records_message_id(tmp_path):
    import re
    h, rules = prepared(tmp_path)
    h.cfg = replace(h.cfg, email_to=('qa.person@example.test',))
    out = _run_with_sender(h, rules, lambda *args: {'status': 'sent', 'recipients': 1, 'message_id': '<abc123@example.test>',
                                                    'recipient_masks': ['qa.***@example.test']})
    note = out['stages']['email']['note']
    assert out['stages']['email']['status'] == 'ok'
    assert re.fullmatch(r'발송 \d{4}-\d{2}-\d{2} \d{2}:\d{2} · 수신 qa\.\*\*\*@example\.test · Message-ID <abc123@example\.test>', note), note
    assert 'qa.person' not in note


def test_mail_stage_note_masks_configured_recipients_when_sender_gives_no_masks(tmp_path):
    h, rules = prepared(tmp_path)
    h.cfg = replace(h.cfg, email_to=('qa.person@example.test',))
    out = _run_with_sender(h, rules, lambda *args: {'status': 'sent'})
    note = out['stages']['email']['note']
    assert '수신 qa.***@example.test' in note and 'qa.person' not in note


def test_run_stores_usage_before_and_mail_shows_this_runs_usage(tmp_path, monkeypatch):
    import json
    from app.core import claude_usage
    state = tmp_path / 'claude_usage.json'
    state.write_text(json.dumps({'windows': {'five_hour': {'used_percent': 5.0, 'resets_at': '2026-10-15 09:00'},
                                             'seven_day': {'used_percent': 70.0, 'resets_at': '2026-10-18 01:00'}},
                                 'measured_at': '2026-09-30 07:00'}), encoding='utf-8')
    monkeypatch.setattr(claude_usage, 'state_path', lambda settings=None: state)
    h, rules = prepared(tmp_path)
    h.usage = {'input_tokens': 1000, 'output_tokens': 134, 'cache_creation_input_tokens': 50, 'cache_read_input_tokens': 50}
    h.rate_limit = {'unifiedWindows': {'five_hour': {'utilization': 0.07, 'resetsAt': 1792022400},
                                       'seven_day': {'utilization': 0.76, 'resetsAt': 1792224000}}}
    out = _run_with_sender(h, rules, lambda *args: h.sent.append(args) or {'status': 'sent'})
    assert out['summary']['usage_before'] == {'five_hour': 5.0, 'seven_day': 70.0, 'measured_at': '2026-09-30 07:00',
                                             'five_hour_resets_kst': '2026-10-15 09:00', 'seven_day_resets_kst': '2026-10-18 01:00'}
    subject, text, html = h.sent[0][:3]
    assert subject.startswith('[QA Agent] ') and '점검 · 검토 필요' in subject
    line = next(row for row in text.splitlines() if row.startswith('- 5시간 사용량'))
    assert '실행 전 5% (2026-09-30 07:00 측정) → 실행 후 7% (+2%p)' in line and '초기화 2026-10-15 09:00' in line
    assert '1,234' in text and '호출 2회' in text
    assert isinstance(out['summary']['next_run'], str) and 'spec_results' in out['summary']


def test_mail_is_sent_even_when_usage_and_next_run_cannot_be_read(tmp_path, monkeypatch):
    from app.core import claude_usage
    from app.modules.daily_qa import automation_policy

    def boom(*args, **kwargs):
        raise RuntimeError('읽을 수 없음')
    monkeypatch.setattr(claude_usage, 'reading_for_run', boom)
    monkeypatch.setattr(automation_policy, 'next_run', boom)
    h, rules = prepared(tmp_path)
    out = _run_with_sender(h, rules, lambda *args: h.sent.append(args) or {'status': 'sent'})
    assert out['email']['status'] == 'sent' and len(h.sent) == 1 and out['stages']['email']['status'] == 'ok'
    subject, text, html = h.sent[0][:3]
    assert subject.startswith('[QA Agent] ')
    assert '사용량 기록 없음 (이번 실행의 호출 기록을 읽지 못했습니다)' in text and '다음 자동 실행' not in text
