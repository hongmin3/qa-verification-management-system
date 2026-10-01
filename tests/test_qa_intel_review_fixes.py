"""코드 리뷰(2026-10-01)에서 찾은 결함과 사용자 결정의 재현 테스트. 가짜 Polarion·가짜 Claude 만 쓴다.

TEST-QAINTEL-014 재시도·중복 제거·기간 실행 경계.

Validates: REQ-QAINTEL-004, REQ-QAINTEL-005, REQ-QAINTEL-006, REQ-QAINTEL-007, REQ-QAINTEL-025, REQ-QAINTEL-027,
REQ-DAILY-022, NFR-DAILY-001
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from app.modules.daily_qa import claude_limits, scheduled_jobs
from app.modules.daily_qa.agent_runner import FakeRunner, RunnerOutcome
from app.modules.daily_qa.change_events import (
    ISSUE_CREATED,
    ISSUE_RD_RESULT_CHANGED,
    detect_issue_events,
    is_significant,
)
from app.modules.daily_qa.intelligence import ANALYSIS_SKILLS
from app.modules.daily_qa.pipeline import run_daily
from app.modules.qa_agent.dashboard import parse_run_period
from app.modules.daily_qa.settings import PolarionSettings
from tests.daily_qa_fixtures import comment, issue_item, make_tc_workbook, rules_ok, srs_item
from tests.qa_intel_harness import DAY1, DAY2, DAY3, Harness

DAY4 = DAY1 + timedelta(days=3)
D1, D2, D3 = DAY1.date(), DAY2.date(), DAY3.date()
EVIDENCE = [{"source_type": "srs", "location": "VP-10 본문", "validity": "Current"}]
FIXED_SKILL = ANALYSIS_SKILLS["FIXED_ISSUE"]
COMMENT_SKILL = ANALYSIS_SKILLS["COMMENT"]
NEW_SKILL = ANALYSIS_SKILLS["NEW_ISSUE"]


def _harness(tmp_path, **settings) -> Harness:
    workbook = make_tc_workbook(tmp_path / "(TC) VXvue_TestCase.xlsx", [("TC_1", "VP-10", "목록 표시"), ("TC_2", "VP-11", "검색")])
    h = Harness(tmp_path, tc_paths=[workbook], **settings)
    h.srs = [srs_item("VP-10", "01-01", "목록"), srs_item("VP-11", "01-02", "검색")]
    h.issues = [issue_item("VP-100", "2026-09-28T00:00:00Z", ["VP-10"], review="", status="open")]
    for target in ("VP-100", "VP-200", "VP-300", "VP-10", "VP-11"):
        h.script[target] = {"verdict": "SPEC_VIOLATION", "evidence": EVIDENCE,
                            "sections": {"duplicate": {"verdict": "NO_DUPLICATE_FOUND", "candidates": []}}}
    return h


def _failing_for(h: Harness, predicate):
    """predicate(task, payload) 가 참인 작업은 실패로 끝내는 가짜 Claude."""
    def produce(task, run):
        payload = json.loads((run.in_dir / f"{task.task_id}.json").read_text(encoding="utf-8"))
        if predicate(task, payload):
            h.calls.append((task.skill, [item.get("target") for item in payload.get("items", [])]))
            return RunnerOutcome(False, 1, 0.0, error="exit=1 결과 파일을 쓰지 못했습니다")
        return h._produce(task, run)
    return FakeRunner(produce)


def _targets(h: Harness, skill: str) -> list[str]:
    return [target for called, targets in h.calls if called == skill for target in targets]


# -- 리뷰 1: 지난 날 기간 실행은 재시도 대기열을 건드리지 않는다 (결정 4) ------------------------------


def test_past_period_run_does_not_abandon_or_rerun_retry_queue(tmp_path):
    h = _harness(tmp_path)
    h.run(DAY1)
    h.issues.append(issue_item("VP-200", "2026-09-29T09:00:00Z", ["VP-11"], review="", status="open"))
    h.run(DAY2)
    h.issues.append(issue_item("VP-300", "2026-09-30T09:00:00Z", ["VP-11"], review="", status="open"))
    failed_day = h.run(DAY3, runner=_failing_for(h, lambda task, payload: any(
        item.get("target") == "VP-300" for item in payload.get("items", []))))
    vp300 = [event for event in h.events(failed_day["run_id"]) if event["entity_id"] == "VP-300"]
    assert vp300 and vp300[0]["analysis_status"] == "failed" and vp300[0]["attempts"] == 1
    calls = len(h.calls)

    period = h.run(DAY3 + timedelta(hours=2), since=D1, until=D2)

    again = h.store.list_events(ids=[vp300[0]["id"]])[0]
    assert again["analysis_status"] == "failed" and again["attempts"] == 1       # 포기하지도, 다시 돌지도 않았다
    assert "VP-300" not in [target for _, targets in h.calls[calls:] for target in targets]
    assert period["status"] == "NO_CHANGE"                                        # D1→D2 의 VP-200 은 이미 분석했다

    # 다음 매일 실행은 그 대기열을 오늘 스냅샷으로 다시 분석한다.
    h.run(DAY4)
    assert h.store.list_events(ids=[vp300[0]["id"]])[0]["analysis_status"] == "done"


# -- 리뷰 3: 한 이벤트의 두 분석은 각자 재시도한다 ---------------------------------------------


def test_event_with_two_analyses_retries_only_the_failed_one(tmp_path):
    h = _harness(tmp_path)
    h.issues = [issue_item("VP-100", "2026-09-28T00:00:00Z", ["VP-10"], review="lab_fixed", status="resolved",
                           cause="캐시 초기화 누락", action="초기화 추가", comment_ids=["c1"])]
    h.comments = {"VP-100": [comment("c1", "처음 댓글입니다 확인 부탁드립니다 자세히")]}
    h.run(DAY1)
    h.issues[0] = issue_item("VP-100", "2026-09-29T09:00:00Z", ["VP-10"], review="lab_fixed", status="resolved",
                             cause="캐시 초기화 누락", action="초기화 추가", comment_ids=["c1", "c2"])
    h.comments["VP-100"].append(comment("c2", "원인은 캐시 초기화 누락으로 확인되었고 재현 절차를 바꿨습니다."))

    day2 = h.run(DAY2, runner=_failing_for(h, lambda task, payload: task.skill == FIXED_SKILL))

    event = next(e for e in h.events(day2["run_id"]) if e["event_type"] == "ISSUE_COMMENT_ADDED")
    assert event["analyses"] == ["COMMENT", "FIXED_ISSUE"]
    assert event["analysis_status"] == "failed"                  # 새 댓글 분석이 성공해도 done 이 아니다
    assert event["remaining_analyses"] == ["FIXED_ISSUE"]
    assert day2["stages"]["FIXED_ISSUE"]["status"] == "failed"
    comment_calls = len(_targets(h, COMMENT_SKILL))

    h.run(DAY3)

    final = h.store.list_events(ids=[event["id"]])[0]
    assert final["analysis_status"] == "done" and final["remaining_analyses"] == []
    assert len(_targets(h, COMMENT_SKILL)) == comment_calls      # 성공한 새 댓글 분석은 다시 돌지 않는다
    assert _targets(h, FIXED_SKILL).count("VP-100") >= 2         # 실패한 수정 완료 분석만 다시 돌았다


def test_two_analyses_failing_in_one_run_count_one_attempt(tmp_path):
    h = _harness(tmp_path)
    h.issues = [issue_item("VP-100", "2026-09-28T00:00:00Z", ["VP-10"], review="lab_fixed", status="resolved", comment_ids=["c1"])]
    h.comments = {"VP-100": [comment("c1", "처음 댓글입니다 확인 부탁드립니다 자세히")]}
    h.run(DAY1)
    h.issues[0] = issue_item("VP-100", "2026-09-29T09:00:00Z", ["VP-10"], review="lab_fixed", status="resolved", comment_ids=["c1", "c2"])
    h.comments["VP-100"].append(comment("c2", "원인은 캐시 초기화 누락으로 확인되었고 재현 절차를 바꿨습니다."))

    day2 = h.run(DAY2, runner=_failing_for(h, lambda task, payload: True))

    event = next(e for e in h.events(day2["run_id"]) if e["event_type"] == "ISSUE_COMMENT_ADDED")
    assert event["analysis_status"] == "failed" and event["attempts"] == 1
    assert sorted(event["remaining_analyses"]) == ["COMMENT", "FIXED_ISSUE"]


# -- 리뷰 4: 기간 실행이 두 날에 걸친 변경을 다시 분석하지 않는다 --------------------------------------


def test_period_spanning_two_analysed_days_creates_nothing_new(tmp_path):
    h = _harness(tmp_path)
    h.issues.append(issue_item("VP-102", "2026-09-28T00:00:00Z", ["VP-10"], review="", status="open", comment_ids=["c1"]))
    h.comments = {"VP-102": [comment("c1", "처음 댓글입니다 확인 부탁드립니다 자세히")]}
    h.run(DAY1)
    # DAY2: SRS 제목, 새 댓글 c2, 새 이슈 VP-200
    h.srs[1] = srs_item("VP-11", "01-02", "검색 화면")
    h.issues[1] = issue_item("VP-102", "2026-09-29T09:00:00Z", ["VP-10"], review="", status="open", comment_ids=["c1", "c2"])
    h.comments["VP-102"].append(comment("c2", "목록이 두 번 그려지는 현상을 추가로 확인했습니다 로그 첨부"))
    h.issues.append(issue_item("VP-200", "2026-09-29T09:00:00Z", ["VP-11"], review="", status="open"))
    h.run(DAY2)
    # DAY3: SRS 본문, 새 댓글 c3, VP-200 상태만
    h.srs[1] = srs_item("VP-11", "01-02", "검색 화면", text="검색 결과를 최신순으로 표시한다")
    h.issues[1] = issue_item("VP-102", "2026-09-30T09:00:00Z", ["VP-10"], review="", status="open", comment_ids=["c1", "c2", "c3"])
    h.comments["VP-102"].append(comment("c3", "재현 빈도는 열 번 중 세 번 정도이며 캐시와 관련 있어 보입니다", "2026-09-30T01:00:00Z"))
    h.issues[2] = issue_item("VP-200", "2026-09-30T09:00:00Z", ["VP-11"], review="", status="in_progress")
    h.run(DAY3)
    events_before, calls_before = len(h.events()), len(h.calls)

    period = h.run(DAY3 + timedelta(hours=1), since=D1)

    detected = json.loads((h.cfg.output_dir / period["run_id"] / "change_events.json").read_text(encoding="utf-8"))
    assert {event["event_type"] for event in detected} >= {"SRS_UPDATED", "ISSUE_COMMENT_ADDED", ISSUE_CREATED}
    assert h.events(period["run_id"]) == [] and len(h.events()) == events_before
    assert len(h.calls) == calls_before and period["summary"]["claude_calls"] == 0
    # 결정 3: 저장하지 않은 이벤트는 새 이벤트로 세지 않는다.
    assert period["status"] == "NO_CHANGE"
    assert period["stages"]["events"]["counts"]["total"] == 0


def test_period_still_finds_a_change_not_yet_seen(tmp_path):
    """대조군: 매일 실행이 보지 못한 끝 상태(같은 날 두 번 바뀐 값의 중간이 아닌 새 값)는 기간 실행이 찾는다."""
    h = _harness(tmp_path)
    h.run(DAY1)
    h.srs[1] = srs_item("VP-11", "01-02", "검색 화면")
    h.run(DAY2)
    h.srs[1] = srs_item("VP-11", "01-02", "검색 화면 개편")    # 매일 실행 없이 바뀐 값
    period = h.run(DAY3, since=D1)
    assert [e["event_type"] for e in h.events(period["run_id"])] == ["SRS_UPDATED"]


# -- 결정 5: 같은 전이가 다시 일어나면 새 이벤트다 -----------------------------------------------


def test_repeated_transition_is_detected_again(tmp_path):
    h = _harness(tmp_path)
    h.script["VP-100"] = {"verdict": "FIX_CONFIRMED_BY_TC", "evidence": EVIDENCE}
    h.run(DAY1)
    fixed = dict(review="lab_fixed", status="resolved", cause="캐시 초기화 누락", action="초기화 추가")
    h.issues[0] = issue_item("VP-100", "2026-09-29T09:00:00Z", ["VP-10"], **fixed)
    h.run(DAY2)
    h.issues[0] = issue_item("VP-100", "2026-09-30T09:00:00Z", ["VP-10"], review="", status="open",
                             cause="캐시 초기화 누락", action="초기화 추가")          # 다시 열림
    h.run(DAY3)
    h.issues[0] = issue_item("VP-100", "2026-10-01T09:00:00Z", ["VP-10"], **fixed)   # 다시 수정됨
    day4 = h.run(DAY4)

    rd = [e for e in h.events(day4["run_id"]) if e["event_type"] == ISSUE_RD_RESULT_CHANGED]
    assert len(rd) == 1 and rd[0]["analyses"] == ["FIXED_ISSUE"]
    assert _targets(h, FIXED_SKILL).count("VP-100") == 2
    # 같은 날 다시 돌려도 겹치지 않는다(REQ-QAINTEL-006 순서 2).
    again = h.run(DAY4 + timedelta(hours=1))
    assert h.events(again["run_id"]) == []


# -- 결정 3·Q3: 실행 결과 ---------------------------------------------------------------


def test_run_without_polarion_is_partial_not_no_change(tmp_path):
    """수집을 건너뛴 실행은 '변경사항 없음' 이 아니다 — 확인하지 못했기 때문이다 (REQ-QAINTEL-007 순서 2)."""
    h = _harness(tmp_path, polarion=PolarionSettings(host="", token="", project_id="", srs_query="type:srs",
                                                     issue_query="type:issue", request_interval_seconds=0))
    outcome = run_daily(h.cfg, today=DAY1, polarion_factory=None, runner=FakeRunner(h._produce), inputs=h.inputs,
                        store=h.store, send_email=lambda *args: {"status": "sent"}, spec_chunks_loader=lambda: ([], {}, []),
                        rules_state=rules_ok(tmp_path))
    assert outcome["stages"]["collect_srs"]["status"] == "skipped"
    assert outcome["status"] == "PARTIAL"
    assert outcome["summary"]["claude_calls"] == 0


# -- 결정 1·2: 기간 입력과 지난 기간의 E 점검 ----------------------------------------------------


def test_until_only_period_starts_the_day_before_until():
    today = date(2026, 9, 30)
    assert parse_run_period("", "2026-09-27", today=today) == ("2026-09-26", "2026-09-27")
    assert parse_run_period("", "", today=today) == ("", "")
    assert parse_run_period("2026-09-29", "", today=today) == ("2026-09-29", "")


def test_past_period_trace_check_never_uses_a_snapshot_after_until(tmp_path):
    h = _harness(tmp_path)
    h.run(DAY2)
    h.run(DAY3)
    outcome = h.run(DAY3 + timedelta(hours=1), since=D1 - timedelta(days=5), until=D1, force_weekly=True)
    assert outcome["stages"]["collect_srs"]["status"] == "failed"
    assert DAY3.date().isoformat() not in outcome["stages"]["collect_srs"]["note"]
    assert outcome["stages"]["E"]["status"] == "skipped"


# -- 결정 6: 제품 잡음 규칙은 기본 규칙에 더한다 -------------------------------------------------


def test_product_noise_patterns_extend_default_noise():
    product = (r"재확인 ?부탁드립니다[.!~\s]*",)
    assert not is_significant({"text": "확인 부탁드립니다"}, 2, product)            # 기본 규칙도 남는다
    assert not is_significant({"text": "재확인 부탁드립니다"}, 2, product)          # 제품 규칙
    assert is_significant({"text": "원인은 캐시 초기화 누락입니다"}, 2, product)


# -- 리뷰 Minor ------------------------------------------------------------------------


def test_fixed_analysis_uses_snapshot_comments_when_live_read_fails(tmp_path):
    """기준 스냅샷 실행은 댓글 본문을 읽지 않는다. 그 뒤 새 댓글로 본문이 저장된 이슈가 수정 완료되었는데
    Polarion 댓글 읽기가 실패하면, 수정 완료 분석은 스냅샷의 댓글 본문을 받는다."""
    h = _harness(tmp_path)
    h.issues = [issue_item("VP-100", "2026-09-28T00:00:00Z", ["VP-10"], review="", status="open", comment_ids=[])]
    h.comments = {"VP-100": []}
    h.run(DAY1)
    h.issues[0] = issue_item("VP-100", "2026-09-29T09:00:00Z", ["VP-10"], review="", status="open", comment_ids=["c1"])
    h.comments["VP-100"].append(comment("c1", "원인은 캐시 초기화 누락으로 보입니다 재현 로그를 첨부합니다"))
    h.run(DAY2)
    h.issues[0] = issue_item("VP-100", "2026-09-30T09:00:00Z", ["VP-10"], review="lab_fixed", status="resolved",
                             cause="캐시 초기화 누락", action="초기화 추가", comment_ids=["c1"])
    h.fail_comments.add("VP-100")
    start = len(h.payloads)
    h.run(DAY3)
    fixed = [item for payload in h.payloads[start:] if payload.get("analysis_type") == "FIXED_ISSUE"
             for item in payload.get("items", []) if item.get("target") == "VP-100"]
    assert fixed, "수정 완료 분석이 돌지 않았다"
    assert "캐시 초기화 누락으로 보입니다" in json.dumps(fixed, ensure_ascii=False)


def test_manual_check_shares_the_remaining_task_budget(tmp_path):
    h = _harness(tmp_path, max_tasks_per_run=1)
    h.inputs.manuals = {"User Manual.txt": "검색 화면 설명"}
    h.run(DAY1)
    h.issues.append(issue_item("VP-200", "2026-09-29T09:00:00Z", ["VP-11"], review="", status="open"))
    h.srs[1] = srs_item("VP-11", "01-02", "검색 화면")
    outcome = h.run(DAY2, force_weekly=True)
    called = h.skills_called()
    assert len(called) == 1                                               # 상한 1 을 분석이 다 썼다
    assert outcome["stages"]["F"]["status"] == "not_due" and "상한" in outcome["stages"]["F"]["note"]
    assert h.store.get_state(h.cfg.state_key("manual_check_due")) == "1"  # 다음 실행으로 미룬다


@pytest.mark.parametrize("text", ["API Error: 401 {\"type\":\"authentication_error\"}", "401 Unauthorized",
                                  "OAuth token has expired. Please run /login"])
def test_cli_auth_messages_are_auth(text):
    assert claude_limits.classify(text).kind == claude_limits.KIND_AUTH


@pytest.mark.parametrize("text", ["결과: DICOM 서버가 HTTP 401 을 돌려주는 이슈입니다", "unauthorized access 화면 문구 오타"])
def test_quoted_401_in_task_text_is_not_auth(text):
    assert claude_limits.classify(text) is None


def test_catchup_is_kept_when_another_run_is_in_progress(tmp_path, monkeypatch):
    from tests.daily_qa_fixtures import make_settings
    from app.modules.daily_qa.pipeline import STATE_CATCHUP_AFTER
    from app.modules.daily_qa.store import DailyQaStore

    root = tmp_path / "root"
    (root / "data").mkdir(parents=True)
    cfg = make_settings(root, tmp_path / "ws")
    store = DailyQaStore(cfg.db_path)
    store.set_state(cfg.state_key(STATE_CATCHUP_AFTER), (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat())
    monkeypatch.setattr(scheduled_jobs, "configured_products", lambda: [cfg.product])
    monkeypatch.setattr(scheduled_jobs, "load", lambda product=None: cfg)
    results = iter([{"status": "running", "product": cfg.slug}, {"status": "launched", "pid": 1, "product": cfg.slug}])
    monkeypatch.setattr(scheduled_jobs, "launch_detached", lambda product, trigger="": next(results))

    assert scheduled_jobs.run_catchups()[0]["status"] == "running"
    assert store.get_state(cfg.state_key(STATE_CATCHUP_AFTER))            # 다른 실행 중이면 catch-up 을 남긴다
    assert scheduled_jobs.run_catchups()[0]["status"] == "launched"
    assert store.get_state(cfg.state_key(STATE_CATCHUP_AFTER)) == ""


def test_answered_questions_are_filtered_by_product_and_include_old_skill_names(tmp_path):
    h = _harness(tmp_path)
    h.run(DAY1)
    run_id = h.store.list_runs(limit=1)[0]["id"]
    h.store.add_question(run_id, "vxvue-manual-completeness", "VP-10", "옛 질문", "이유", "")
    h.store.add_question(run_id, "qa-manual-completeness", "VP-11", "우리 질문", "이유", h.cfg.slug)
    h.store.add_question(run_id, "qa-manual-completeness", "VP-12", "다른 제품 질문", "이유", "other")
    for question in h.store.list_questions(limit=10):
        h.store.answer_question(question["id"], "답", "qa")
    subjects = {item["subject"] for item in h.store.answered_questions("qa-manual-completeness", product=h.cfg.slug,
                                                                         include_legacy=True)}
    assert subjects == {"VP-10", "VP-11"}


def test_crashed_run_keeps_tokens_spent_before_the_crash(tmp_path, monkeypatch):
    from app.modules.daily_qa import pipeline

    h = _harness(tmp_path)
    h.usage = {"input_tokens": 100, "output_tokens": 20, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    h.run(DAY1)
    h.issues.append(issue_item("VP-200", "2026-09-29T09:00:00Z", ["VP-11"], review="", status="open"))

    def boom(self):
        raise RuntimeError("초안 Excel 오류")
    monkeypatch.setattr(pipeline._Run, "_draft_excel", boom)
    outcome = h.run(DAY2)
    assert outcome["status"] == "FAILED"
    assert outcome["summary"]["token_usage"]["total_tokens"] == 120
    assert outcome["summary"]["claude_calls"] == 1
