"""QA Agent 화면과 API.

이 모듈은 `/qa-agent` prefix 아래에 붙는다 (`app/web/router.py` 가 prefix 만 결정한다).

화면 구성은 규칙 §14 Human Review UI 를 따른다 — 상단에 Gate 판정, 그 아래 Issue /
Specification / TC Coverage / Regression / QA Action 다섯 개 탭.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from threading import Thread

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.config import get_settings
from app.core.product_knowledge import load_manifest, resolve_config
from app.core.qa_rules import SKILL_TITLES, load_rule_set, rule_coverage
from app.core.storage import Storage
from app.core.uploads import save_upload
from app.modules.qa_agent.analyzer import ANALYSIS_STAGES, QaAgentAnalyzer
from app.modules.qa_agent.gates import ExecutionContext
from app.modules.qa_agent.rule_capability import (
    MODE_LABELS,
    RULE_CAPABILITY,
    STATUS_LABELS,
    summary as capability_summary,
)
from app.modules.qa_agent.schemas import AXIS_DESCRIPTIONS, AXIS_LABELS, TC_JUDGMENT_LABELS

router = APIRouter()
templates = Jinja2Templates(
    directory=[Path(__file__).parent / "templates", get_settings().root / "app" / "web" / "templates"]
)
storage = Storage()

MODULE_NAME = "qa_agent"

QA_DECISIONS = {
    "APPROVED": "승인",
    "REJECTED": "거절",
    "EDITED": "수정 후 승인",
    "NEED_EVIDENCE": "근거 추가 필요",
}

#: 관찰 수단 선택 목록 (규칙 §5.2 · §10). QA 가 화면에서 고른다.
OBSERVATION_MEANS = {
    "ui": "UI 화면",
    "log": "제품 로그",
    "api": "API / WebSocket",
    "db": "DB",
    "device": "실장비",
    "dicom": "DICOM 수신 장비",
}


def _bool_or_none(value: str) -> bool | None:
    """라디오 3지 선택(예/아니오/모름)을 bool|None 으로. 미입력을 False 로 바꾸지 않는다."""
    if value == "yes":
        return True
    if value == "no":
        return False
    return None


def _context_from_form(
    can_execute: str,
    can_expose: str,
    has_dose_table: str,
    test_data_ready: str,
    observation_means: list[str],
    required_means: list[str],
    draft_allowed: bool,
    environment_note: str,
) -> ExecutionContext:
    return ExecutionContext(
        can_execute=_bool_or_none(can_execute),
        can_expose=_bool_or_none(can_expose),
        has_dose_table=_bool_or_none(has_dose_table),
        test_data_ready=_bool_or_none(test_data_ready),
        observation_means=tuple(observation_means),
        required_means=tuple(required_means),
        draft_allowed=draft_allowed,
        environment_note=environment_note.strip(),
    )


def _product_readiness(product: str) -> dict:
    """이 제품으로 분석을 돌릴 수 있는지. 없는 것을 먼저 보여주고 Knowledge 화면으로 보낸다."""
    rule_set = load_rule_set(product)
    config = resolve_config(product)
    export_dir = Path(config.issue_source.export_dir) if config and config.issue_source.export_dir else None
    return {
        "product": product,
        "specifications": len(storage.active_documents("specification", product)),
        "testcases": len(storage.active_documents("testcase", product)),
        "rules_available": rule_set.available,
        "rule_revision": rule_set.revision,
        "rule_documents": rule_set.revisions(),
        "knowledge_synced_at": load_manifest(product).get("synced_at", ""),
        "issue_export_available": bool(export_dir and export_dir.is_dir()),
        "issue_export_dir": str(export_dir) if export_dir else "",
    }


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def home(request: Request, product: str = ""):
    products = storage.list_products()
    selected = product or (products[0] if products else "")
    analyzer = QaAgentAnalyzer(storage=storage)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "products": products,
            "selected": selected,
            "readiness": _product_readiness(selected) if selected else None,
            "issue_ids": analyzer.available_issue_ids(selected) if selected else [],
            "observation_means": OBSERVATION_MEANS,
        },
    )


@router.get("/guide", response_class=HTMLResponse)
def guide(request: Request):
    return templates.TemplateResponse(
        request,
        "guide.html",
        {"axes": AXIS_LABELS, "axis_descriptions": AXIS_DESCRIPTIONS, "judgments": TC_JUDGMENT_LABELS, "skills": SKILL_TITLES},
    )


@router.get("/rules", response_class=HTMLResponse)
def rules(request: Request, product: str = ""):
    products = storage.list_products()
    selected = product or (products[0] if products else "")
    rule_set = load_rule_set(selected) if selected else None
    entries = sorted(RULE_CAPABILITY.values(), key=lambda capability: int(capability.section))
    return templates.TemplateResponse(
        request,
        "rules.html",
        {
            "products": products,
            "selected": selected,
            "entries": entries,
            "mode_labels": MODE_LABELS,
            "status_labels": STATUS_LABELS,
            "summary": capability_summary(),
            "coverage": rule_coverage(rule_set) if rule_set else None,
            "skills": SKILL_TITLES,
            "slice_sizes": (
                {
                    skill: {
                        "sections": len(rule_set.slice(skill=skill, char_budget=6000)),
                        "chars": sum(section.size for section in rule_set.slice(skill=skill, char_budget=6000)),
                    }
                    for skill in SKILL_TITLES
                }
                if rule_set and rule_set.available
                else {}
            ),
        },
    )


@router.get("/analyses", response_class=HTMLResponse)
def history(request: Request, page: int = 1):
    page_size = 25
    analyses, total = storage.list_analyses(limit=page_size, offset=(max(page, 1) - 1) * page_size, module=MODULE_NAME)
    for item in analyses:
        result = item.get("result") or {}
        item["issue_id"] = result.get("issue_id", "")
        item["gate_blocked"] = result.get("status") == "GATE_BLOCKED"
        item["llm_calls"] = result.get("request_count", 0)
    total_pages = max((total + page_size - 1) // page_size, 1)
    return templates.TemplateResponse(
        request, "history.html", {"analyses": analyses, "total": total, "page": max(page, 1), "total_pages": total_pages}
    )


@router.get("/analyses/{job_id}", response_class=HTMLResponse)
def analysis_detail(request: Request, job_id: str):
    analysis = storage.get_analysis(job_id)
    if not analysis:
        raise HTTPException(404, "분석 작업을 찾을 수 없습니다.")
    result = analysis.get("result") or {}
    approvals = storage.qa_agent_approval_map(job_id)
    return templates.TemplateResponse(
        request,
        "analysis.html",
        {
            "analysis": analysis,
            "result": result,
            "issue": result.get("issue") or {},
            "gates": result.get("gates") or {},
            "decision": result.get("decision") or {},
            "evidence": result.get("evidence") or {},
            "validation": result.get("validation") or {},
            "retrieval": result.get("retrieval") or {},
            "audit": result.get("ai_audit") or {},
            "axes": AXIS_LABELS,
            "judgments": TC_JUDGMENT_LABELS,
            "qa_decisions": QA_DECISIONS,
            "approvals": {f"{kind}|{label}": row for (kind, label), row in approvals.items()},
            "approval_stats": storage.qa_agent_approval_stats(),
        },
    )


@router.post("/analyses/{job_id}/approve")
def approve(job_id: str, claim_kind: str = Form(...), claim_label: str = Form(...), qa_decision: str = Form(...), qa_note: str = Form("")):
    """QA 결정을 기록한다. AI 판정을 덮어쓰지 않고 별도 행으로 쌓는다 (규칙 §20)."""
    analysis = storage.get_analysis(job_id)
    if not analysis:
        raise HTTPException(404, "분석 작업을 찾을 수 없습니다.")
    if analysis["status"] != "DONE":
        raise HTTPException(409, "완료된 분석만 승인할 수 있습니다.")
    if qa_decision not in QA_DECISIONS:
        raise HTTPException(400, f"알 수 없는 결정입니다: {qa_decision}")
    storage.save_qa_agent_approval(job_id, claim_kind, claim_label, qa_decision, qa_note.strip())
    return RedirectResponse(f"/qa-agent/analyses/{job_id}#tab-action", status_code=303)


def _run_job(job_id: str, product: str, issue_id: str, issue_path: str, context: ExecutionContext, scope_note: str) -> None:
    try:
        storage.update_analysis(job_id, "RUNNING")
        result = QaAgentAnalyzer(storage=storage).run(
            product=product,
            issue_id=issue_id,
            issue_path=Path(issue_path) if issue_path else None,
            context=context,
            analysis_id=job_id,
            scope_note=scope_note,
        )
        storage.update_analysis(job_id, "DONE", result=result.as_dict())
    except Exception as exc:
        storage.update_analysis(job_id, "FAILED", error=str(exc))


def _ensure_job_capacity() -> None:
    limit = int(get_settings().get("analysis.max_concurrent_jobs", 2) or 2)
    if storage.active_analysis_count() >= limit:
        raise HTTPException(429, f"동시에 실행할 수 있는 분석은 최대 {limit}건입니다. 실행 중인 작업이 끝난 뒤 다시 시도하세요.")


def resume_queued_jobs() -> int:
    """프로세스가 작업 시작 전에 종료된 QUEUED QA Agent 분석을 저장된 입력으로 복구한다."""
    resumed = 0
    for item in storage.queued_analyses(MODULE_NAME):
        request = item["request"] or {}
        issue_path = str(request.get("issue_path") or "")
        if issue_path and not Path(issue_path).exists():
            storage.update_analysis(item["id"], "FAILED", error="서버 재시작 후 업로드한 Issue 파일을 찾지 못해 자동 복구할 수 없습니다.")
            continue
        Thread(
            target=_run_job,
            args=(
                item["id"],
                str(request.get("product") or ""),
                str(request.get("issue_id") or ""),
                issue_path,
                ExecutionContext(**(request.get("context") or {})),
                str(request.get("scope_note") or ""),
            ),
            daemon=True,
            name=f"qa-agent-{item['id']}",
        ).start()
        resumed += 1
    return resumed


@router.post("/analyses")
def start_analysis(
    background_tasks: BackgroundTasks,
    product: str = Form(...),
    issue_id: str = Form(""),
    issue_file: UploadFile | None = File(default=None),
    scope_note: str = Form(""),
    can_execute: str = Form("unknown"),
    can_expose: str = Form("unknown"),
    has_dose_table: str = Form("unknown"),
    test_data_ready: str = Form("unknown"),
    observation_means: list[str] = Form(default=[]),
    required_means: list[str] = Form(default=[]),
    draft_allowed: bool = Form(False),
    environment_note: str = Form(""),
):
    issue_id = issue_id.strip()
    upload = issue_file if issue_file and issue_file.filename else None
    if not issue_id and upload is None:
        raise HTTPException(400, "Issue ID를 고르거나 Polarion Export JSON을 첨부하세요.")

    from app.modules.impact_analyzer.router import daily_token_status

    token_status = daily_token_status()
    if token_status["exceeded"]:
        raise HTTPException(
            429,
            f"오늘 Gemini 누적 토큰 사용량({token_status['used']:,})이 설정한 한도({token_status['limit']:,})를 초과해 분석을 실행할 수 없습니다. "
            "config.yaml의 analysis.daily_token_limit을 조정하세요.",
        )
    _ensure_job_capacity()

    issue_path = ""
    if upload is not None:
        issue_path = str(save_upload(upload, get_settings().path("storage.upload_dir"), {".json"}))

    context = _context_from_form(
        can_execute, can_expose, has_dose_table, test_data_ready, observation_means, required_means, draft_allowed, environment_note
    )
    job_id = uuid.uuid4().hex[:12]
    request_snapshot = {
        "product": product,
        "issue_id": issue_id,
        "issue_path": issue_path,
        "issue_file": upload.filename if upload else "",
        "scope_note": scope_note.strip(),
        "context": {
            "can_execute": context.can_execute,
            "can_expose": context.can_expose,
            "has_dose_table": context.has_dose_table,
            "test_data_ready": context.test_data_ready,
            "observation_means": list(context.observation_means),
            "required_means": list(context.required_means),
            "draft_allowed": context.draft_allowed,
            "environment_note": context.environment_note,
        },
        "readiness": _product_readiness(product),
    }
    storage.create_analysis(job_id, stage_total=len(ANALYSIS_STAGES), request=request_snapshot, module=MODULE_NAME)
    background_tasks.add_task(_run_job, job_id, product, issue_id, issue_path, context, scope_note.strip())
    return {"job_id": job_id, "status_url": f"/qa-agent/analyses/{job_id}"}


@router.get("/issues", response_class=HTMLResponse)
def issue_list(request: Request, product: str = ""):
    """Export 폴더의 Issue 목록. 화면에서 제품을 바꿀 때 쓰는 조각 응답이다."""
    analyzer = QaAgentAnalyzer(storage=storage)
    issue_ids = analyzer.available_issue_ids(product) if product else []
    return HTMLResponse("".join(f'<option value="{issue_id}">{issue_id}</option>' for issue_id in issue_ids))


@router.get("/readiness")
def readiness(product: str):
    """이 제품으로 분석 가능한지 JSON 으로. 실행 전에 무엇이 없는지 확인하는 데 쓴다."""
    return _product_readiness(product)


@router.get("/analyses/{job_id}/export")
def export_result(job_id: str):
    """결과 JSON 원문. 다른 도구로 넘길 때 쓴다 (로드맵 §15 JSON 출력)."""
    analysis = storage.get_analysis(job_id)
    if not analysis:
        raise HTTPException(404, "분석 작업을 찾을 수 없습니다.")
    return json.loads(json.dumps(analysis.get("result") or {}, ensure_ascii=False))
