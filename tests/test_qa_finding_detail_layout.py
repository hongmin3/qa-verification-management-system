"""분석 상세 화면을 위에서 아래 한 흐름으로 읽는다 (TEST-QAINTEL-028).

Validates: REQ-QAINTEL-020
운영 결과 288(수정 완료)·290(사양 변경)·291(새 댓글)과 같은 모양을 합성 값으로 만든다.
실제 이름·번호·운영 DB 는 쓰지 않고 Claude·Polarion 도 부르지 않는다.
"""
import os
import re
from pathlib import Path

import pytest

from app.modules.qa_agent import dashboard as dash
from app.modules.qa_agent import issue_body
from tests.test_qa_intel_dashboard import world  # noqa: F401
from tests.test_qa_issue_body import snapshot_item, write

REPORTER = ("9001001", "김예시")
COMMENTER = ("9001002", "이예시")
ANALYZED_AT = "2026-10-14T04:35:00+00:00"          # 한국 시간 2026-10-14 13:35
FIRST_COMMENT = "2026-10-13T08:20:00Z"             # 한국 시간 2026-10-13 17:20
LATER_COMMENT = "2026-10-14T09:09:00Z"             # 한국 시간 2026-10-14 18:09


@pytest.fixture
def site(world):  # noqa: F811
    issue_body.clear_cache()
    store = world["store"]
    store.storage.qa_save_reporters("vxvue", [
        {"id": "VP-1", "reporter_id": REPORTER[0], "reporter_name": REPORTER[1]},
        {"id": "VP-2", "reporter_id": COMMENTER[0], "reporter_name": COMMENTER[1]}])
    write(world["cfg"].issue_snapshot_dir, "2026-10-15", [
        snapshot_item("VP-7287", comments=[
            {"id": "c1", "created": FIRST_COMMENT, "author": COMMENTER[0], "text": "원인은 Key 값이 남는 것입니다."},
            {"id": "c2", "created": LATER_COMMENT, "author": COMMENTER[0], "text": "분석 뒤에 달린 의견입니다."}]),
        snapshot_item("VP-7300", title="긴 본문 이슈", reproduction_step="", description="\n".join(f"{n}번째 줄" for n in range(1, 13)),
                      comments=[{"id": "d1", "created": FIRST_COMMENT, "author": "9009999", "text": "작성자를 모르는 댓글"}]),
        snapshot_item("VP-7400", title="새 이슈", rd_result="UNSET", comments=[])])
    return world


def put_event(world, subject, kind, after, created, status="done", analyses=("FIXED_ISSUE",), key=""):
    store = world["store"]
    event = {"entity_type": "issue", "entity_id": subject, "event_type": kind, "after": after, "changed_fields": ["comments"],
             "analysis_required": True, "analyses": list(analyses), "fingerprint": f"{subject}-{kind}-{key or created}"}
    event_id = store.storage.daily_qa_add_events("vxvue", world["changed"]["run_id"], [event])[0]
    with store.storage.connect() as db:
        db.execute("UPDATE qa_change_events SET created_at=?, analysis_status=? WHERE id=?", (created, status, event_id))
    return event_id


def put_finding(world, data, created=ANALYZED_AT):
    base = {"subject_title": "합성 제목", "confidence": "Review Needed", "evidence": [], "sections": {}}
    store = world["store"]
    finding_id = store.add_finding(world["changed"]["run_id"], "qa-fixed-issue-analysis", f"t-{data['subject']}-{data.get('analysis_type')}",
                                   {**base, **data}, product="vxvue")
    with store.storage.connect() as db:
        db.execute("UPDATE daily_qa_findings SET created_at=? WHERE id=?", (created, finding_id))
    return finding_id


def comment_after(comment_id, created, text="원인은 Key 값이 남는 것입니다."):
    return {"comments": [{"id": comment_id, "created": created, "text": text, "author": COMMENTER[0]}], "significant_ids": [comment_id]}


