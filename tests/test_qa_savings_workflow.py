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


def test_single_call_budget_leaves_other_scope_pending(tmp_path):
    h, rules = prepared(tmp_path)
    h.cfg = replace(h.cfg, savings_per_run=1)
    out = h.run(DAY2, trigger='scheduled', rules_state=rules)
    assert len(h.calls) == 1 and out['summary']['call_budget_exhausted']
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
