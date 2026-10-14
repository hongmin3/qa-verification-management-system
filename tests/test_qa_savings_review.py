"""Final review regressions: missing reviews, forced Opus, actual period coverage."""
from datetime import date, timedelta
from tests.test_qa_savings_workflow import prepared
from tests.daily_qa_fixtures import issue_item, srs_item
from tests.qa_intel_harness import DAY2, DAY3
from app.modules.daily_qa import execution_plan


def valid_new():
    return {'verdict': 'SPEC_VIOLATION', 'summary': '검토 결과',
        'evidence': [{'source_type': 'srs', 'location': 'VP-10 본문', 'validity': 'Current'}],
        'sections': {'duplicate': {'verdict': 'NO_DUPLICATE_FOUND', 'candidates': []},
          'specification': {'summary': 'VP-10과 관찰 결과 비교'}, 'historical': {'candidates': [], 'flags': []},
          'recommendation': {'actions': ['실제 동작 확인'], 'checks': []}, 'tc_coverage': [],
          'tc_assessment': {'summary': '선택 시트의 후보에 관련 검증 조건이 없어 보완 필요'}}}


def test_missing_fixed_review_axes_must_not_complete_or_cache(tmp_path):
    h, rules = prepared(tmp_path)
    h.srs = [srs_item('VP-10', '01', '사양')]
    h.issues = [issue_item('VP-100', '2026-09-28', ['VP-10'], review='lab_fixed', status='resolved', cause='원인', action='조치')]
    h.gate_status = {key: 'PASS' for key in ('G1','G2','G3','G4','G7')}
    h.script['VP-100'] = {'verdict': 'CONSISTENT_WITH_SPEC', 'summary': '근거만 있고 필수 검토 없음',
        'evidence': [{'source_type': 'srs', 'location': 'VP-10 본문', 'validity': 'Current'}], 'sections': {}}
    out = h.run(DAY2, rules_state=rules)
    assert h.calls
    assert all(event['analysis_status'] != 'done' for event in h.events() if event['analysis_required'])
    assert out['status'] != 'SUCCESS'


def test_each_explicit_opus_review_bypasses_both_caches(tmp_path):
    h, rules = prepared(tmp_path)
    h.gate_status = {key: 'PASS' for key in ('G1','G2','G3','G4','G7')}
    h.script['VP-200'] = valid_new()
    out = h.run(DAY2, trigger='prepare', analysis_scope='issues', rules_state=rules)
    plan = h.store.storage.qa_get_plan(out['summary']['plan_id'], h.cfg.slug)
    key = next(row['key'] for row in plan['targets'] if row['subject'] == 'VP-200')
    for i in (1, 2):
        token = execution_plan.select(h.store.storage, plan['id'], h.cfg.slug, [key], plan['policy'], review=True)
        result = h.run(DAY2 + timedelta(seconds=i), selection_id=token, rules_state=rules)
        assert result['summary']['claude_calls'] == 1
    assert len(h.calls) == 2


def test_historical_gaps_show_actual_end_and_missing_intervals(tmp_path):
    h, rules = prepared(tmp_path)
    h.run(DAY2, trigger='prepare', rules_state=rules)
    out = h.run(DAY3 + timedelta(days=10), trigger='prepare', since=date(2026,9,1), until=date(2026,10,5), rules_state=rules)
    plan = h.store.storage.qa_get_plan(out['summary']['plan_id'], h.cfg.slug)
    coverage = plan['basis']['coverage']['issues']
    assert coverage['start'] == '2026-09-29' and coverage['end'] == '2026-09-30'
    assert coverage['warnings'] and '2026-10-05' in ' '.join(coverage['warnings'])
    assert out['stages']['collect_issues']['status'] == 'partial'
    assert out['summary']['coverage']['issues'] == coverage
