"""QA Agent 점검의 토큰 절약 (2026-10-14 실행 검토 결과).

- 예약 시각 04:00, 사양서 동기화 03:30 (REQ-QAINTEL-001, REQ-SYNC-001)
- 지난 날 대기 이벤트 상한 (REQ-QAINTEL-006 6번)
- 같은 이슈의 새 댓글 분석을 수정 완료 이슈 분석에 합치기 (REQ-QAINTEL-010·013)
- 작업 입력·참고 문서를 프롬프트에 넣기 (REQ-DAILY-016 3번)
- 새 댓글 분석 모델 (REQ-DAILY-016 5번)

Validates: REQ-QAINTEL-001, REQ-QAINTEL-006, REQ-QAINTEL-010, REQ-QAINTEL-013, REQ-DAILY-016
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import yaml

from app.modules.daily_qa import pipeline
from app.modules.daily_qa.agent_runner import PROMPT_INLINE_LIMIT, RunnerOutcome, build_prompt
from app.modules.daily_qa.change_events import COMMENT, FIXED_ISSUE, merge_comment_targets
from app.modules.daily_qa.intelligence import ANALYSIS_SKILLS
from app.modules.daily_qa.packages import Task
from app.modules.daily_qa.rules import RulesState
from app.modules.daily_qa.settings import IntelligenceSettings, _intelligence
from app.modules.daily_qa.workspace import prepare
from tests.daily_qa_fixtures import comment, issue_item, rules_ok, srs_item
from tests.qa_intel_harness import DAY1, DAY2, DAY3, Harness

REPO_ROOT = Path(__file__).resolve().parents[1]
DAY4 = DAY3 + timedelta(days=1)
FIXED_SKILL = ANALYSIS_SKILLS[FIXED_ISSUE]
COMMENT_SKILL = ANALYSIS_SKILLS[COMMENT]


def _targets(h: Harness, skill: str) -> list[str]:
    return [target for called, targets in h.calls if called == skill for target in targets]


def _audit(h: Harness, outcome: dict) -> dict:
    return json.loads((h.cfg.output_dir / outcome["run_id"] / "audit.json").read_text(encoding="utf-8"))


# -- 예약 시각 -----------------------------------------------------------------------------------


def test_qa_check_runs_at_0400_and_spec_sync_30_minutes_earlier():
    config = yaml.safe_load((REPO_ROOT / "config.yaml").read_text(encoding="utf-8"))
    product = yaml.safe_load((REPO_ROOT / "config" / "products" / "vxvue.yaml").read_text(encoding="utf-8"))
    assert config["daily_qa"]["schedule"]["time"] == "04:00"
    assert product["sync"]["schedule_time"] == "03:30"


def test_operating_config_uses_sonnet_for_small_tasks_and_caps_backlog():
    intelligence = yaml.safe_load((REPO_ROOT / "config.yaml").read_text(encoding="utf-8"))["daily_qa"]["intelligence"]
    assert intelligence["comment_model"] == "claude-sonnet-5-5"
    assert intelligence["audit_model"] == "claude-sonnet-5-5"
    assert intelligence["backlog_max_events"] == 10


def test_empty_comment_model_follows_light_model():
    values = {"ai.claude.models.light": "light-model"}
    settings = _intelligence(lambda key, default=None: values.get(key, default))
    assert settings.comment_model == "light-model" and settings.audit_model == "light-model"
    assert settings.backlog_max_events == 10


# -- 지난 날 대기 이벤트 상한 -------------------------------------------------------------------


def _open_issue(item_id: str, updated: str) -> dict:
    return issue_item(item_id, updated, [], review="", status="open")


def test_backlog_events_are_capped_oldest_first_but_todays_events_are_not(tmp_path):
    h = Harness(tmp_path, intelligence=IntelligenceSettings(backlog_max_events=1))
    h.srs = [srs_item("VP-10", "01-01", "목록")]
    h.issues = [_open_issue("VP-100", "2026-09-28T00:00:00Z")]
    h.run(DAY1)
    h.issues += [_open_issue(f"VP-20{number}", "2026-09-29T09:00:00Z") for number in range(3)]
    blocked = rules_ok(tmp_path)
    h.run(DAY2, rules_state=RulesState(False, "1.10", "판 불일치", blocked.guide_path, None))   # 세 건 모두 대기
    assert h.calls == []

    h.issues += [_open_issue("VP-300", "2026-09-30T09:00:00Z"), _open_issue("VP-301", "2026-09-30T09:00:00Z")]
    day3 = h.run(DAY3)

    assert sorted(_targets(h, ANALYSIS_SKILLS["NEW_ISSUE"])) == ["VP-200", "VP-300", "VP-301"]
    assert _audit(h, day3)["backlog"] == {"waiting": 3, "taken": 1}
    left = {event["entity_id"]: event for event in h.events(event_types=("ISSUE_CREATED",))}
    assert (left["VP-201"]["analysis_status"], left["VP-201"]["attempts"]) == ("pending", 0)   # 올리지 않은 대기는 그대로

    h.run(DAY4)
    assert sorted(_targets(h, ANALYSIS_SKILLS["NEW_ISSUE"]))[-1] == "VP-301"
    assert "VP-201" in _targets(h, ANALYSIS_SKILLS["NEW_ISSUE"]) and "VP-202" not in _targets(h, ANALYSIS_SKILLS["NEW_ISSUE"])


def test_same_day_rerun_still_takes_all_of_todays_events(tmp_path):
    h = Harness(tmp_path, intelligence=IntelligenceSettings(backlog_max_events=0))
    h.srs = [srs_item("VP-10", "01-01", "목록")]
    h.issues = [_open_issue("VP-100", "2026-09-28T00:00:00Z")]
    h.run(DAY1)
    h.issues += [_open_issue("VP-200", "2026-09-29T09:00:00Z"), _open_issue("VP-201", "2026-09-29T09:00:00Z")]
    blocked = rules_ok(tmp_path)
    h.run(DAY2, rules_state=RulesState(False, "1.10", "판 불일치", blocked.guide_path, None))
    again = h.run(DAY2 + timedelta(hours=5))          # 같은 날 다시 도는 실행 (예: 한도 뒤 재개)
    assert sorted(_targets(h, ANALYSIS_SKILLS["NEW_ISSUE"])) == ["VP-200", "VP-201"]
    assert _audit(h, again)["backlog"] == {"waiting": 0, "taken": 0}


# -- 새 댓글 분석 합치기 ------------------------------------------------------------------------


def _fixed_issue_with_new_comment(tmp_path) -> Harness:
    h = Harness(tmp_path)
    h.srs = [srs_item("VP-10", "01-01", "목록")]
    h.issues = [issue_item("VP-100", "2026-09-28T00:00:00Z", ["VP-10"], review="lab_fixed", status="resolved", comment_ids=["c1"])]
    h.comments = {"VP-100": [comment("c1", "처음 댓글입니다 확인 부탁드립니다 자세히")]}
    h.run(DAY1)
    h.issues[0] = issue_item("VP-100", "2026-09-29T09:00:00Z", ["VP-10"], review="lab_fixed", status="resolved", comment_ids=["c1", "c2"])
    h.comments["VP-100"].append(comment("c2", "원인은 캐시 초기화 누락으로 확인되었고 재현 절차를 바꿨습니다."))
    return h


def test_comment_on_fixed_issue_runs_once_inside_fixed_analysis(tmp_path):
    h = _fixed_issue_with_new_comment(tmp_path)
    h.script["VP-100"] = {"verdict": "CONSISTENT_WITH_SPEC", "evidence": [{"source_type": "srs", "location": "VP-10 본문", "validity": "Current"}]}
    day2 = h.run(DAY2)

    assert h.skills_called() == [FIXED_SKILL]                    # 새 댓글 분석을 따로 부르지 않는다
    item = h.payloads[0]["items"][0]
    assert [entry["id"] for entry in item["new_comments"]] == ["c2"]
    event = next(e for e in h.events(day2["run_id"]) if e["event_type"] == "ISSUE_COMMENT_ADDED")
    assert event["analyses"] == ["COMMENT", "FIXED_ISSUE"]
    assert (event["analysis_status"], event["remaining_analyses"]) == ("done", [])
    assert day2["stages"]["COMMENT"]["status"] == "skipped"


def test_failed_merged_analysis_leaves_both_and_retries_together(tmp_path):
    h = _fixed_issue_with_new_comment(tmp_path)
    h.outcome_override = lambda task, run: RunnerOutcome(False, 1, 0.0, "exit=1 boom")
    day2 = h.run(DAY2)
    event = next(e for e in h.events(day2["run_id"]) if e["event_type"] == "ISSUE_COMMENT_ADDED")
    assert event["analysis_status"] == "failed" and event["attempts"] == 1   # 두 분석이 함께 실패해도 1번
    assert sorted(event["remaining_analyses"]) == ["COMMENT", "FIXED_ISSUE"]

    h.outcome_override = None
    h.script["VP-100"] = {"verdict": "CONSISTENT_WITH_SPEC", "evidence": [{"source_type": "srs", "location": "VP-10 본문", "validity": "Current"}]}
    h.run(DAY3)
    final = h.store.list_events(ids=[event["id"]])[0]
    assert (final["analysis_status"], final["remaining_analyses"]) == ("done", [])
    assert _targets(h, COMMENT_SKILL) == []


def test_comment_only_target_still_runs_comment_analysis():
    comment_event = {"id": 7, "entity_id": "VP-1"}
    fixed_event = {"id": 8, "entity_id": "VP-2"}
    grouped = {FIXED_ISSUE: {"VP-2": [fixed_event]}, COMMENT: {"VP-1": [comment_event]}}
    assert merge_comment_targets(grouped) == {}
    assert grouped[COMMENT] == {"VP-1": [comment_event]}


def test_merge_does_not_duplicate_an_event_shared_by_both_analyses():
    shared = {"id": 3, "entity_id": "VP-1"}
    older = {"id": 2, "entity_id": "VP-1"}
    grouped = {FIXED_ISSUE: {"VP-1": [shared]}, COMMENT: {"VP-1": [older, shared]}}
    assert merge_comment_targets(grouped) == {"VP-1": [2, 3]}
    assert grouped[COMMENT] == {} and grouped[FIXED_ISSUE]["VP-1"] == [shared, older]


# -- 프롬프트에 입력·참고 문서 넣기 ------------------------------------------------------------


def _workspace(tmp_path):
    run = prepare(tmp_path / "ws", tmp_path / "repo", "r1", None, None)
    skill = run.root / ".claude" / "skills" / "qa-common-rules" / "references"
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "output-contract.md").write_text("결과 형식 본문", encoding="utf-8")
    product = run.root / ".claude" / "skills" / "vxvue-qa-rules" / "references"
    product.mkdir(parents=True, exist_ok=True)
    (product / "gates.md").write_text("Gate 본문", encoding="utf-8")
    run.product_skill = "vxvue-qa-rules"
    return run


def test_prompt_inlines_task_input_and_reference_documents(tmp_path):
    run = _workspace(tmp_path)
    (run.in_dir / "FIX-001.json").write_text('{"items": [{"target": "VP-100"}]}', encoding="utf-8")
    prompt = build_prompt(Task("FIX-001", FIXED_SKILL, {}), run)
    assert prompt.startswith(f"/{FIXED_SKILL} runs/r1/in/FIX-001.json")
    assert '<file path="runs/r1/in/FIX-001.json">' in prompt and '"target": "VP-100"' in prompt
    assert "결과 형식 본문" in prompt and "Gate 본문" in prompt
    assert "다시 읽지 않는다" in prompt and "한 응답에서 함께 부른다" in prompt
    assert "runs/r1/context/srs_current.jsonl" in prompt


def test_prompt_skips_input_that_is_too_large_or_excluded_for_cache_key(tmp_path):
    run = _workspace(tmp_path)
    (run.in_dir / "FIX-001.json").write_text("x" * (PROMPT_INLINE_LIMIT + 1), encoding="utf-8")
    task = Task("FIX-001", FIXED_SKILL, {})
    assert '<file path="runs/r1/in/FIX-001.json">' not in build_prompt(task, run)
    (run.in_dir / "FIX-001.json").write_text('{"event_ids": [1]}', encoding="utf-8")
    assert '"event_ids"' not in build_prompt(task, run, inline_input=False)
    assert "결과 형식 본문" in build_prompt(task, run, inline_input=False)


# -- 새 댓글 분석 모델 ------------------------------------------------------------------------


def test_comment_tasks_use_the_comment_model(tmp_path, monkeypatch):
    h = Harness(tmp_path)
    h.srs = [srs_item("VP-10", "01-01", "목록")]
    h.issues = [issue_item("VP-100", "2026-09-28T00:00:00Z", ["VP-10"], review="", status="open", comment_ids=["c1"])]
    h.comments = {"VP-100": [comment("c1", "처음 댓글입니다 확인 부탁드립니다 자세히")]}
    h.run(DAY1)
    h.issues[0] = issue_item("VP-100", "2026-09-29T09:00:00Z", ["VP-10"], review="", status="open", comment_ids=["c1", "c2"])
    h.comments["VP-100"].append(comment("c2", "원인은 캐시 초기화 누락으로 확인되었고 재현 절차를 바꿨습니다."))
    h.cfg = replace(h.cfg, intelligence=replace(h.cfg.intelligence, comment_model="comment-model"), claude_model="main-model")
    used: list[tuple[str, str]] = []

    class Recording:
        def __init__(self, command, token, timeout, model="", effort="", cancel_check=None):
            self.model = model

        def run(self, task, run):
            used.append((task.task_id, self.model))
            return h._produce(task, run)

    monkeypatch.setattr(pipeline, "ClaudeRunner", Recording)
    outcome = h.run(DAY2, runner=None)
    assert [model for task_id, model in used if task_id.startswith("CMT-")] == ["comment-model"]
    assert _audit(h, outcome)["ai_settings"]["comment_model"] == "comment-model"


def test_merged_comment_without_target_finding_stays_retryable(tmp_path):
    h = _fixed_issue_with_new_comment(tmp_path)
    # 정상 JSON이지만 해당 이슈 결과가 없다.
    result = h.run(DAY2)
    event = next(e for e in h.events(result['run_id']) if e['event_type'] == 'ISSUE_COMMENT_ADDED')
    assert event['analysis_status'] == 'failed'
    assert 'COMMENT' in event['remaining_analyses']
    assert event['attempts'] == 1


def test_rejected_merged_finding_does_not_complete_comment(tmp_path):
    h = _fixed_issue_with_new_comment(tmp_path)
    h.script['VP-100'] = {'verdict': 'CONSISTENT_WITH_SPEC',
        'evidence': [{'source_type': 'srs', 'location': 'VP-99999 본문', 'validity': 'Current'}]}
    result = h.run(DAY2)
    assert any(t.get('rejected') for t in _audit(h, result)['tasks'])
    event = next(e for e in h.events(result['run_id']) if e['event_type'] == 'ISSUE_COMMENT_ADDED')
    assert event['analysis_status'] == 'failed'
    assert 'COMMENT' in event['remaining_analyses']
