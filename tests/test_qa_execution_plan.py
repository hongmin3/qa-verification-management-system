"""Validates: REQ-QAINTEL-042. Immutable selection and no AI during preparation."""
from datetime import datetime, timedelta, timezone
import pytest
from tests.qa_intel_harness import Harness
from app.modules.daily_qa import execution_plan as plans


def test_plan_selection_expiry_product_and_source_guard(tmp_path):
    h = Harness(tmp_path)
    now = datetime.now(timezone.utc)
    source = tmp_path / 'guide.md'; source.write_text('원문', encoding='utf-8')
    plan = plans.create(h.store.storage, h.cfg.slug, [{'key': 'NEW_ISSUE:VP-1'}],
                        {'events': [], 'srs': [], 'issues': []}, [source], {}, now=now)
    key = plans.select(h.store.storage, plan['id'], h.cfg.slug, ['NEW_ISSUE:VP-1'], {}, now=now)
    assert h.store.storage.qa_claim_selection(key, h.cfg.slug)['keys'] == ['NEW_ISSUE:VP-1']
    assert h.store.storage.qa_claim_selection(key, h.cfg.slug) is None
    for product, keys, when in [('other', ['NEW_ISSUE:VP-1'], now),
                                (h.cfg.slug, [], now), (h.cfg.slug, ['NEW_ISSUE:VP-2'], now),
                                (h.cfg.slug, ['NEW_ISSUE:VP-1'], now + timedelta(minutes=31))]:
        with pytest.raises(ValueError):
            plans.select(h.store.storage, plan['id'], product, keys, {}, now=when)
    source.write_text('수정됨', encoding='utf-8')
    with pytest.raises(ValueError):
        plans.validate(plan, h.cfg.slug, {}, now=now)


def test_public_plan_never_exposes_source_paths_or_corpus(tmp_path):
    h = Harness(tmp_path)
    plan = plans.create(h.store.storage, h.cfg.slug, [], {'secret': 'source'}, [], {})
    public = plans.public(plan)
    assert 'data' not in public and 'files' not in public
    assert public['targets'] == []


def test_prepare_and_selection_execute_only_selected_target(tmp_path):
    from dataclasses import replace
    from tests.qa_intel_harness import DAY1, DAY2
    from tests.daily_qa_fixtures import rules_ok, srs_item, issue_item
    from tests.test_qa_workflow_settings import workbook
    h = Harness(tmp_path)
    h.cfg = replace(h.cfg, savings_enabled=True)
    path = workbook(tmp_path / 'Checklist.xlsx', ['V1.1.0'])
    h.inputs.tc_paths = [path]; h.inputs.checklist_template = path
    from tests.qa_intel_harness import complete_rules
    rule = complete_rules(tmp_path)
    rule.prompt_path = tmp_path / 'instructions.txt'
    rule.prompt_path.write_text('원문 지침', encoding='utf-8')
    h.srs = [srs_item('VP-10', '01', '사양')]
    h.issues = [issue_item('VP-100', '2026-09-28', ['VP-10'])]
    h.run(DAY1, trigger='prepare', rules_state=rule)
    h.issues += [issue_item('VP-200', '2026-09-30', ['VP-10']), issue_item('VP-300', '2026-09-30', ['VP-10'])]
    out = h.run(DAY2, trigger='prepare', rules_state=rule)
    assert h.calls == []
    plan = h.store.storage.qa_get_plan(out['summary']['plan_id'], h.cfg.slug)
    selected_key = next(row['key'] for row in plan['targets'] if row['subject'] == 'VP-200')
    selection = plans.select(h.store.storage, plan['id'], h.cfg.slug, [selected_key], plan['policy'])
    out = h.run(DAY2 + timedelta(seconds=1), selection_id=selection, rules_state=rule)
    assert h.calls and all(target == 'VP-200' for _, targets in h.calls for target in targets)
    assert len(h.calls) == 1  # No automatic retry of an empty response.
    assert any(event['entity_id'] == 'VP-300' and event['analysis_status'] == 'pending' for event in h.events())


# -- 상세 분석 화면용 공개 형식 (REQ-QAINTEL-042) -------------------------------------

