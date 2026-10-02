"""QA Intelligence 파이프라인 (SPEC specs/qa-intelligence.md). 가짜 Polarion·가짜 Claude 만 쓴다.

Validates: REQ-QAINTEL-003, REQ-QAINTEL-004, REQ-QAINTEL-006, REQ-QAINTEL-007, REQ-QAINTEL-008,
REQ-QAINTEL-009, REQ-QAINTEL-010, REQ-QAINTEL-025, NFR-QAINTEL-001
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.modules.daily_qa import claude_limits
from app.modules.daily_qa.agent_runner import FakeRunner, RunnerOutcome
from app.modules.daily_qa.pipeline import STATE_CATCHUP_AFTER, STATE_CLAUDE_LIMIT, Inputs, RunLocked, _Lock, run_daily
from app.modules.daily_qa.store import DailyQaStore
from tests.daily_qa_fixtures import FakePolarion, comment, issue_item, make_settings, make_tc_workbook, rules_ok, srs_item

DAY1 = datetime(2026, 9, 29, 7, 30, tzinfo=timezone.utc)   # 화요일
DAY2 = DAY1 + timedelta(days=1)
DAY3 = DAY1 + timedelta(days=2)


def analysis_producer(calls: list):
    """작업 입력의 대상마다 분석 종류에 맞는 결과를 쓴다."""

    def produce(task, run):
        payload = json.loads((run.in_dir / f"{task.task_id}.json").read_text(encoding="utf-8"))
        calls.append((task.skill, [item["target"] for item in payload.get("items", [])]))
        findings = []
        for item in payload.get("items", []):
            kind = payload["analysis_type"]
            target = item["target"]
            evidence = [{"source_type": "srs", "location": f"{(item.get('srs') or {}).get('id') or 'VP-10'} 본문", "validity": "Current"}]
            finding = {"subject": target, "summary": f"{kind} 분석", "evidence": evidence, "confidence": "Review Needed"}
            if kind == "NEW_ISSUE":
                finding.update(verdict="SPEC_VIOLATION", sections={"duplicate": {"verdict": "NO_DUPLICATE_FOUND", "candidates": []}})
            elif kind == "FIXED_ISSUE":
                finding.update(verdict="CONSISTENT_WITH_SPEC", draft_tcs=[{
                    "kind": "수정확인", "title": f"{target} 수정 확인", "test_step": "1. 실행한다.", "expected_result": "1. 정상 표시된다.",
                    "perspective": "DIRECT_FIX"}])
            elif kind == "SPEC_DECISION":
                finding.update(verdict="SUPPORTED_BY_SPEC")
            elif kind == "COMMENT":
                ids = [value["id"] for value in item.get("new_comments", [])]
                finding.update(verdict="ROOT_CAUSE_INFORMATION", evidence=[],
                               sections={"comments": [{"comment_id": ids[0], "summary": "원인", "classification": "ROOT_CAUSE_INFORMATION"}]})
            elif kind == "SPEC_COVERAGE" and task.skill == "qa-spec-change-summary":
                finding.update(verdict="QA_CHECK_NEEDED", action="바뀐 동작 확인", sections={"change": {"summary": "사양 변경"}})
            elif kind == "SPEC_COVERAGE":
                finding.update(verdict="NOT_COVERED", draft_tcs=[{
                    "kind": "Regression", "srs_no": target, "title": f"{target} 신규 검증", "test_step": "1. 실행한다.",
                    "expected_result": "1. 표시된다.", "perspective": "DIRECT_SPEC", "rationale": "기존 TC 없음"}])
            findings.append(finding)
        (run.out_dir / f"{task.task_id}.json").write_text(json.dumps({
            "skill": task.skill, "task_id": task.task_id, "gate_status": {"G1": "PASS"}, "findings": findings,
            "open_questions": [], "human_review_required": True}, ensure_ascii=False), encoding="utf-8")

    return produce


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "root"
    (root / "data").mkdir(parents=True)
    cfg = make_settings(root, tmp_path / "ws")
    workbook = make_tc_workbook(tmp_path / "(TC) VXvue_TestCase.xlsx", [("TC_1", "VP-10", "목록 표시"), ("TC_2", "VP-11", "검색")])
    store = DailyQaStore(cfg.db_path)
    calls: list = []
    state = {"srs": [srs_item("VP-10", "01-01", "목록"), srs_item("VP-11", "01-02", "검색")],
             "issues": [issue_item("VP-100", "2026-09-28T00:00:00Z", ["VP-10"], review="", status="open")],
             "comments": {}}

    def run(day, runner=None, **kwargs):
        polarion = FakePolarion(state["srs"], state["issues"], state["comments"], kwargs.pop("fail_comments", None))
        state["polarion"] = polarion
        return run_daily(cfg, today=day, polarion_factory=lambda: polarion,
                         runner=runner or FakeRunner(analysis_producer(calls)),
                         inputs=Inputs(tc_paths=[workbook]), rules_state=rules_ok(tmp_path), store=store,
                         send_email=lambda *_: {"status": "sent"}, spec_chunks_loader=lambda: ([], {}, []), **kwargs)

    return {"cfg": cfg, "store": store, "calls": calls, "state": state, "run": run}


def test_issue_query_reads_all_issues_and_first_run_is_baseline_only(env):
    outcome = env["run"](DAY1)
    assert env["state"]["polarion"].queries == ["type:srs", "type:issue"]
    assert outcome["status"] == "BASELINE"
    assert env["calls"] == []
    assert outcome["summary"]["claude_calls"] == 0
    assert env["store"].list_events(product="vxvue") == []
    assert (env["cfg"].issue_snapshot_dir / "2026-09-29.json").is_file()
    assert (env["cfg"].srs_snapshot_dir / "2026-09-29.json").is_file()


def test_no_change_run_calls_claude_zero_times(env):
    env["run"](DAY1)
    outcome = env["run"](DAY2)
    assert outcome["status"] == "NO_CHANGE"
    assert env["calls"] == []
    assert outcome["summary"]["claude_calls"] == 0
    assert env["store"].get_run(outcome["run_id"])["status_label"] == "변경사항 없음"


def test_new_issue_detected_on_second_run(env):
    env["run"](DAY1)
    env["state"]["issues"].append(issue_item("VP-200", "2026-09-29T09:00:00Z", ["VP-11"], review="", status="open"))
    outcome = env["run"](DAY2)
    events = env["store"].list_events(product="vxvue", run_id=outcome["run_id"])
    assert [(event["entity_id"], event["event_type"]) for event in events] == [("VP-200", "ISSUE_CREATED")]
    assert env["calls"] == [("qa-new-issue-analysis", ["VP-200"])]
    event = events[0]
    assert event["analysis_required"] and event["analysis_status"] == "done" and event["finding_ids"]
    finding = env["store"].get_finding(event["finding_ids"][0])
    assert finding["analysis_type"] == "NEW_ISSUE" and finding["product"] == "vxvue"
    assert outcome["status"] == "SUCCESS"


def test_status_only_change_records_event_without_ai(env):
    env["run"](DAY1)
    env["state"]["issues"][0] = issue_item("VP-100", "2026-09-29T09:00:00Z", ["VP-10"], review="", status="in_progress")
    outcome = env["run"](DAY2)
    events = env["store"].list_events(product="vxvue", run_id=outcome["run_id"])
    assert [event["event_type"] for event in events] == ["ISSUE_STATUS_ONLY_CHANGED"]
    assert events[0]["analysis_status"] == "not_required"
    assert env["calls"] == []
    assert outcome["summary"]["claude_calls"] == 0


def test_rd_result_fixed_runs_fixed_analyzer_and_drafts_excel(env):
    env["run"](DAY1)
    env["state"]["issues"][0] = issue_item("VP-100", "2026-09-29T09:00:00Z", ["VP-10"], review="lab_fixed", status="resolved",
                                           cause="캐시 초기화 누락", action="초기화 추가")
    outcome = env["run"](DAY2)
    kinds = sorted(event["event_type"] for event in env["store"].list_events(product="vxvue", run_id=outcome["run_id"]))
    assert kinds == ["ISSUE_ACTION_DETAILS_CHANGED", "ISSUE_METADATA_CHANGED", "ISSUE_RD_RESULT_CHANGED", "ISSUE_ROOT_CAUSE_CHANGED"]
    assert env["calls"] == [("qa-fixed-issue-analysis", ["VP-100"])]   # 세 이벤트를 한 분석으로 묶는다
    finding = env["store"].list_findings(run_id=outcome["run_id"], analysis_type="FIXED_ISSUE")[0]
    assert len(finding["event_ids"]) == 3 and finding["draft_tcs"] == []   # 자동 실행은 초안을 만들지 않는다 (REQ-QAINTEL-013)
    axes = [entry["axis"] for entry in finding["sections"]["regression_risk"]["axes"]]
    assert "GENERATOR" in axes   # 제품 설정이 정한 축
    assert not (env["cfg"].output_dir / outcome["run_id"] / "impact_checklist_draft.xlsx").exists()
    # [검증 TC 초안 만들기]를 누르면 TC 후보와 함께 초안 Skill 이 돌고 초안 Excel 이 생긴다 (REQ-QAINTEL-032).
    drafted = env["run"](DAY2.replace(hour=12), on_demand="tc-draft", finding_id=finding["id"])
    assert env["calls"][-1] == ("qa-verification-tc-draft", ["VP-100"])
    saved = env["store"].list_findings(run_id=drafted["run_id"], analysis_type="TC_DRAFT")[0]
    assert saved["draft_tcs"] and saved["sections"]["source_finding"] == finding["id"]
    assert (env["cfg"].output_dir / drafted["run_id"] / "impact_checklist_draft.xlsx").is_file()


def test_rd_result_spec_runs_spec_analyzer(env):
    env["run"](DAY1)
    env["state"]["issues"][0] = issue_item("VP-100", "2026-09-29T09:00:00Z", ["VP-10"], review="lab_inspec", status="open")
    env["run"](DAY2)
    assert env["calls"] == [("qa-spec-decision-analysis", ["VP-100"])]


def test_new_comment_creates_comment_event_and_analysis(env):
    env["state"]["issues"][0] = issue_item("VP-100", "2026-09-28T00:00:00Z", ["VP-10"], review="", status="open", comment_ids=["c1"])
    env["run"](DAY1)
    env["state"]["issues"][0] = issue_item("VP-100", "2026-09-29T09:00:00Z", ["VP-10"], review="", status="open", comment_ids=["c1", "c2"])
    env["state"]["comments"] = {"VP-100": [comment("c1", "처음 댓글입니다 확인 부탁"), comment("c2", "원인은 캐시 초기화 누락으로 확인되었습니다.")]}
    outcome = env["run"](DAY2)
    events = env["store"].list_events(product="vxvue", run_id=outcome["run_id"])
    assert [event["event_type"] for event in events] == ["ISSUE_COMMENT_ADDED"]
    assert [item["id"] for item in events[0]["after"]["comments"]] == ["c2"]
    assert env["calls"] == [("qa-comment-analysis", ["VP-100"])]


def test_noise_comment_is_recorded_without_ai(env):
    env["state"]["issues"][0] = issue_item("VP-100", "2026-09-28T00:00:00Z", ["VP-10"], review="", status="open", comment_ids=[])
    env["run"](DAY1)
    env["state"]["issues"][0] = issue_item("VP-100", "2026-09-29T09:00:00Z", ["VP-10"], review="", status="open", comment_ids=["c9"])
    env["state"]["comments"] = {"VP-100": [comment("c9", "확인했습니다.")]}
    outcome = env["run"](DAY2)
    events = env["store"].list_events(product="vxvue", run_id=outcome["run_id"])
    assert events[0]["event_type"] == "ISSUE_COMMENT_ADDED" and not events[0]["analysis_required"]
    assert env["calls"] == []


def test_srs_and_issue_changes_in_same_run(env):
    env["run"](DAY1)
    env["state"]["srs"][0] = srs_item("VP-10", "01-01", "목록", text="표시 항목은 4개다.")
    env["state"]["issues"].append(issue_item("VP-201", "2026-09-29T09:00:00Z", [], review="", status="open"))
    outcome = env["run"](DAY2)
    skills = sorted(skill for skill, _ in env["calls"])
    assert skills == ["qa-new-issue-analysis", "qa-spec-change-summary"]
    assert outcome["status"] == "SUCCESS"


def test_whitespace_only_srs_change_is_not_an_event(env):
    env["run"](DAY1)
    env["state"]["srs"][0] = srs_item("VP-10", "01-01", "목록  ", text="본문")
    outcome = env["run"](DAY2)
    assert outcome["status"] == "NO_CHANGE"


def test_polarion_failure_does_not_advance_snapshot(env):
    env["run"](DAY1)
    env["state"]["issues"] = []   # 0건 = 비정상
    outcome = env["run"](DAY2)
    assert outcome["stages"]["collect_issues"]["status"] == "failed"
    assert "0건" in outcome["stages"]["collect_issues"]["note"]
    assert not (env["cfg"].issue_snapshot_dir / "2026-09-30.json").exists()
    env["state"]["issues"] = [issue_item("VP-100", "2026-09-28T00:00:00Z", ["VP-10"], review="", status="open"),
                              issue_item("VP-300", "2026-09-30T00:00:00Z", [], review="", status="open")]
    outcome = env["run"](DAY3)
    events = env["store"].list_events(product="vxvue", run_id=outcome["run_id"])
    assert [(event["entity_id"], event["event_type"]) for event in events] == [("VP-300", "ISSUE_CREATED")]


def test_issue_count_drop_is_treated_as_failure(env):
    env["state"]["issues"] = [issue_item(f"VP-{index}", "2026-09-28T00:00:00Z", [], review="") for index in range(100, 110)]
    env["run"](DAY1)
    env["state"]["issues"] = env["state"]["issues"][:3]
    outcome = env["run"](DAY2)
    assert outcome["stages"]["collect_issues"]["status"] == "failed"
    assert "급감" in outcome["stages"]["collect_issues"]["note"]
    assert env["store"].list_events(product="vxvue") == []


def test_ai_failure_keeps_event_for_retry(env):
    env["run"](DAY1)
    env["state"]["issues"].append(issue_item("VP-200", "2026-09-29T09:00:00Z", [], review="", status="open"))
    failing = FakeRunner(lambda task, run: RunnerOutcome(False, 1, 0.0, "exit=1 boom"))
    outcome = env["run"](DAY2, runner=failing)
    event = env["store"].list_events(product="vxvue")[0]
    assert event["analysis_status"] == "failed" and event["attempts"] == 1
    assert outcome["status"] == "PARTIAL"
    env["run"](DAY3)   # 새 변경이 없어도 실패한 이벤트를 다시 분석한다
    event = env["store"].list_events(product="vxvue")[0]
    assert event["analysis_status"] == "done"
    assert ("qa-new-issue-analysis", ["VP-200"]) in env["calls"]


def test_session_limit_stops_ai_keeps_events_pending_and_schedules_catchup(env):
    env["run"](DAY1)
    env["state"]["issues"] += [issue_item("VP-200", "2026-09-29T09:00:00Z", [], review="", status="open")]
    env["state"]["srs"][0] = srs_item("VP-10", "01-01", "목록", text="바뀐 본문")
    reset = DAY2 + timedelta(hours=2)
    runner = FakeRunner(lambda task, run: RunnerOutcome(
        False, 1, 0.0, f"Claude AI usage limit reached|{int(reset.timestamp())}",
        limit=claude_limits.classify(f"Claude AI usage limit reached|{int(reset.timestamp())}")))
    outcome = env["run"](DAY2, runner=runner)
    assert len(runner.calls) == 1   # 첫 한도에서 멈춘다
    assert outcome["status"] == "PARTIAL"
    assert outcome["summary"]["claude_limit"]["kind"] == "SESSION"
    events = env["store"].list_events(product="vxvue")
    assert {event["analysis_status"] for event in events} == {"pending"}
    assert all(event["attempts"] == 0 for event in events)
    cfg = env["cfg"]
    assert env["store"].get_state(cfg.state_key(STATE_CLAUDE_LIMIT))
    assert env["store"].get_state(cfg.state_key(STATE_CATCHUP_AFTER))
    # 한도가 아직 살아 있으면 다음 실행은 Claude 를 부르지 않는다.
    later = env["run"](DAY2 + timedelta(hours=1))
    assert env["calls"] == []
    assert later["stages"]["NEW_ISSUE"]["status"] == "limit"


def test_lock_blocks_concurrent_run(env):
    cfg = env["cfg"]
    with _Lock(cfg.lock_path):
        with pytest.raises(RunLocked):
            env["run"](DAY1)


def test_dry_run_saves_nothing(env):
    env["run"](DAY1)
    env["state"]["issues"].append(issue_item("VP-200", "2026-09-29T09:00:00Z", [], review="", status="open"))
    outcome = env["run"](DAY2, dry_run=True)
    assert env["store"].list_events(product="vxvue") == []
    assert not (env["cfg"].issue_snapshot_dir / "2026-09-30.json").exists()
    change_file = env["cfg"].output_dir / outcome["run_id"] / "change_events.json"
    assert json.loads(change_file.read_text(encoding="utf-8"))[0]["event_type"] == "ISSUE_CREATED"