@pytest.fixture
def fixed_finding(site):
    first = put_event(site, "VP-7287", "ISSUE_COMMENT_ADDED", comment_after("c1", FIRST_COMMENT), "2026-10-13T08:21:00+00:00")
    rd = put_event(site, "VP-7287", "ISSUE_RD_RESULT_CHANGED", {"rd_result": "FIXED"}, "2026-10-13T08:21:00+00:00", key="rd")
    return put_finding(site, {
        "subject": "VP-7287", "analysis_type": "FIXED_ISSUE", "verdict": "SPEC_UNDEFINED", "event_ids": [first, rd],
        "summary": "수정은 원인과 맞지만 사양에 이 동작이 정의돼 있지 않습니다.",
        "evidence": [{"source_type": "srs", "location": "VP-10 본문", "summary": "전송 항목 정의"}],
        "sections": {"root_cause_review": {"summary": "원인 설명이 코드 변경과 맞다"}, "resolution_review": {"summary": "값이 있을 때만 채운다"},
                     "regression_risk": {"axes": [{"axis": "direct", "applicable": True, "reason": "같은 화면"},
                                                  {"axis": "state", "applicable": True, "reason": "선택 상태"},
                                                  {"axis": "data", "applicable": False, "reason": "해당 없음"}]},
                     "checked": {"confirmed": ["VP-10 본문"], "unconfirmed": ["송신 코드 미확인"]}},
        "detail": "분석 메모 원문입니다.", "action": "1. Sequence 전송 확인\n2. None 값 재현\n3. 회귀 확인\n4. 네 번째"})


def page_of(site, finding_id):
    response = site["client"].get(f"/qa-agent/findings/{finding_id}")
    assert response.status_code == 200
    return response.text


def between(html, start, end):
    return html[html.index(f'id="{start}"'):html.index(f'id="{end}"')]


def test_detail_shows_body_before_ai_judgement(site, fixed_finding):
    html = page_of(site, fixed_finding)
    assert html.index('id="issue-body"') < html.index('id="ai-judgement"') < html.index('id="qa-todos"')
    assert html.index("이슈 본문") < html.index("AI 판단")
    body = between(html, "issue-body", "ai-judgement")
    assert "[Title] Tag 값을 None 으로 변경" in body                # 설명이 비면 재현 절차를 보인다
    assert "Key 값이 남음" in body and "값이 있을 때만 채움" in body and "1.3.0" in body
    assert "원문 스냅샷 없음" not in html


def test_header_second_line_has_author_dates_status_and_original_link(site, fixed_finding):
    html = page_of(site, fixed_finding)
    head = between(html, "issue-head", "issue-body")
    assert "[VP-7287]" in head and "김예시" in head and "원문 열기" in head
    assert "등록 2026-09-21 16:37" in head and "최근 업데이트 2026-10-14 18:09" in head
    assert "reviewed" in head and "연구소 FIXED" in head


def test_detail_marks_changes_after_analysis(site, fixed_finding):
    put_event(site, "VP-7287", "ISSUE_COMMENT_ADDED", comment_after("c2", LATER_COMMENT), "2026-10-14T09:10:00+00:00",
              status="pending", key="later")
    html = page_of(site, fixed_finding)
    assert "분석(2026-10-14 13:35) 이후 변경 1건 · 재분석 대기" in html
    comments = between(html, "comments", "evidence-panel")
    assert comments.index("2026-10-13 17:20") < comments.index("2026-10-14 18:09")
    assert "분석에 사용" in comments and comments.count("분석 이후") == 1
    assert comments.count("이예시") == 2 and "9001002" not in comments


def test_up_to_date_result_has_no_freshness_banner(site, fixed_finding):
    with site["store"].storage.connect() as db:
        db.execute("UPDATE daily_qa_findings SET created_at='2026-10-15T00:00:00+00:00' WHERE id=?", (fixed_finding,))
    assert "재분석 대기" not in page_of(site, fixed_finding)


def test_trigger_events_are_deduplicated(site):
    ids = [put_event(site, "VP-7287", "ISSUE_COMMENT_ADDED", comment_after("c1", FIRST_COMMENT), "2026-10-13T08:21:00+00:00", key=str(n))
           for n in range(2)]
    finding_id = put_finding(site, {"subject": "VP-7287", "analysis_type": "FIXED_ISSUE", "verdict": "SPEC_UNDEFINED",
                                    "summary": "요약", "event_ids": ids})
    html = page_of(site, finding_id)
    assert "댓글 추가 · 댓글 추가" not in html
    line = re.search(r'id="trigger">(.*?)</p>', html, re.S).group(1)
    assert "왜 분석했나" in line and "2026-10-13 17:20 새 댓글" in line and line.count("새 댓글") == 1


