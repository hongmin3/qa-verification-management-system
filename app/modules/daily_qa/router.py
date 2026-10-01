"""옛 일일 점검 주소 (`/daily-qa`, SPEC REQ-QAINTEL-019).

화면은 `/qa-agent` 대시보드 하나로 합쳤다. 이미 보낸 메일의 링크(`/daily-qa/runs/<실행 ID>`)가 계속
열리도록 GET 주소만 새 화면으로 넘긴다. 승인·거절·근거 추가 필요·질문 답변 화면은 1차 개편에서
보이지 않는다(기록은 표에 그대로 남는다).
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

router = APIRouter()


def _to(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=307)


@router.get("", name="daily_qa_index")
def index():
    return _to("/qa-agent")


@router.get("/queue")
def queue():
    return _to("/qa-agent")


@router.get("/questions")
def questions():
    return _to("/qa-agent")


@router.get("/guide")
def guide():
    return _to("/qa-agent/guide")


@router.get("/runs/{run_id}")
def run_detail(run_id: str):
    return _to(f"/qa-agent/runs/{run_id}")


@router.get("/runs/{run_id}/files/{name}")
def run_file(run_id: str, name: str):
    return _to(f"/qa-agent/runs/{run_id}/files/{name}")


@router.get("/findings/{finding_id}")
def finding_detail(finding_id: int):
    return _to(f"/qa-agent/findings/{finding_id}")
