from __future__ import annotations

import asyncio
import json
import uuid
from threading import Thread
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from app.core.config import get_settings, reload_settings
from app.core.evaluation import aggregate_evaluations, evaluate_analysis, parse_tc_ids
from app.core.storage import Storage
from app.core.uploads import save_upload
from app.modules.impact_analyzer.regression_analyzer import RegressionAnalyzer
from app.modules.impact_analyzer.schemas import ANALYSIS_STAGES

router = APIRouter()
templates = Jinja2Templates(directory=[Path(__file__).parent / "templates", get_settings().root / "app" / "web" / "templates"])
storage = Storage()


def _today_start_iso() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def daily_token_status() -> dict:
    limit = int(get_settings().get("analysis.daily_token_limit", 0) or 0)
    used = storage.tokens_used_since(_today_start_iso())
    return {"used": used, "limit": limit, "exceeded": limit > 0 and used >= limit}


@router.get("/impact-analyzer", response_class=HTMLResponse)
def home(request: Request):
    products = [p for p in storage.list_products() if storage.active_documents("specification", p) and storage.active_documents("testcase", p)]
    return templates.TemplateResponse(request, "index.html", {"products": products})


@router.get("/impact-analyzer/guide", response_class=HTMLResponse)
def guide(request: Request):
    return templates.TemplateResponse(request, "guide.html", {})


@router.get("/guide")
def legacy_guide_redirect():
    """기존 북마크를 회귀 분석 전용 사용법으로 연결한다."""
    return RedirectResponse("/impact-analyzer/guide", status_code=308)


ANALYSIS_HISTORY_PAGE_SIZE = 25


