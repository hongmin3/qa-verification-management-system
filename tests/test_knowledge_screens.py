"""Knowledge 화면과 사용법 화면의 안내 문구가 운영 방식과 맞는지 본다.

옛 포트·옛 명령을 보고 수동 실행하면 연결에 실패하거나 서버 대신 로컬 DB 에 등록한다
(docs/SPEC_CODE_MISMATCH.md 5절 7번).
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def _page(path: str) -> str:
    response = TestClient(app).get(path)
    assert response.status_code == 200
    return response.text


# Validates: REQ-SYNC-001
def test_knowledge_page_states_weekday_spec_sync_time():
    text = _page("/knowledge")
    assert "매주 월요일 07:30" not in text
    assert "평일 09:40" in text


# Validates: REQ-SYNC-002, REQ-KNOW-017
def test_guide_shows_upload_command_and_schedule():
    text = _page("/knowledge/guide")
    assert "12000" not in text
    assert "--report-to http://서버" not in text
    assert "--upload-to http://&lt;서버 주소&gt;:24357" in text
    assert "평일 10:00" in text
    assert "매주 자동 수집" not in text


# Validates: REQ-KNOW-003
def test_manual_form_is_named_specification_only():
    """수동 양식으로 올린 파일은 종류가 사양서가 된다. 매뉴얼은 지식 폴더로만 받는다 (SPEC 13.5 추천 ②)."""
    page = _page("/knowledge")
    guide = _page("/knowledge/guide")
    assert "사양서 / Manual 등록" not in page
    assert "<h2>사양서 등록</h2>" in page
    assert "사양서/Manual 등록" not in guide
    assert "사양서 등록" in guide
