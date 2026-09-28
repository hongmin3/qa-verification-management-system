"""일일 점검 검토 화면 (`/daily-qa`, SPEC REQ-DAILY-009).

이 화면은 배치가 저장한 결과를 보여 주고 사람의 결정을 저장하기만 한다. 결정은 이 시스템
안에만 남고 Polarion 이나 원본 TC 파일로 나가지 않는다.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote, unquote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.config import get_settings
from app.modules.daily_qa.report import STAGE_LABELS, STATUS_LABELS
from app.modules.daily_qa.schema import SKILL_LABELS, VERDICTS
from app.modules.daily_qa.settings import load
from app.modules.daily_qa.store import REVIEW_STATUSES, DailyQaStore

router = APIRouter()
templates = Jinja2Templates(
    directory=[Path(__file__).parent / "templates", get_settings().root / "app" / "web" / "templates"]
)

REVIEWER_COOKIE = "daily_qa_reviewer"
DOWNLOADABLE_SUFFIXES = (".xlsx", ".json")


def get_store() -> DailyQaStore:
    return DailyQaStore(load().db_path)


def _context(request: Request, **values) -> dict:
    values.setdefault("skill_labels", SKILL_LABELS)
    values.setdefault("review_statuses", REVIEW_STATUSES)
    values.setdefault("stage_labels", STAGE_LABELS)
    values.setdefault("status_labels", STATUS_LABELS)
    values.setdefault("reviewer", unquote(request.cookies.get(REVIEWER_COOKIE, "")))
    return values


def _remember(response: RedirectResponse, reviewer: str) -> None:
    """검토자 이름을 기억한다. 쿠키 값은 latin-1 만 되므로 한글 이름은 URL 인코딩한다."""
    response.set_cookie(REVIEWER_COOKIE, quote(reviewer.strip()), max_age=60 * 60 * 24 * 180, samesite="lax")


@router.get("", response_class=HTMLResponse, name="daily_qa_index")
def index(request: Request):
    store = get_store()
    runs = store.list_runs(limit=30)
    return templates.TemplateResponse(
        request, "daily_qa_index.html",
        _context(request, runs=runs, counts=store.review_counts(), open_questions=len(store.list_questions(unanswered_only=True))),
    )


@router.get("/runs/{run_id}", response_class=HTMLResponse)
def run_detail(request: Request, run_id: str):
    store = get_store()
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="실행 기록이 없습니다.")
    out_dir = load().output_dir / run_id
    files = sorted(path.name for path in out_dir.glob("*") if path.suffix in DOWNLOADABLE_SUFFIXES) if out_dir.is_dir() else []
    return templates.TemplateResponse(
        request, "daily_qa_run.html",
        _context(request, run=run, files=files, counts=store.review_counts(run_id),
                 findings=store.list_findings(run_id=run_id, limit=50), questions=store.list_questions(run_id=run_id)),
    )


@router.get("/runs/{run_id}/files/{name}")
def run_file(run_id: str, name: str):
    base = (load().output_dir / run_id).resolve()
    target = (base / name).resolve()
    if target.parent != base or target.suffix not in DOWNLOADABLE_SUFFIXES or not target.is_file():
        raise HTTPException(status_code=404, detail="파일이 없습니다.")
    return FileResponse(target, filename=target.name)


@router.get("/queue", response_class=HTMLResponse)
def queue(request: Request, skill: str = "", verdict: str = "", status: str = "PENDING", run_id: str = ""):
    store = get_store()
    findings = store.list_findings(run_id=run_id or None, skill=skill or None, verdict=verdict or None,
                                   review_status=status or None, limit=300)
    verdict_options = VERDICTS.get(skill, ()) if skill else sorted({v for values in VERDICTS.values() for v in values})
    return templates.TemplateResponse(
        request, "daily_qa_queue.html",
        _context(request, findings=findings, filters={"skill": skill, "verdict": verdict, "status": status, "run_id": run_id},
                 verdict_options=verdict_options),
    )


@router.get("/findings/{finding_id}", response_class=HTMLResponse)
def finding_detail(request: Request, finding_id: int):
    finding = get_store().get_finding(finding_id)
    if not finding:
        raise HTTPException(status_code=404, detail="Finding 이 없습니다.")
    return templates.TemplateResponse(request, "daily_qa_finding.html", _context(request, finding=finding))


@router.post("/findings/{finding_id}/decision")
def decide(finding_id: int, decision: str = Form(...), reviewer: str = Form(...), note: str = Form(""), next_url: str = Form("")):
    if not reviewer.strip():
        raise HTTPException(status_code=400, detail="검토자 이름을 입력하세요.")
    if decision == "NEED_EVIDENCE" and not note.strip():
        raise HTTPException(status_code=400, detail="근거 추가 필요는 무엇이 필요한지 메모를 남겨야 합니다.")
    try:
        updated = get_store().review_finding(finding_id, decision, reviewer, note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not updated:
        raise HTTPException(status_code=404, detail="Finding 이 없습니다.")
    target = next_url if next_url.startswith("/daily-qa") else f"/daily-qa/findings/{finding_id}"
    response = RedirectResponse(target, status_code=303)
    _remember(response, reviewer)
    return response


@router.get("/questions", response_class=HTMLResponse)
def questions(request: Request, show: str = "open"):
    store = get_store()
    items = store.list_questions(unanswered_only=(show == "open"))
    return templates.TemplateResponse(request, "daily_qa_questions.html", _context(request, questions=items, show=show))


@router.post("/questions/{question_id}/answer")
def answer(question_id: int, answer_text: str = Form(...), reviewer: str = Form(...)):
    if not reviewer.strip():
        raise HTTPException(status_code=400, detail="답변자 이름을 입력하세요.")
    try:
        updated = get_store().answer_question(question_id, answer_text, reviewer)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not updated:
        raise HTTPException(status_code=404, detail="질문이 없습니다.")
    response = RedirectResponse("/daily-qa/questions", status_code=303)
    _remember(response, reviewer)
    return response


@router.get("/guide", response_class=HTMLResponse)
def guide(request: Request):
    return templates.TemplateResponse(request, "daily_qa_guide.html", _context(request))
