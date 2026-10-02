"""자동 실행은 핵심만, TC·매뉴얼 비교는 사람이 요청할 때만 (REQ-QAINTEL-031~034).

가짜 Polarion·가짜 Claude(`Harness`)로 돌린다. 실제 Polarion·Claude 를 부르지 않는다.

Validates: REQ-QAINTEL-031, REQ-QAINTEL-032, REQ-QAINTEL-034, REQ-QAINTEL-013, REQ-QAINTEL-016
"""

from __future__ import annotations

from datetime import timedelta

import app.modules.daily_qa.pipeline as pipeline
from app.modules.daily_qa import claude_limits
from app.modules.daily_qa.change_events import FIXED_ISSUE, SPEC_COVERAGE
from tests.daily_qa_fixtures import issue_item, srs_item
from tests.qa_intel_harness import DAY1, DAY2, Harness
from tests.test_qa_intel_coverage import checklist

LATER = DAY2 + timedelta(hours=1)


def world(tmp_path, manuals: dict | None = None) -> Harness:
    tc = checklist(tmp_path / "(TC) R-20-643_VXvue_변경사항 영향성평가_Checklist.xlsx",
                   [("TC-310", "VP-767", "Retake 후 목록", "1. 목록이 바뀐다.")])
    h = Harness(tmp_path, tc_paths=[tc], manuals=manuals or {"op.txt": "Retake 하면 목록을 다시 읽습니다.\n\n" * 3})
    h.srs = [srs_item("VP-767", "07-01-01", "Retake 후 Protocol 목록", text="Retake 하면 목록을 다시 읽는다.")]
    h.issues = [issue_item("VP-301", "2026-09-01T00:00:00Z", ["VP-767"], review="", title="Retake 후 목록 안 바뀜")]
    h.run(DAY1)                                                                   # 기준 스냅샷
    h.srs[0] = srs_item("VP-767", "07-01-01", "Retake 후 Protocol 목록",
                        text="Retake 하면 목록을 다시 읽는다. Reload 뒤에도 목록 상태를 유지한다.")
    h.issues[0] = issue_item("VP-301", "2026-09-29T09:00:00Z", ["VP-767"], review="lab_fixed", title="Retake 후 목록 안 바뀜",
                             cause="목록 캐시", action="Retake 때 캐시를 비움")
    h.script["VP-767"] = {"verdict": "QA_CHECK_NEEDED", "summary": "Reload 뒤 목록 유지 조건이 생겼다.",
                          "action": "Reload 뒤 목록 유지 확인\n연구소에 저장 시점 확인",
                          "sections": {"change": {"summary": "Reload 뒤 목록 상태 유지가 추가됐다.",
                                                  "changed_requirements": ["Reload 뒤에도 목록 상태를 유지한다."]},
                                       "related_issues": [{"issue_id": "VP-301", "relation": "PAST_FIXED", "reason": "같은 목록"},
                                                          {"issue_id": "VP-99999", "relation": "PAST_FIXED", "reason": "없는 번호"}]}}
    h.script["VP-301"] = {"verdict": "CONSISTENT_WITH_SPEC", "summary": "조치가 사양과 맞다.",
                          "evidence": [{"source_type": "srs", "location": "VP-767 본문", "validity": "Current"}],
                          "draft_tcs": [{"kind": "수정확인", "srs_no": "VP-767", "change": "목록", "title": "Retake 목록 갱신",
                                         "precondition": "1. 로그인", "test_step": "1. Retake 한다.", "expected_result": "1. 목록이 바뀐다."}]}
    return h


def _items(h: Harness):
    return [item for payload in h.payloads for item in payload.get("items", [])]


def test_auto_inputs_have_no_tc_or_manual_candidates(tmp_path):
    h = world(tmp_path)
    h.run(DAY2)
    items = _items(h)
    assert items, "자동 실행이 분석 작업을 만들지 않았다"
    for item in items:
        assert "tcs" not in (item.get("candidates") or {}), item["target"]
        assert "manuals" not in (item.get("candidates") or {}), item["target"]


