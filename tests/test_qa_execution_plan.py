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