@router.get("/analyses", response_class=HTMLResponse)
def analysis_history(request: Request, status: str = "", product: str = "", q: str = "", page: int = 1):
    page = max(page, 1)
    analyses, total = storage.list_analyses(
        limit=ANALYSIS_HISTORY_PAGE_SIZE, offset=(page - 1) * ANALYSIS_HISTORY_PAGE_SIZE,
        status=status or None, product=product or None, search=q or None,
        # 여러 기능이 같은 analyses 테이블을 쓰므로 이 화면은 Regression 분석만 보여준다.
        module="impact_analyzer",
    )
    for item in analyses:
        result = item.get("result") or {}
        decisions = result.get("decisions") or []
        item["recommended_count"] = sum(bool(decision.get("recommended")) for decision in decisions)
        item["impact_counts"] = {
            impact: sum(decision.get("impact") == impact for decision in decisions)
            for impact in ("HIGH", "MEDIUM", "LOW", "NONE")
        }
    total_pages = max((total + ANALYSIS_HISTORY_PAGE_SIZE - 1) // ANALYSIS_HISTORY_PAGE_SIZE, 1)
    return templates.TemplateResponse(
        request,
        "analyses.html",
        {
            "analyses": analyses, "total": total, "page": page, "total_pages": total_pages,
            "filters": {"status": status, "product": product, "q": q},
            "products": storage.list_products(),
        },
    )


@router.get("/analyses/{job_id}/view", response_class=HTMLResponse)
def analysis_detail(request: Request, job_id: str):
    analysis = storage.get_analysis(job_id)
    if not analysis:
        raise HTTPException(404, "분석 작업을 찾을 수 없습니다.")
    result = analysis.get("result") or {}
    evaluation = storage.get_analysis_evaluation(job_id)
    evaluation_report = evaluate_analysis(result, evaluation) if evaluation else None
    reports = [
        evaluate_analysis(item["result"], {"case_id": item["analysis_id"], "expected_tc_ids": item["expected_tc_ids"]})
        for item in storage.list_evaluated_analyses()
    ]
    return templates.TemplateResponse(
        request,
        "analysis_detail.html",
        {"analysis": analysis, "result": result, "audit": result.get("ai_audit") or {},
         "evaluation": evaluation, "evaluation_report": evaluation_report,
         "evaluation_summary": aggregate_evaluations(reports)},
    )


@router.post("/analyses/{job_id}/evaluation")
def save_evaluation(job_id: str, expected_tc_ids: str = Form(""), qa_note: str = Form("")):
    analysis = storage.get_analysis(job_id)
    if not analysis:
        raise HTTPException(404, "분석 작업을 찾을 수 없습니다.")
    if analysis["status"] != "DONE" or not analysis.get("result"):
        raise HTTPException(409, "완료된 분석만 QA 정답을 확정할 수 있습니다.")
    storage.save_analysis_evaluation(job_id, parse_tc_ids(expected_tc_ids), qa_note.strip())
    return RedirectResponse(f"/analyses/{job_id}/view?evaluation=saved", status_code=303)


def _run_job(job_id: str, changes: list[Path], product: str, notes: str) -> None:
    try:
        storage.update_analysis(job_id, "RUNNING")
        result = RegressionAnalyzer().run_for_product(changes, product, analysis_id=job_id, user_notes=notes)
        storage.update_analysis(job_id, "DONE", result=result.model_dump(mode="json"))
    except Exception as exc:
        storage.update_analysis(job_id, "FAILED", error=str(exc))


def _ensure_job_capacity() -> None:
    limit = int(get_settings().get("analysis.max_concurrent_jobs", 2) or 2)
    if storage.active_analysis_count() >= limit:
        raise HTTPException(429, f"동시에 실행할 수 있는 분석은 최대 {limit}건입니다. 실행 중인 작업이 끝난 뒤 다시 시도하세요.")


def resume_queued_jobs() -> int:
    """프로세스가 작업 시작 전에 종료된 QUEUED Regression 분석을 저장된 입력으로 복구한다."""
    resumed = 0
    for item in storage.queued_analyses("impact_analyzer"):
        request = item["request"]
        paths = [Path(value) for value in request.get("change_paths") or []]
        if (request.get("change_files") and (not paths or any(not path.exists() for path in paths))) or (not paths and not str(request.get("user_notes") or "").strip()):
            storage.update_analysis(item["id"], "FAILED", error="서버 재시작 후 원본 입력을 찾지 못해 자동 복구할 수 없습니다.")
            continue
        Thread(
            target=_run_job,
            args=(item["id"], paths, str(request.get("product") or ""), str(request.get("user_notes") or "")),
            daemon=True,
            name=f"analysis-{item['id']}",
        ).start()
        resumed += 1
    return resumed


@router.post("/analyses")
def start_analysis(background_tasks: BackgroundTasks, product: str = Form(...), notes: str = Form(""), change_files: list[UploadFile] = File(default=[])):
    notes = notes.strip()
    uploads = [f for f in change_files if f and f.filename]
    if not uploads and not notes:
        raise HTTPException(400, "변경문서를 첨부하거나 요청 사항을 입력하세요.")
    token_status = daily_token_status()
    if token_status["exceeded"]:
        raise HTTPException(429, f"오늘 Gemini 누적 토큰 사용량({token_status['used']:,})이 설정한 한도({token_status['limit']:,})를 초과해 분석을 실행할 수 없습니다. config.yaml의 analysis.daily_token_limit을 조정하세요.")
    if not storage.active_documents("specification", product) or not storage.active_documents("testcase", product):
        raise HTTPException(404, f"'{product}' 제품에 등록된 사양서 또는 TC가 없습니다. Knowledge 메뉴에서 먼저 등록하세요.")
    _ensure_job_capacity()
    changes = [save_upload(f, get_settings().path("storage.upload_dir"), {".pdf", ".docx"}) for f in uploads]
    job_id = uuid.uuid4().hex[:12]
    request_snapshot = {
        "product": product,
        "user_notes": notes,
        "change_files": [upload.filename for upload in uploads],
        "change_paths": [str(path) for path in changes],
        "knowledge_documents": [
            {key: doc.get(key) for key in ("id", "kind", "product", "version", "revision", "name", "created_at")}
            for kind in ("specification", "testcase") for doc in storage.active_documents(kind, product)
        ],
    }
    storage.create_analysis(job_id, stage_total=len(ANALYSIS_STAGES), request=request_snapshot, module="impact_analyzer")
    background_tasks.add_task(_run_job, job_id, changes, product, notes)
    return {"job_id": job_id, "status_url": f"/analyses/{job_id}"}


@router.post("/analyses/{job_id}/retry")
def retry_analysis(job_id: str, background_tasks: BackgroundTasks):
    previous = storage.get_analysis(job_id)
    if not previous:
        raise HTTPException(404, "분석 작업을 찾을 수 없습니다.")
    if previous["status"] not in {"FAILED", "DONE"}:
        raise HTTPException(409, "완료되거나 실패한 분석만 재실행할 수 있습니다.")
    request = dict(previous.get("request") or {})
    product = request.get("product", "")
    paths = [Path(value) for value in request.get("change_paths") or []]
    if request.get("change_files") and (not paths or any(not path.exists() for path in paths)):
        raise HTTPException(409, "이 분석은 재실행용 원본 경로가 없거나 파일이 삭제됐습니다. 변경 문서를 다시 업로드하세요.")
    if not paths and not str(request.get("user_notes") or "").strip():
        raise HTTPException(409, "재실행 가능한 입력이 저장되지 않았습니다.")
    _ensure_job_capacity()
    new_id = uuid.uuid4().hex[:12]
    request["retry_of"] = job_id
    storage.create_analysis(new_id, stage_total=len(ANALYSIS_STAGES), request=request, module="impact_analyzer")
    background_tasks.add_task(_run_job, new_id, paths, product, str(request.get("user_notes") or ""))
    return {"job_id": new_id, "retry_of": job_id, "status_url": f"/analyses/{new_id}"}


@router.get("/analyses/{job_id}")
def job_status(job_id: str):
    job = storage.get_analysis(job_id)
    if not job:
        raise HTTPException(404, "분석 작업을 찾을 수 없습니다.")
    return job


@router.get("/analyses/{job_id}/stream")
async def job_status_stream(job_id: str):
    """SSE로 실제 backend 단계 진행 상황을 push한다. 가짜 percentage는 계산하지 않는다 —
    stage_index/stage_total은 RegressionAnalyzer._execute가 실제로 지나간 단계만 기록한다."""
    if not storage.get_analysis(job_id):
        raise HTTPException(404, "분석 작업을 찾을 수 없습니다.")

    async def event_source():
        last_payload = None
        while True:
            job = storage.get_analysis(job_id)
            if not job:
                break
            payload = json.dumps(job, ensure_ascii=False, default=str)
            if payload != last_payload:
                yield f"data: {payload}\n\n"
                last_payload = payload
            if job["status"] in ("DONE", "FAILED"):
                break
            await asyncio.sleep(0.7)

    return StreamingResponse(event_source(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/config/status")
def config_status():
    """Gemini Key 설정 여부만 반환한다. Key 값은 포함하지 않는다."""
    status = get_settings().secret_status()
    status["daily_token_usage"] = daily_token_status()
    return status


@router.post("/config/reload")
def config_reload():
    """secrets.txt/secrets.json을 수정한 뒤 재시작 없이 다시 읽는다."""
    return reload_settings().secret_status()


@router.get("/reports/{filename}")
def report(filename: str):
    safe = Path(filename).name
    path = get_settings().path("storage.report_dir") / safe
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="text/html")


@router.get("/exports/{filename}")
def export(filename: str):
    safe = Path(filename).name
    path = get_settings().path("storage.export_dir") / safe
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, filename=safe)


@router.get("/generated_tc/{filename}")
def generated_tc(filename: str):
    safe = Path(filename).name
    path = get_settings().path("storage.generated_tc_dir") / safe
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, filename=safe)