CREATED = datetime(2026, 10, 14, 8, 41, 15, tzinfo=timezone.utc)


class _NoStorage:
    def qa_save_plan(self, plan):
        pass


def make_plan(created=CREATED, targets=None, issues=None, events=None):
    """저장소 없이 준비 목록을 만든다. 기본은 작성자·업데이트가 있는 수정 완료 이슈 한 건이다."""
    if targets is None:
        targets = [{'key': 'FIXED_ISSUE:VP-200', 'kind': 'FIXED_ISSUE', 'subject': 'VP-200', 'title': '검색 오류',
                    'state': 'pending', 'source': None}]
    if issues is None:
        issues = [{'id': 'VP-200', 'updated': '2026-10-14T08:00:00Z', 'reporter_id': 'u1', 'reporter_name': '홍길동'}]
    if events is None:
        events = [{'entity_id': 'VP-200', 'event_type': 'ISSUE_RD_RESULT_CHANGED',
                   'analysis_required': True, 'analyses': ['FIXED_ISSUE']}]
    return plans.create(_NoStorage(), 'vxvue', targets, {'events': events, 'srs': [], 'issues': issues},
                        [], {}, now=created)


def test_public_plan_marks_expired_after_30_minutes():
    plan = make_plan(created=datetime(2026, 10, 14, 8, 41, 15, tzinfo=timezone.utc))
    assert plans.public(plan, now=datetime(2026, 10, 14, 9, 11, 16, tzinfo=timezone.utc))['expired'] is True
    assert plans.public(plan, now=datetime(2026, 10, 14, 9, 11, 14, tzinfo=timezone.utc))['expired'] is False


def test_public_plan_rows_carry_kind_reporter_and_update():
    row = plans.public(make_plan(), now=CREATED)['targets'][0]
    assert row['kind_label'] == '수정 완료 이슈'
    assert row['issue_updated_kst'] == '2026-10-14 17:00 (한국 시간)'
    assert row['issue_updated_kst'].endswith('(한국 시간)')
    assert row['reporter_name'] == '홍길동'
    assert row['reason'] == '연구소 결과 변경'


def test_public_plan_spec_target_has_no_fake_reporter_or_update():
    plan = make_plan(targets=[{'key': 'SPEC_COVERAGE:VP-10', 'kind': 'SPEC_COVERAGE', 'subject': 'VP-10',
                               'title': '목록', 'state': 'pending', 'source': None}],
                     events=[{'entity_id': 'VP-10', 'event_type': 'SRS_UPDATED',
                              'analysis_required': True, 'analyses': ['SPEC_COVERAGE']}])
    row = plans.public(plan, now=CREATED)['targets'][0]
    assert (row['kind_label'], row['reason']) == ('사양 변경', 'SRS 변경')
    assert row['reporter_name'] == '' and row['issue_updated_kst'] == ''


def test_public_plan_reporter_falls_back_to_saved_reporters_then_id():
    issues = [{'id': 'VP-200', 'updated': '', 'reporter_id': 'u1', 'reporter_name': ''}]
    plan = make_plan(issues=issues)
    saved = {'VP-200': {'reporter_id': 'u1', 'reporter_name': '김철수', 'issue_updated_at': '2026-10-14T01:00:00Z'}}
    row = plans.public(plan, now=CREATED, reporters=saved)['targets'][0]
    assert row['reporter_name'] == '김철수' and row['issue_updated_kst'] == '2026-10-14 10:00 (한국 시간)'
    row = plans.public(plan, now=CREATED)['targets'][0]
    assert row['reporter_name'] == '이름 미확인 (u1)' and row['issue_updated_kst'] == ''


def test_public_plan_reason_merges_comment_events_into_fixed_issue():
    events = [{'entity_id': 'VP-200', 'event_type': 'ISSUE_RD_RESULT_CHANGED', 'analysis_required': True,
               'analyses': ['FIXED_ISSUE']},
              {'entity_id': 'VP-200', 'event_type': 'ISSUE_COMMENT_ADDED', 'analysis_required': True,
               'analyses': ['COMMENT']}]
    row = plans.public(make_plan(events=events), now=CREATED)['targets'][0]
    assert row['reason'] == '연구소 결과 변경, 댓글 추가'
