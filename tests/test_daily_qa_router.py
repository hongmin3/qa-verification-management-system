"""옛 `/daily-qa` 주소가 새 `/qa-agent` 화면으로 이어지는지 (REQ-QAINTEL-019).

Validates: REQ-QAINTEL-019
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.daily_qa import router as router_module


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router_module.router, prefix="/daily-qa")
    return TestClient(app, follow_redirects=False)


@pytest.mark.parametrize(("old", "new"), [
    ("/daily-qa", "/qa-agent"),
    ("/daily-qa/queue", "/qa-agent"),
    ("/daily-qa/questions", "/qa-agent"),
    ("/daily-qa/guide", "/qa-agent/guide"),
    ("/daily-qa/runs/20260928-090000", "/qa-agent/runs/20260928-090000"),
    ("/daily-qa/runs/20260928-090000/files/impact_checklist_draft.xlsx",
     "/qa-agent/runs/20260928-090000/files/impact_checklist_draft.xlsx"),
    ("/daily-qa/findings/7", "/qa-agent/findings/7"),
])
def test_old_links_in_sent_emails_keep_working(client, old, new):
    response = client.get(old)
    assert response.status_code == 307 and response.headers["location"] == new


@pytest.mark.parametrize("path", ["/daily-qa/findings/1/decision", "/daily-qa/questions/1/answer"])
def test_review_and_answer_workflow_is_not_exposed(client, path):
    """1차 개편은 승인·거절·질문 답변을 화면·주소에서 받지 않는다 (기록 표는 남는다)."""
    assert client.post(path, data={"decision": "APPROVED", "reviewer": "x", "answer_text": "y"}).status_code in (404, 405)