def test_new_issue_shows_validity_question(site):
    finding_id = put_finding(site, {"subject": "VP-7400", "analysis_type": "NEW_ISSUE", "verdict": "SPEC_VIOLATION", "summary": "화면이 사양과 다릅니다.",
                                    "sections": {"duplicate": {"verdict": "POSSIBLE_DUPLICATE", "candidates": [{"issue_id": "VP-1"}]}}})
    judgement = between(page_of(site, finding_id), "ai-judgement", "qa-todos")
    assert "사양으로 보면 이슈가 맞는가" in judgement and "이슈로 보임" in judgement and "중복 의심" in judgement


@pytest.mark.parametrize("verdict,shown", [("CONSISTENT_WITH_SPEC", "사양상 정상, 이슈 아닐 수 있음"),
                                           ("SPEC_UNDEFINED", "사양 미정의, 기획 확인 필요"),
                                           ("SOMETHING_NEW", "SOMETHING_NEW")])
def test_new_issue_verdict_names(site, verdict, shown):
    finding_id = put_finding(site, {"subject": "VP-7400", "analysis_type": "NEW_ISSUE", "verdict": verdict, "summary": "요약"})
    judgement = between(page_of(site, finding_id), "ai-judgement", "qa-todos")
    assert shown in judgement
    assert ("qa-review-needed" in judgement) == (verdict == "SOMETHING_NEW")      # 표에 없는 값은 `검토 필요` 표시를 단다
    assert "중복 의심" not in judgement


def test_historical_duplicate_flag_marks_duplicate_suspicion(site):
    finding_id = put_finding(site, {"subject": "VP-7400", "analysis_type": "NEW_ISSUE", "verdict": "SPEC_UNDEFINED", "summary": "요약",
                                    "sections": {"historical": {"flags": ["HISTORICAL_SPEC_DUPLICATE"]}}})
    assert "중복 의심" in between(page_of(site, finding_id), "ai-judgement", "qa-todos")


def test_fixed_issue_question_counts_applicable_regression_axes(site, fixed_finding):
    html = page_of(site, fixed_finding)
    judgement = between(html, "ai-judgement", "qa-todos")
    assert "원인 설명과 수정이 사양에 맞는가" in judgement
    assert "원인 설명이 코드 변경과 맞다" in judgement and "값이 있을 때만 채운다" in judgement
    assert "회귀 위험 축 해당 2개" in judgement and "사양 미정의" in judgement and "VP-10 본문" in judgement
    todos = between(html, "qa-todos", "comments")
    assert "Sequence 전송 확인" in todos and "회귀 확인" in todos and "네 번째" not in todos
    assert "확인하지 못한 자료" in todos and "송신 코드 미확인" in todos


def test_comment_question_and_no_empty_interpretation_text_for_other_kinds(site, fixed_finding):
    fixed = page_of(site, fixed_finding)
    assert "해석 없음" not in fixed and "해석이 저장되지 않았습니다" not in fixed
    event = put_event(site, "VP-7287", "ISSUE_COMMENT_ADDED", comment_after("c1", FIRST_COMMENT), "2026-10-13T08:21:00+00:00",
                      analyses=("COMMENT",), key="comment-kind")
    finding_id = put_finding(site, {"subject": "VP-7287", "analysis_type": "COMMENT", "verdict": "ROOT_CAUSE_INFORMATION", "summary": "댓글 요약",
                                    "event_ids": [event], "sections": {"comments": [
                                        {"comment_id": "c1", "summary": "원인을 알려 줌", "classification": "ROOT_CAUSE_INFORMATION", "impact": "TC 확인"}]}})
    html = page_of(site, finding_id)
    judgement = between(html, "ai-judgement", "qa-todos")
    assert "새 댓글이 QA 판단을 바꾸는가" in judgement and "원인 정보" in judgement and "TC 확인" in judgement
    comments = between(html, "comments", "evidence-panel")
    assert "원인을 알려 줌" in comments and "분석에 사용" in comments