def test_spec_change_uses_summary_skill_and_validates_related_issues(tmp_path):
    h = world(tmp_path)
    outcome = h.run(DAY2)
    assert "qa-spec-change-summary" in h.skills_called() and "qa-spec-coverage-analysis" not in h.skills_called()
    finding = h.store.list_findings(run_id=outcome["run_id"], analysis_type=SPEC_COVERAGE)[0]
    assert finding["skill"] == "qa-spec-change-summary" and finding["verdict"] == "QA_CHECK_NEEDED"
    assert [entry["issue_id"] for entry in finding["sections"]["related_issues"]] == ["VP-301"]   # 입력에 없는 번호는 뺀다
    assert finding["related_ids"]["issues"] == ["VP-301"]
    # 저장한 QA 할 일이 카드까지 이어진다 (REQ-QAINTEL-033). 표에 action 칸이 없어 구획에 남는다.
    from app.modules.qa_agent import dashboard as dash

    assert dash.card(finding)["todos"] == ["Reload 뒤 목록 유지 확인", "연구소에 저장 시점 확인"]


def test_auto_context_has_no_tc_index_or_manuals(tmp_path):
    h = world(tmp_path)
    outcome = h.run(DAY2)
    context = h.cfg.product_workspace_dir / "runs" / outcome["run_id"] / "context"
    assert (context / "srs_current.jsonl").is_file()
    assert not (context / "tc_index.jsonl").exists()
    assert not (context / "manuals").exists() or not any((context / "manuals").iterdir())


def test_auto_context_removes_previous_generated_tc_and_manual_files(tmp_path):
    from app.modules.daily_qa.workspace import RunWorkspace, write_context

    run = RunWorkspace(tmp_path, "reused-run")
    write_context(run, [], [{"id": "TC-1"}], {"manual.txt": "old manual"})
    assert (run.context_dir / "tc_index.jsonl").is_file()
    assert (run.context_dir / "manuals" / "manual.txt").is_file()
    write_context(run, [], [], {})
    assert not (run.context_dir / "tc_index.jsonl").exists()
    assert not (run.context_dir / "manuals").exists()


def test_fixed_issue_auto_drafts_are_dropped(tmp_path):
    h = world(tmp_path)
    outcome = h.run(DAY2)
    finding = h.store.list_findings(run_id=outcome["run_id"], analysis_type=FIXED_ISSUE)[0]
    assert finding["draft_tcs"] == []
    assert any("검증 TC 초안" in note for note in finding["sections"]["validation"]["notes"])


def test_auto_run_never_runs_manual_check(tmp_path):
    h = world(tmp_path)
    h.store.set_state(h.cfg.state_key(pipeline.STATE_MANUAL_DUE), "1")
    outcome = h.run(DAY2, force_weekly=True)                                     # 주간 요일 + 미룬 점검 + 매뉴얼 있음
    assert "qa-manual-completeness" not in h.skills_called()
    assert outcome["stages"]["F"]["status"] == "not_due"
    assert "요청할 때만" in outcome["stages"]["F"]["note"]


# -- 요청 실행 (버튼) ------------------------------------------------------------------


def _finding(h: Harness, run_id: str, kind: str) -> dict:
    return h.store.list_findings(run_id=run_id, analysis_type=kind)[0]


def test_tc_check_runs_one_coverage_task_with_tc_candidates(tmp_path):
    h = world(tmp_path)
    auto = h.run(DAY2)
    source = _finding(h, auto["run_id"], SPEC_COVERAGE)
    statuses = {event["id"]: event["analysis_status"] for event in h.events()}
    mails = len(h.sent)
    h.payloads.clear(), h.calls.clear()
    h.script["VP-767"] = {"verdict": "PARTIALLY_COVERED", "summary": "Reload 검증 없음",
                          "evidence": [{"source_type": "srs", "location": "VP-767 본문", "validity": "Current"}],
                          "sections": {"checklist_coverage": {"tcs": [{"tc_id": "TC-310", "decision": "UPDATE_EXISTING",
                                                                        "reason": "옛 Expected"}]}}}
    outcome = h.run(LATER, on_demand="tc-check", finding_id=source["id"])
    assert h.skills_called() == ["qa-spec-coverage-analysis"]
    item = h.payloads[0]["items"][0]
    assert item["target"] == "VP-767" and item["candidates"]["tcs"]
    saved = _finding(h, outcome["run_id"], "TC_CHECK")
    assert saved["sections"]["source_finding"] == source["id"]
    assert {event["id"]: event["analysis_status"] for event in h.events()} == statuses   # 이벤트 상태는 그대로
    assert len(h.sent) == mails                                                  # 요청 실행은 메일을 보내지 않는다


