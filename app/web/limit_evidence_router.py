"""Claude 사용량 한도 증거 사진 보기 (REQ-QAINTEL-039)."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.modules.daily_qa import limit_evidence

router = APIRouter(prefix="/claude", tags=["Claude 사용량"])
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


@router.get("/limit-evidence", response_class=HTMLResponse)
def limit_evidence_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="limit_evidence.html",
                                      context={"items": limit_evidence.list_evidence()})


@router.get("/limit-evidence.json")
def limit_evidence_list() -> JSONResponse:
    """담당자 PC 의 공유폴더 복사 작업도 이 목록을 읽을 수 있다."""
    return JSONResponse({"items": limit_evidence.list_evidence()}, media_type="application/json; charset=utf-8")


@router.get("/limit-evidence/files/{name}")
def limit_evidence_download(name: str) -> FileResponse:
    path = limit_evidence.evidence_file(name)
    if path is None:
        raise HTTPException(404, "증거 파일이 없습니다.")
    media = "image/png" if path.suffix == ".png" else "text/plain; charset=utf-8"
    return FileResponse(path, media_type=media)
