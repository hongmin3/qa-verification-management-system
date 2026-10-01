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


# Validates: REQ-SYNC-001, REQ-KNOW-020
def test_alm_specification_shows_status_without_upload():
    text = _page("/knowledge/products/vxvue")
    assert "매주 월요일 07:30" not in text
    assert "ALM 에서 자동으로 수집합니다" in text
    section = text[text.index('id="kind-specification"'):text.index('id="kind-testcase"')]
    assert "새 문서 등록" not in section and 'type="file"' not in section


# Validates: REQ-SYNC-002, REQ-KNOW-017
def test_guide_shows_upload_command_and_schedule():
    text = _page("/knowledge/guide")
    assert "12000" not in text
    assert "--report-to http://서버" not in text
    assert "--upload-to http://&lt;서버 주소&gt;:24357" in text
    assert "평일 10:00" in text
    assert "매주 자동 수집" not in text


# Validates: REQ-KNOW-001, REQ-KNOW-003
def test_dashboard_has_no_upload_forms_and_manual_source_detail_has():
    """현황판에는 업로드·제품 추가 양식이 없다. 출처가 manual 인 Bellalun 사양서 구역에만 등록 버튼이 있다."""
    page = _page("/knowledge")
    assert 'type="file"' not in page and 'action="/knowledge/products"' not in page
    detail = _page("/knowledge/products/bellalun-viewer")
    section = detail[detail.index('id="kind-specification"'):detail.index('id="kind-testcase"')]
    assert "새 문서 등록" in section and 'accept=".pdf,.docx"' in section
    manual = detail[detail.index('id="kind-manual"'):detail.index('id="kind-qa_rules"')]
    assert 'type="file"' not in manual                       # 매뉴얼은 지식 폴더 자료 (SPEC 13.5)
