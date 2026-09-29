"""Validates: REQ-DAILY-009 (TEST-DAILY-005)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.daily_qa import router as router_module
from app.modules.daily_qa.store import DailyQaStore
from tests.daily_qa_fixtures import make_settings


@pytest.fixture
def client(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    store = DailyQaStore(tmp_path / "app.db")
    cfg = make_settings(root, tmp_path / "ws")
    monkeypatch.setattr(router_module, "get_store", lambda: store)
    monkeypatch.setattr(router_module, "load", lambda: cfg)
    store.create_run("20260928-090000", dry_run=False)
    evidence = [{"source_type": "srs", "location": "VP-10 / 본문", "summary": "", "validity": "Current"}]
    store.add_finding("20260928-090000", "vxvue-spec-change-impact", "B-001",
                      {"subject": "VP-10", "verdict": "수정 필수", "summary": "Expected 불일치", "evidence": evidence})
    store.add_finding("20260928-090000", "vxvue-trace-gap", "E-weekly",
                      {"subject": "VP-3", "verdict": "TC 없음", "summary": "TC 없음",
                       "evidence": [{"source_type": "srs", "location": "VP-3 / oldId", "summary": "", "validity": "Current"}]})
    store.add_question("20260928-090000", "vxvue-spec-change-impact", "VP-10", "Admin 만 보이나?", "권한")
    store.finish_run("20260928-090000", "SUCCESS", {"B": {"status": "ok", "note": ""}}, {"findings": 2, "questions": 1})
    out = cfg.output_dir / "20260928-090000"
    out.mkdir(parents=True)
    (out / "impact_checklist_draft.xlsx").write_bytes(b"x")
    (out / "secret.txt").write_text("no", encoding="utf-8")
    app = FastAPI()
    app.include_router(router_module.router, prefix="/daily-qa")
    return TestClient(app), store


def test_index_and_run_pages_show_counts_and_files(client):
    http, _ = client
    index = http.get("/daily-qa")
    assert index.status_code == 200 and "20260928-090000" in index.text and "검토 대기" in index.text
    run = http.get("/daily-qa/runs/20260928-090000")
    assert run.status_code == 200 and "impact_checklist_draft.xlsx" in run.text and "secret.txt" not in run.text


def test_file_download_is_limited_to_run_outputs(client):
    http, _ = client
    assert http.get("/daily-qa/runs/20260928-090000/files/impact_checklist_draft.xlsx").status_code == 200
    assert http.get("/daily-qa/runs/20260928-090000/files/secret.txt").status_code == 404
    assert http.get("/daily-qa/runs/20260928-090000/files/..%2F..%2Fapp.db").status_code == 404


def test_queue_filters_by_skill_and_status(client):
    http, _ = client
    page = http.get("/daily-qa/queue", params={"skill": "vxvue-trace-gap", "status": "PENDING"})
    assert "VP-3" in page.text and "VP-10" not in page.text


def test_decision_is_stored_with_reviewer(client):
    http, store = client
    finding_id = store.list_findings(skill="vxvue-spec-change-impact")[0]["id"]
    response = http.post(f"/daily-qa/findings/{finding_id}/decision",
                         data={"decision": "APPROVED", "reviewer": "김QA", "note": "확인함", "next_url": "/daily-qa/queue"},
                         follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/daily-qa/queue"
    saved = store.get_finding(finding_id)
    assert (saved["review_status"], saved["reviewer"], saved["review_note"]) == ("APPROVED", "김QA", "확인함")
    assert saved["reviewed_at"]


def test_need_evidence_requires_note_and_unknown_decision_is_refused(client):
    http, store = client
    finding_id = store.list_findings()[0]["id"]
    assert http.post(f"/daily-qa/findings/{finding_id}/decision", data={"decision": "NEED_EVIDENCE", "reviewer": "김QA"}).status_code == 400
    assert http.post(f"/daily-qa/findings/{finding_id}/decision", data={"decision": "CLOSE_ISSUE", "reviewer": "김QA"}).status_code == 400
    assert store.get_finding(finding_id)["review_status"] == "PENDING"


def test_answer_is_saved_and_feeds_next_run(client):
    http, store = client
    question_id = store.list_questions()[0]["id"]
    response = http.post(f"/daily-qa/questions/{question_id}/answer", data={"answer_text": "Admin 전용", "reviewer": "김QA"}, follow_redirects=False)
    assert response.status_code == 303
    answers = store.answered_questions("vxvue-spec-change-impact")
    assert answers and answers[0]["answer"] == "Admin 전용"


def test_guide_page_renders(client):
    http, _ = client
    assert http.get("/daily-qa/guide").status_code == 200


# 사람이 보는 이름에 알파벳 약칭(B·C·E·F)을 붙이지 않는다. 내부 단계 키는 그대로 둔다.
VISIBLE_NAMES = ("사양 변경 영향 검토", "이슈 수정확인 초안", "사양–TC 연결 점검", "매뉴얼 누락 후보 점검")
OLD_LETTER_LABELS = ("B 사양", "C 이슈", "E 추적", "E 사양", "F 매뉴얼")


def test_screens_and_mail_use_feature_names_not_letter_codes(client):
    """Validates: REQ-DAILY-008, REQ-DAILY-009 (TEST-DAILY-005)."""
    from app.modules.daily_qa.report import STAGE_LABELS
    from app.modules.daily_qa.schema import SKILL_LABELS

    for key, name in zip(("B", "C", "E", "F"), VISIBLE_NAMES):
        assert STAGE_LABELS[key] == name
    assert sorted(SKILL_LABELS.values()) == sorted(VISIBLE_NAMES)
    http, _ = client
    pages = {path: http.get(path).text for path in ("/daily-qa/guide", "/daily-qa/queue", "/daily-qa/runs/20260928-090000")}
    for path, page in pages.items():
        for old in OLD_LETTER_LABELS:
            assert old not in page, (path, old)
    for name in VISIBLE_NAMES:
        assert name in pages["/daily-qa/guide"], name
    assert "사양 변경 영향 검토" in pages["/daily-qa/queue"]