def test_spec_change_has_spec_section_and_no_comments(site):
    finding_id = put_finding(site, {"subject": "VP-10", "subject_title": "목록", "analysis_type": "SPEC_COVERAGE", "verdict": "PARTIALLY_COVERED",
                                    "summary": "정렬 기준이 바뀌었습니다.", "sections": {"issue_summary": "목록을 최신순으로 표시한다"}})
    html = page_of(site, finding_id)
    assert "바뀐 사양을 TC가 덮는가" in html
    section = between(html, "issue-body", "ai-judgement")
    assert "사양 내용" in section and "목록을 최신순으로 표시한다" in section
    assert "원문 스냅샷 없음" not in html and 'id="comments"' not in html


def test_missing_snapshot_and_legacy_kind_render(site):
    finding_id = put_finding(site, {"subject": "VP-0001", "verdict": "TC 없음", "summary": "옛 형식 요약", "detail": "옛 상세"})
    html = page_of(site, finding_id)
    assert "원문 스냅샷 없음" in html and "이전 형식 결과" in html
    judgement = between(html, "ai-judgement", "qa-todos")
    assert "옛 형식 요약" in judgement and "사양으로 보면 이슈가 맞는가" not in judgement
    assert "재분석 대기" not in html


def test_placeholder_snapshot_comments_are_filled_from_stored_originals(site):
    """수집기가 번호만 남긴 빈 댓글(created·text 빈 값)이 이벤트 원문을 덮어쓰지 않는다."""
    placeholder = {"created": "", "author": "", "text": ""}
    write(site["cfg"].issue_snapshot_dir, "2026-10-16", [snapshot_item("VP-7500", comments=[
        {"id": "pl-no-original", **placeholder},
        {"id": "pl-from-event", **placeholder},
        {"id": "dated", "created": "2026-10-13T08:00:00Z", "author": COMMENTER[0], "text": "시각이 있는 댓글"}])])
    event = put_event(site, "VP-7500", "ISSUE_COMMENT_ADDED", comment_after("pl-from-event", FIRST_COMMENT, "이벤트에 저장된 원문"),
                      "2026-10-13T08:21:00+00:00", analyses=("COMMENT",))
    finding_id = put_finding(site, {"subject": "VP-7500", "analysis_type": "COMMENT", "verdict": "SPEC_CLAIM", "summary": "요약",
                                    "event_ids": [event], "sections": {"comments": [
                                        {"comment_id": "pl-from-event", "summary": "원문에서 채운 해석", "classification": "SPEC_CLAIM", "impact": "영향"},
                                        {"comment_id": "only-interpreted", "summary": "해석만 남은 댓글", "classification": "SPEC_CLAIM"}]}})
    comments = between(page_of(site, finding_id), "comments", "evidence-panel")
    assert "댓글 4건" in comments
    # 이벤트 원문으로 채움: 시각·본문·작성자 이름
    assert "2026-10-13 17:20" in comments and "이벤트에 저장된 원문" in comments and "이예시" in comments
    # 원문이 없으면 본문 미수집, 해석만 있는 댓글도 줄이 남는다
    assert comments.count("본문 미수집") == 2 and "해석만 남은 댓글" in comments
    # 순서: 시각을 아는 댓글(시간순) → 시각을 모르는 댓글(스냅샷 순서 → 해석만 있는 순서)
    order = [comments.index(text) for text in ("시각이 있는 댓글", "이벤트에 저장된 원문", "해석만 남은 댓글")]
    assert order[0] < order[1] < order[2]
    rows = dash.comment_rows({"created_at": ANALYZED_AT}, issue_body.load("vxvue", "VP-7500", snapshot_dir=site["cfg"].issue_snapshot_dir),
                             {}, set(), {"only-interpreted": {"summary": "x"}})
    assert [row["id"] for row in rows] == ["dated", "pl-no-original", "pl-from-event", "only-interpreted"]
    assert [row["body_missing"] for row in rows] == [False, True, True, True]


def test_empty_verdict_shows_not_saved_for_every_kind(site):
    for kind in ("NEW_ISSUE", "FIXED_ISSUE", "COMMENT", "SPEC_DECISION"):
        finding_id = put_finding(site, {"subject": "VP-7400", "analysis_type": kind, "verdict": "", "summary": "요약"})
        judgement = between(page_of(site, finding_id), "ai-judgement", "qa-todos")
        assert "판정 저장 안 됨" in judgement, kind
        assert "판정 </b>" not in judgement and "qa-review-needed" not in judgement, kind
    assert dash.verdict_view({"analysis_type": "SPEC_COVERAGE", "verdict": "", "sections": {}}, "COVERAGE")["verdict"] == "판정 저장 안 됨"


