"""Validates: REQ-QAINTEL-046. Result lookup never invokes AI."""
from tests.qa_intel_harness import Harness
from app.modules.daily_qa import target_reuse


def test_period_and_batch_do_not_change_target_identity(tmp_path):
    h = Harness(tmp_path)
    event = {'id': 1, 'run_id': 'yesterday', 'basis': '2026-10-12', 'event_type': 'SRS_UPDATED',
             'before': {'text': 'old'}, 'after': {'text': 'new'}, 'changed_fields': ['text']}
    key = target_reuse.key('vxvue', 'SPEC_COVERAGE', 'VP-1', [event], 'full-context-hash')
    next_event = {**event, 'id': 400, 'run_id': 'monthly', 'basis': '2026-09-14'}
    assert target_reuse.key('vxvue', 'SPEC_COVERAGE', 'VP-1', [next_event], 'full-context-hash') == key
    assert target_reuse.key('vxvue', 'SPEC_COVERAGE', 'VP-1', [{**event, 'before': {'text': 'different'}}], 'full-context-hash') != key
    assert target_reuse.key('vxvue', 'SPEC_COVERAGE', 'VP-1', [event], 'changed-rules') != key
    assert target_reuse.key('other', 'SPEC_COVERAGE', 'VP-1', [event], 'full-context-hash') != key


def test_only_existing_source_findings_are_returned(tmp_path):
    h = Harness(tmp_path)
    h.store.create_run('original', False, h.cfg.slug)
    finding_id = h.store.add_finding('original', 'qa-new-issue-analysis', 'NEW-001',
        {'subject': 'VP-1', 'verdict': 'SUPPORTED_BY_SPEC', 'summary': 'original', 'analysis_type': 'NEW_ISSUE'}, h.cfg.slug)
    target_reuse.save(h.store.storage, 'key', [finding_id], 'opus')
    assert target_reuse.load(h.store.storage, 'key', h.cfg.slug, 'NEW_ISSUE', 'VP-1')['finding_ids'] == [finding_id]
    assert target_reuse.load(h.store.storage, 'key', 'other', 'NEW_ISSUE', 'VP-1') is None
    assert target_reuse.load(h.store.storage, 'key', h.cfg.slug, 'SPEC_COVERAGE', 'VP-1') is None


def test_reporter_metadata_is_not_analysis_context():
    assert target_reuse.semantic({'reporter_name': 'A', 'author': 'B', 'text': 'same'}) == {'text': 'same'}


def test_next_day_wider_period_links_original_without_ai(tmp_path):
    from dataclasses import replace
    from tests.qa_intel_harness import DAY1, DAY2, DAY3
    from tests.daily_qa_fixtures import rules_ok, srs_item, issue_item
    from tests.test_qa_workflow_settings import workbook
    h = Harness(tmp_path)
    h.cfg = replace(h.cfg, savings_enabled=True)
    path = workbook(tmp_path / 'Checklist.xlsx', ['V1.1.0'])
    h.inputs.tc_paths = [path]; h.inputs.checklist_template = path
    from tests.qa_intel_harness import complete_rules
    rules = complete_rules(tmp_path)
    rules.prompt_path = tmp_path / 'prompt.txt'; rules.prompt_path.write_text('분석 지침', encoding='utf-8')
    h.srs = [srs_item('VP-10', '01', '사양')]
    h.issues = [issue_item('VP-100', '2026-09-28', ['VP-10'])]
    h.run(DAY1, trigger='prepare', rules_state=rules)
    h.issues += [issue_item('VP-200', '2026-09-30', ['VP-10'])]
    from tests.test_qa_savings_review import valid_new
    h.script['VP-200'] = valid_new()
    h.gate_status = {key: 'PASS' for key in ('G1', 'G2', 'G7')}
    first = h.run(DAY2, rules_state=rules)
    original = h.store.list_findings(run_id=first['run_id'])[0]
    h.store.storage.qa_set_processing(original['id'], h.cfg.slug, 'done', 'QA', '확인 완료')
    calls = len(h.calls)
    from datetime import date
    second = h.run(DAY3, since=date(2026, 9, 1), until=date(2026, 10, 1), rules_state=rules)
    assert len(h.calls) == calls
    assert second['summary']['target_reused']
    rows = h.store.storage.qa_search_findings(h.cfg.slug, run_id=second['run_id'])['items']
    assert [row['id'] for row in rows] == [original['id']]
    assert rows[0]['processing_state'] == 'done' and rows[0]['created_at'] == original['created_at']


def test_modified_stored_result_is_not_reused(tmp_path):
    h = Harness(tmp_path)
    h.store.create_run('original', False, h.cfg.slug)
    fid = h.store.add_finding('original', 'qa-new-issue-analysis', 'NEW-1',
        {'subject': 'VP-1', 'verdict': 'SPEC_VIOLATION', 'summary': 'original', 'analysis_type': 'NEW_ISSUE'}, h.cfg.slug)
    target_reuse.save(h.store.storage, 'key', [fid], 'sonnet')
    with h.store.storage.connect() as db:
        db.execute('UPDATE daily_qa_findings SET summary=? WHERE id=?', ('altered', fid))
    assert target_reuse.load(h.store.storage, 'key', h.cfg.slug, 'NEW_ISSUE', 'VP-1') is None