def test_tc_draft_makes_verification_drafts_for_fixed_issue(tmp_path):
    h = world(tmp_path)
    auto = h.run(DAY2)
    source = _finding(h, auto["run_id"], FIXED_ISSUE)
    h.calls.clear()
    outcome = h.run(LATER, on_demand="tc-draft", finding_id=source["id"])
    assert h.skills_called() == ["qa-verification-tc-draft"]
    saved = _finding(h, outcome["run_id"], "TC_DRAFT")
    assert [draft["title"] for draft in saved["draft_tcs"]] == ["Retake 목록 갱신"]


def test_tc_draft_on_wrong_kind_fails_without_claude(tmp_path):
    h = world(tmp_path)
    auto = h.run(DAY2)
    source = _finding(h, auto["run_id"], SPEC_COVERAGE)
    h.calls.clear()
    outcome = h.run(LATER, on_demand="tc-draft", finding_id=source["id"])
    assert h.calls == [] and outcome["status"] == "FAILED"
    assert "수정 완료 이슈 분석에서만" in outcome["stages"]["on_demand"]["note"]


def test_on_demand_under_limit_calls_no_claude(tmp_path):
    h = world(tmp_path)
    auto = h.run(DAY2)
    source = _finding(h, auto["run_id"], SPEC_COVERAGE)
    limit = claude_limits.LimitInfo(claude_limits.KIND_WEEKLY, (LATER + timedelta(days=2)).isoformat(), "주간 한도")
    h.store.set_state(h.cfg.state_key(pipeline.STATE_CLAUDE_LIMIT), claude_limits.dump(limit))
    h.calls.clear()
    outcome = h.run(LATER, on_demand="tc-check", finding_id=source["id"])
    assert h.calls == [] and outcome["status"] == "PARTIAL"
    assert "다시 요청" in outcome["stages"]["preflight"]["note"]
    assert "다시 요청" in outcome["stages"][SPEC_COVERAGE]["note"]
    assert h.store.get_finding(source["id"])["verdict"] == "QA_CHECK_NEEDED"


def test_manual_check_button_runs_f_only(tmp_path):
    h = world(tmp_path)
    h.run(DAY2)
    h.calls.clear()
    outcome = h.run(LATER, on_demand="manual-check")
    assert set(h.skills_called()) == {"qa-manual-completeness"}
    assert outcome["stages"]["F"]["status"] == "ok"


def test_requested_tc_check_is_distinct_from_legacy_auto_result_but_dedupes_repeat(tmp_path):
    h = world(tmp_path)
    auto = h.run(DAY2)
    source = h.store.list_findings(run_id=auto["run_id"], analysis_type=SPEC_COVERAGE)[0]
    h.script["VP-767"] = {"verdict": "PARTIALLY_COVERED", "summary": "TC update needed",
                          "sections": {"checklist_coverage": {"tcs": []}}}
    legacy = {**source, "verdict": "PARTIALLY_COVERED", "sections": {}, "draft_tcs": []}
    h.store.add_finding(auto["run_id"], pipeline.SKILL_COVERAGE, "legacy-auto", legacy, h.cfg.slug)
    first = h.run(LATER, on_demand="tc-check", finding_id=source["id"])
    second = h.run(LATER + timedelta(minutes=1), on_demand="tc-check", finding_id=source["id"])
    findings = h.store.list_findings(run_id=second["run_id"], analysis_type="TC_CHECK")
    assert findings == []
    first_findings = h.store.list_findings(run_id=first["run_id"], analysis_type="TC_CHECK")
    assert len(first_findings) == 1
    assert first_findings[0]["sections"]["source_finding"] == source["id"]
