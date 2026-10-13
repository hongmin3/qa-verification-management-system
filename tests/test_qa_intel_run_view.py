"""실행 결과 요약과 QA 할 일. Validates: REQ-QAINTEL-020."""
import pytest

from app.modules.qa_agent import dashboard, run_view


def cards(count, verdict="TC 없음", analysis_type=""):
    return [dashboard.card({"id": i + 1, "subject": f"VP-{i}", "verdict": verdict,
                            "analysis_type": analysis_type, "summary": "확인할 결과"}) for i in range(count)]


def test_collection_failure_is_first_even_with_214_rule_findings():
    run = {"status": "PARTIAL", "stages": {
        "collect_srs": {"status": "failed", "note": "Polarion 요청이 3회 실패했습니다 (ConnectError)"},
        "collect_issues": {"status": "failed", "note": "ConnectError"},
        "events": {"status": "ok", "counts": {"total": 0, "analysis_required": 0}},
        "E": {"status": "ok", "counts": {"found": 214}}}, "summary": {"claude_calls": 0}}
    view = run_view.build(run, [], cards(214), [])
    assert view["headline"].startswith("Polarion")
    assert "확인하지 못했습니다" in view["headline"]
    assert "0건을 찾" not in view["headline"] and "DNS" not in view["headline"]
    assert view["tone"] == "bad"
    assert any("관리자" in action["todo"] for action in view["actions"])
    assert [tile["value"] for tile in view["key_tiles"]] == ["-", "0", "214"]
    assert len(view["priority_cards"]) == 10 and view["remaining"] == 204


@pytest.mark.parametrize(("stage", "detail", "category", "word"), [
    ("collect_srs", {"status": "failed", "note": "Name or service not known"}, "dns", "DNS"),
    ("collect_issues", {"status": "failed", "note": "HTTP 401 Unauthorized"}, "auth", "토큰"),
    ("on_demand", {"status": "failed", "note": "Claude login required"}, "auth", "로그인"),
    ("preflight", {"status": "rules", "note": "지원하지 않는 QA 규칙 판"}, "rules", "규칙"),
    ("on_demand", {"status": "limit"}, "limit", "다시 요청"),
    ("email", {"status": "failed", "note": "SMTP 535"}, "mail", "메일"),
    ("tc_index", {"status": "failed", "note": "file error"}, "stage", "관리자"),
])
def test_failure_actions_follow_evidence(stage, detail, category, word):
    view = run_view.build({"stages": {stage: detail}, "summary": {}}, [], [], [])
    assert view["actions"][0]["kind"] == category
    assert word in view["actions"][0]["todo"]


def test_success_has_no_actions_and_ai_count_is_completed_tasks():
    view = run_view.build({"status": "SUCCESS", "stages": {
        "events": {"status": "ok", "counts": {"total": 8}},
        "NEW_ISSUE": {"status": "ok", "counts": {"tasks": 5, "done": 3}},
        "E": {"status": "ok", "counts": {"found": 11}}}}, [], cards(11), [])
    assert view["actions"] == []
    assert [tile["value"] for tile in view["key_tiles"]] == ["8", "3", "11"]


def test_priority_uses_dashboard_conclusions_and_counts_other_results():
    all_cards = cards(11) + cards(1, "SPEC_VIOLATION") + cards(2, "INSUFFICIENT_EVIDENCE")
    all_cards += cards(3, "CONSISTENT_WITH_SPEC") + cards(4, "ROOT_CAUSE_INFORMATION")
    view = run_view.build({"stages": {}}, [], all_cards, [])
    assert view["priority_cards"][0]["level"] == "사양과 다름"
    assert view["attention_count"] == 14 and view["remaining"] == 4
    assert view["other_counts"] == [("참고", 4), ("문제 없음", 3)]