def test_unknown_comment_author_shows_unconfirmed_name_with_id(site):
    finding_id = put_finding(site, {"subject": "VP-7300", "analysis_type": "FIXED_ISSUE", "verdict": "SPEC_UNDEFINED", "summary": "요약"})
    assert "이름 미확인 (9009999)" in page_of(site, finding_id)


def test_long_issue_text_folds_after_eight_lines_without_script(site):
    finding_id = put_finding(site, {"subject": "VP-7300", "analysis_type": "FIXED_ISSUE", "verdict": "SPEC_UNDEFINED", "summary": "요약"})
    body = between(page_of(site, finding_id), "issue-body", "ai-judgement")
    assert "8번째 줄" in body and "12번째 줄" in body                     # 접힌 부분도 HTML 에 있다
    folded = re.search(r"<details[^>]*>\s*<summary>펼치기[^<]*</summary>(.*?)</details>", body, re.S).group(1)
    assert "9번째 줄" in folded and "8번째 줄" not in folded


def test_flow_has_no_tabs_and_keeps_processing_form(site, fixed_finding):
    html = page_of(site, fixed_finding)
    assert "qa-reading-tabs" not in html and "qa-reading.js" not in html and "data-qa-panel" not in html
    assert 'id="processing"' in html and 'name="actor"' in html
    names = ("issue-head", "issue-body", "ai-judgement", "qa-todos", "comments", "evidence-panel", "analysis-record", "processing")
    positions = [html.index(f'id="{name}"') for name in names]
    assert positions == sorted(positions)
    record = between(html, "analysis-record", "processing")
    assert "분석 메모 원문입니다." in record and "이 분석을 만든 변경 이벤트" in record
    assert "<details" in html[html.index('id="analysis-record"') - 40:html.index('id="analysis-record"') + 10]


def test_detail_does_not_start_processes(site, fixed_finding, monkeypatch):
    calls = []
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: calls.append(a))
    page_of(site, fixed_finding)
    assert calls == []


# -- freshness (Contract: FreshnessState) ------------------------------------------------------


def make_issue(updated):
    return issue_body.IssueBody(id="VP-1", title="", status="", rd_result="", created="", updated=updated, reporter_id="", reporter_name="",
                                description="", reproduction_step="", occurrence_cause="", action_details="", occurred_versions=[],
                                target_versions=[], comments=[])


def event(created, status="pending", entity="VP-1", ident=1):
    return {"id": ident, "entity_id": entity, "created_at": created, "analysis_status": status}


FINDING = {"id": 5, "subject": "VP-1", "created_at": "2026-10-14T04:35:00+00:00", "event_ids": [1]}


@pytest.mark.parametrize("issue_updated,events,state,count", [
    ("2026-10-14T04:00:00Z", [event("2026-10-14T04:30:00+00:00", "done")], "current", 0),
    ("2026-10-14T04:00:00Z", [event("2026-10-14T09:10:00+00:00", ident=2)], "changed_after", 1),
    ("2026-10-14T04:00:00Z", [event("2026-10-14T09:10:00+00:00", "pending", entity="VP-2", ident=2)], "current", 0),
    ("2026-10-14T04:00:00Z", [event("2026-10-14T09:10:00+00:00", "done", ident=2)], "current", 0),
    ("2026-10-14T09:09:00Z", [], "changed_after", 0),
])
def test_freshness_states(issue_updated, events, state, count):
    result = dash.freshness(FINDING, make_issue(issue_updated), events)
    assert (result["state"], result["after_events"]) == (state, count)
    if state == "changed_after" and not count:
        assert result["latest_update"] == "2026-10-14T09:09:00Z"


def test_freshness_unknown_without_issue_and_events():
    assert dash.freshness(FINDING, None, [])["state"] == "unknown"
    assert dash.freshness({**FINDING, "created_at": ""}, make_issue("2026-10-14T09:09:00Z"), [])["state"] == "unknown"
    assert dash.freshness(FINDING, None, [event("2026-10-14T09:10:00+00:00", ident=2)])["state"] == "changed_after"


