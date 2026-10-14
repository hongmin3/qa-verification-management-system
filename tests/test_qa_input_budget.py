"""Validates: REQ-QAINTEL-040. 근거 보존, 입력 분할, 규칙 사전 주입."""
import copy
import json
from dataclasses import replace

from app.modules.daily_qa.packages import Task
from app.modules.daily_qa.agent_runner import build_prompt
from tests.test_qa_intel_token_saving import _workspace


def _restore(payload):
    restored = copy.deepcopy(payload)
    pool = restored.pop('shared_candidates', {})
    for item in restored['items']:
        for kind, candidates in item.get('candidates', {}).items():
            item['candidates'][kind] = [
                {**pool[c['shared_candidate']], **{k: v for k, v in c.items() if k != 'shared_candidate'}}
                if 'shared_candidate' in c else c for c in candidates]
    return restored


def test_shared_candidates_roundtrip_preserves_positions_match_and_source():
    from app.modules.daily_qa.input_budget import compact_payload
    candidate = {'id': 'VP-1', 'ref': 'srs:VP-1', 'text': '원문 " \\ <tag> ' * 300, 'match': 'exact'}
    payload = {'items': [
        {'target': 'VP-10', 'candidates': {'srs': [candidate]}},
        {'target': 'VP-11', 'candidates': {'srs': [{**candidate, 'match': 'BM25'}, {**candidate, 'ref': 'other-page'}]}}]}
    original = copy.deepcopy(payload)
    compact = compact_payload(payload)
    assert _restore(compact) == payload == original
    assert len(compact['shared_candidates']) == 1
    assert len(json.dumps(compact)) < len(json.dumps(payload))


def test_split_tasks_keep_all_items_order_and_single_oversize():
    from app.modules.daily_qa.input_budget import split_tasks, input_text
    items = [{'target': f'VP-{i}', 'issue': {'text': '긴 본문' * size}}
             for i, size in enumerate([100, 100, 400, 100])]
    task = Task('FIX-001', 'qa-fixed-issue-analysis', {'items': items, 'answered_questions': [{'answer': '유지'}]})
    before = copy.deepcopy(task.payload)
    tasks = split_tasks([task], 1000)
    assert len(tasks) == 3
    assert [t.task_id for t in tasks] == ['FIX-001', 'FIX-002', 'FIX-003']
    assert [i for t in tasks for i in t.payload['items']] == items
    assert task.payload == before
    assert all(len(input_text(t.payload)) <= 1000 or len(t.payload['items']) == 1 for t in tasks)
    assert all(t.payload['answered_questions'] == before['answered_questions'] for t in tasks)


def test_oversize_file_keeps_readable_lines_and_all_evidence(tmp_path):
    from app.modules.daily_qa.input_budget import split_tasks
    from app.modules.daily_qa.workspace import write_task_input
    run = _workspace(tmp_path)
    candidates = [{'ref': f'srs:VP-{i}', 'text': '근거' * 600} for i in range(200)]
    task = Task('FIX-001', 'qa-fixed-issue-analysis', {'items': [{'target': 'VP-1', 'candidates': {'srs': candidates}}]})
    tasks = split_tasks([task], 60000)
    assert len(tasks) == 1
    path, _ = write_task_input(run, task.task_id, task.payload)
    text = path.read_text(encoding='utf-8')
    assert len(text) > 200000 and len(text.splitlines()) > 200
    assert json.loads(text) == task.payload
    assert 'srs:VP-199' in text
    assert f'<file path="{run.rel(path)}">' not in build_prompt(task, run)


def test_prompt_includes_skills_and_complete_selected_sections(tmp_path):
    run = _workspace(tmp_path)
    skills = run.root / '.claude/skills'
    (skills / 'qa-fixed-issue-analysis').mkdir(parents=True, exist_ok=True)
    (skills / 'qa-fixed-issue-analysis/SKILL.md').write_text('작업 규칙', encoding='utf-8')
    (skills / 'qa-common-rules/SKILL.md').write_text('공통 규칙', encoding='utf-8')
    product = skills / run.product_skill
    (product / 'SKILL.md').write_text('제품 규칙', encoding='utf-8')
    (product / 'guide-sections.json').write_text(json.dumps({'qa-fixed-issue-analysis': ['6', '3.9', '99']}), encoding='utf-8')
    (run.root / 'rules/qa-guide.md').write_text(
        '# 3. Registry\n## 3.9 Device\n장비\n### 하위\n유지할 하위\n## 3.10 Other\n제외할 항목\n'
        '# 6. Types\n유형\n## 하위 분류\n유지할 분류\n# 60. Other\n다른 절\n', encoding='utf-8')
    prompt = build_prompt(Task('FIX-001', 'qa-fixed-issue-analysis', {}), run)
    for text in ('작업 규칙', '공통 규칙', '제품 규칙', '유지할 하위', '유지할 분류'):
        assert text in prompt
    assert '제외할 항목' not in prompt and '다른 절' not in prompt
    assert '99' in prompt and '추가' in prompt


def test_pipeline_splits_before_task_budget_and_leaves_unprocessed_events(tmp_path):
    from tests.qa_intel_harness import Harness, DAY1, DAY2
    from tests.daily_qa_fixtures import srs_item, issue_item
    h = Harness(tmp_path)
    h.cfg = replace(h.cfg, max_tasks_per_run=1,
                    intelligence=replace(h.cfg.intelligence, max_input_chars=1000))
    h.srs = [srs_item('VP-10', '01-01', '사양')]
    h.issues = [issue_item('VP-100', '2026-09-28T00:00:00Z', ['VP-10'], review='', status='open')]
    h.run(DAY1)
    h.issues += [issue_item(f'VP-{n}', '2026-09-29T09:00:00Z', ['VP-10'], review='', status='open') for n in (200, 201)]
    outcome = h.run(DAY2)
    audit = json.loads((h.cfg.output_dir / outcome['run_id'] / 'audit.json').read_text(encoding='utf-8'))
    assert len(audit['tasks']) == 1
    assert audit['tasks'][0]['input_chars'] > 1000
    assert audit['tasks'][0]['input_oversize'] is True
    assert any(e['entity_id'] == 'VP-201' and e['analysis_status'] == 'pending' for e in h.events())


def test_user_instruction_prompt_is_included_verbatim(tmp_path):
    run = _workspace(tmp_path)
    text = '사용자 기준: 장비 조건과 예외를 모두 검토한다.\n판정 근거를 생략하지 않는다.'
    (run.root / 'rules/instruction-prompt.txt').write_text(text, encoding='utf-8')
    prompt = build_prompt(Task('FIX-001', 'qa-fixed-issue-analysis', {}), run)
    assert text in prompt