@pytest.mark.parametrize(("kind", "category", "word"), [("AUTH", "auth", "로그인"), ("CLI_MISSING", "cli", "설치")])
def test_claude_auth_and_missing_cli_are_not_described_as_usage_limit(kind, category, word):
    view = run_view.build({"stages": {"on_demand": {"status": "limit"}},
                           "summary": {"claude_limit": {"kind": kind}}}, [], [], [])
    assert view["actions"][0]["kind"] == category and word in view["actions"][0]["todo"]
    assert "한도" not in view["headline"]


def test_failed_run_without_stage_details_remains_failure():
    view = run_view.build({"status": "FAILED"}, [], [], [])
    assert view["tone"] == "bad" and view["actions"]


def test_failed_tc_file_named_401_is_not_an_authentication_failure():
    view = run_view.build({"stages": {"tc_index": {"status": "failed", "note": "TC401.xlsx 파일 읽기 실패"}}}, [], [], [])
    assert view["actions"][0]["kind"] == "stage"


def test_cancelled_run_does_not_describe_saved_results_as_completed_analysis():
    view = run_view.build({"status": "CANCELLED", "stages": {
        "collect_srs": {"status": "ok", "counts": {"total": 437}},
        "NEW_ISSUE": {"status": "ok", "counts": {"tasks": 18, "done": 4}}}}, [], cards(1), [])
    assert view["headline"].startswith("사용자 요청으로 이번 점검을 중단했습니다.")
    assert "저장" in view["headline"] and view["tone"] == "warn"
    assert view["actions"] == []


def test_cancelled_call_without_final_usage_is_visible_beside_recorded_tokens():
    view = run_view.build({"status": "CANCELLED", "summary": {
        "claude_calls": 2, "token_usage": {"total_tokens": 0, "usage_unknown_calls": 1}}}, [], [], [])
    tile = next(item for item in view["tiles"] if item["label"] == "Claude 호출")
    assert "기록된 토큰 0" in tile["hint"]
    assert "사용량을 확인하지 못한 중단 호출 1회" in tile["hint"]


def test_cached_task_is_separate_from_new_ai_work_and_links_origin():
    """Validates: REQ-QAINTEL-037. 재사용을 신규 분석으로 세지 않는다."""
    view = run_view.build({"status": "SUCCESS", "stages": {
        "NEW_ISSUE": {"status": "ok", "counts": {"tasks": 2, "done": 2}}},
        "summary": {"claude_calls": 1, "result_cache": {"hits": 1, "reused_findings": 5,
            "sources": [{"run_id": "20261013-origin-vxvue", "task_id": "NEW-001", "finding_ids": [42]}]}}},
        [], cards(6), [])
    assert view["key_tiles"][1]["value"] == "1"
    assert "재사용 1" in view["key_tiles"][1]["hint"]
    assert view["cache"]["reused_findings"] == 5
    assert view["cache"]["sources"][0]["url"] == "/qa-agent/runs/20261013-origin-vxvue"
    assert next(tile for tile in view["tiles"] if tile["label"] == "Claude 호출")["value"] == "1"


def test_cache_only_run_has_zero_new_ai_work():
    view = run_view.build({"status": "SUCCESS", "stages": {
        "NEW_ISSUE": {"status": "ok", "counts": {"tasks": 1, "done": 1}}},
        "summary": {"claude_calls": 0, "result_cache": {"hits": 1, "reused_findings": 1, "sources": []}}},
        [], cards(1), [])
    assert view["key_tiles"][1]["value"] == "0"
    assert next(tile for tile in view["tiles"] if tile["label"] == "Claude 호출")["value"] == "0"


def test_weekly_limit_actions_explain_next_week_or_manual_resume():
    view = run_view.build({"status": "PARTIAL", "stages": {"NEW_ISSUE": {"status": "limit"}},
        "summary": {"claude_limit": {"kind": "SESSION"}, "automation_policy": {"mode": "weekly"}}}, [], [], [])
    assert "다음 주 첫 업무일" in view["actions"][0]["todo"]
    assert "변경사항 점검하기" in view["actions"][0]["todo"]
    assert "한도가 풀리면" not in view["headline"]