def test_validity_label_uses_codes_of_the_skill_output_format():
    skill = (Path(__file__).resolve().parents[1] / "app/modules/daily_qa/skills/qa-new-issue-analysis/SKILL.md").read_text(encoding="utf-8")
    for code in ("SPEC_VIOLATION", "CONSISTENT_WITH_SPEC", "SPEC_UNDEFINED", "SPEC_AMBIGUOUS", "INSUFFICIENT_EVIDENCE"):
        assert code in skill and code in dash.VALIDITY_LABEL


# -- 브라우저 (1366px · 1920px, 자바스크립트 꺼짐 포함) ------------------------------------------


@pytest.fixture
def all_findings(site, fixed_finding):
    """운영 결과 288·290·291과 같은 모양, 새 이슈 1건, 스냅샷에 없는 이전 형식 1건."""
    put_event(site, "VP-7287", "ISSUE_COMMENT_ADDED", comment_after("c2", LATER_COMMENT), "2026-10-14T09:10:00+00:00",
              status="pending", key="later")
    comment_event = put_event(site, "VP-7300", "ISSUE_COMMENT_ADDED", comment_after("d1", FIRST_COMMENT, "사양 쪽 설명입니다."),
                              "2026-10-13T08:21:00+00:00", analyses=("COMMENT",))
    return {
        "fixed(288)": fixed_finding,
        "spec(290)": put_finding(site, {"subject": "VP-10", "subject_title": "목록", "analysis_type": "SPEC_COVERAGE", "verdict": "PARTIALLY_COVERED",
                                        "summary": "정렬 기준이 바뀌었습니다.", "sections": {"issue_summary": "목록을 최신순으로 표시한다",
                                                                                        "change": {"summary": "정렬 바뀜", "changed_requirements": ["최신순"]}}}),
        "comment(291)": put_finding(site, {"subject": "VP-7300", "analysis_type": "COMMENT", "verdict": "SPEC_CLAIM", "summary": "댓글이 사양 변경을 설명합니다.",
                                           "event_ids": [comment_event], "sections": {"comments": [
                                               {"comment_id": "d1", "summary": "사양 변경 설명", "classification": "SPEC_CLAIM", "impact": "기대 결과 후보"}]}}),
        "new": put_finding(site, {"subject": "VP-7400", "analysis_type": "NEW_ISSUE", "verdict": "SPEC_VIOLATION", "summary": "화면이 사양과 다릅니다.",
                                  "sections": {"duplicate": {"verdict": "POSSIBLE_DUPLICATE", "candidates": [{"issue_id": "VP-1", "reason": "같은 증상"}]}}}),
        "legacy": put_finding(site, {"subject": "VP-0001", "verdict": "TC 없음", "summary": "옛 형식 요약", "detail": "옛 상세"}),
    }


def test_flow_in_pc_widths_without_overflow_errors_or_script(site, all_findings):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import urlparse
    playwright = pytest.importorskip("playwright.sync_api")
    static_root = Path(__file__).resolve().parents[1] / "app" / "web"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            path = urlparse(self.path).path
            if path.startswith("/static/"):
                file = static_root / path.lstrip("/")
                body = file.read_bytes() if file.is_file() else b""
                kind = "text/javascript" if file.suffix == ".js" else "text/css"
            elif path == "/qa-agent/status":
                body, kind = b'{"running":false,"current_run_id":null}', "application/json"
            else:
                response = site["client"].get(path)
                body, kind = response.content, "text/html; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}/qa-agent/findings/"
    shots = Path(os.environ.get("QA_SCREENSHOT_DIR", ""))   # 비워 두면 화면 사진은 남기지 않는다
    try:
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                for width in (1366, 1920):
                    for script in (True, False):
                        context = browser.new_context(viewport={"width": width, "height": 1080}, java_script_enabled=script)
                        for name, finding_id in all_findings.items():
                            page = context.new_page()
                            errors = []
                            page.on("pageerror", lambda error: errors.append(str(error)))
                            page.goto(base + str(finding_id), wait_until="domcontentloaded", timeout=120000)
                            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), (name, width, script)
                            assert page.locator("#issue-body").is_visible() and page.locator("#ai-judgement").is_visible(), name
                            assert "AI 판단" in page.locator("#ai-judgement").inner_text()
                            assert not errors, (name, errors)
                            if script and os.environ.get("QA_SCREENSHOT_DIR"):
                                page.screenshot(path=str(shots / f"{name.split('(')[0]}-{width}.png"), full_page=True)
                            page.close()
                        context.close()
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
