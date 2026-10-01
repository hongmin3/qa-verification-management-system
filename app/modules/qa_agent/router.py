"""QA Agent 화면과 API.

이 모듈은 `/qa-agent` prefix 아래에 붙는다 (`app/web/router.py` 가 prefix 만 결정한다).

- `/qa-agent` 는 변경 탐지 기반 QA Intelligence 대시보드다(SPEC REQ-QAINTEL-019). 예약·수동 실행이
  같은 파이프라인(`app/modules/daily_qa/pipeline.py`)을 쓰고, 이 화면은 결과를 읽기만 한다.
- `/qa-agent/issue-analysis` 는 사람이 이슈 하나를 골라 돌리는 단일 이슈 분석이다(상위 SPEC 5.2절).
  화면 구성은 규칙 §14 Human Review UI 를 따른다 — 상단에 Gate 판정, 그 아래 Issue /
  Specification / TC Coverage / Regression / QA Action 다섯 개 탭.
"""

from __future__ import annotations

import html
import json
import re
import uuid
from pathlib import Path
from threading import Thread

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core import usage as usage_module
from app.core.config import get_settings
from app.core.product_knowledge import load_manifest, resolve_config
from app.core.qa_rules import SKILL_TITLES, load_rule_set, rule_coverage
from app.core.storage import Storage
from app.core.uploads import save_upload
from app.modules.qa_agent import approval_history
from app.modules.qa_agent import dashboard as dash
from app.modules.qa_agent.analyzer import ANALYSIS_STAGES, QaAgentAnalyzer, valid_qa_issue_type
from app.modules.qa_agent.gates import ExecutionContext
from app.modules.qa_agent.rule_capability import (
    MODE_LABELS,
    RULE_CAPABILITY,
    STATUS_LABELS,
    summary as capability_summary,
)
from app.modules.qa_agent.schemas import AXIS_DESCRIPTIONS, AXIS_LABELS, TC_JUDGMENT_LABELS
from app.parsers.polarion_issue import ISSUE_TYPE_LABELS, TYPE_UNCLASSIFIED

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

#: 결과 화면 탭. QA 결정을 기록한 뒤 이 중 하나로 돌려보낸다.
RESULT_TABS = ("issue", "spec", "tc", "regression", "gates", "action")

#: QA 가 분석 양식에서 고를 수 있는 이슈 유형 (규칙 §6). 미분류는 고를 수 없다.
QA_ISSUE_TYPES = {code: label for code, label in ISSUE_TYPE_LABELS.items() if code != TYPE_UNCLASSIFIED}


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
    qa_issue_type: str = "",
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
        qa_issue_type=qa_issue_type.strip(),
    )


def _context_snapshot(context: ExecutionContext) -> dict:
    """요청 기록에 남길 환경 값. 예약 복구가 `ExecutionContext(**snapshot)` 으로 되살린다."""
    return {
        "can_execute": context.can_execute,
        "can_expose": context.can_expose,
        "has_dose_table": context.has_dose_table,
        "test_data_ready": context.test_data_ready,
        "observation_means": list(context.observation_means),
        "required_means": list(context.required_means),
        "draft_allowed": context.draft_allowed,
        "environment_note": context.environment_note,
        "qa_issue_type": context.qa_issue_type,
    }


def _issue_export_available(product: str) -> bool:
    """이 호스트에 제품의 Polarion Export 폴더가 있는가. 없으면 Issue ID 만으로는 이슈를 읽을 수 없다."""
    config = resolve_config(product)
    export_dir = Path(config.issue_source.export_dir) if config and config.issue_source.export_dir else None
    return bool(export_dir and export_dir.is_dir())


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


DOWNLOADABLE_SUFFIXES = (".xlsx", ".json")
#: 실행 번호: `YYYYMMDD-HHMMSS` 또는 `YYYYMMDD-HHMMSS-<제품 slug>` (REQ-QAINTEL-023).
RUN_ID_RE = re.compile(r"\d{8}-\d{6}(?:-[a-z0-9][a-z0-9-]*)?")


def _choice(product: str):
    choice = dash.choose(product)
    if choice is None:
        raise HTTPException(404, "등록된 제품이 아닙니다.")
    return choice


def _dash_context(**values) -> dict:
    values.setdefault("label", dash.label)
    values.setdefault("kst", dash.kst)
    values.setdefault("analysis_labels", dash.ANALYSIS_TYPE_LABELS)
    return values


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def dashboard_home(request: Request, product: str = ""):
    """QA Intelligence 대시보드 (REQ-QAINTEL-019·022)."""
    choice = _choice(product)
    return templates.TemplateResponse(
        request, "dashboard.html", _dash_context(products=choice.products, selected=choice.cfg.product, **dash.dashboard(choice))
    )


@router.get("/status")
def run_status(product: str = ""):
    """실행 상태 JSON. 화면이 몇 초마다 읽어 갱신한다 (REQ-QAINTEL-021 4번)."""
    return dash.status(_choice(product))


@router.post("/runs")
def run_now(product: str = Form(""), since: str = Form(""), until: str = Form("")):
    """[지금 실행]. 예약 실행과 같은 함수로 분리 프로세스를 띄우고 바로 답한다 (REQ-QAINTEL-021).

    `since`·`until` 을 주면 그 기간의 변경을 분석한다 (REQ-QAINTEL-027).
    """
    from app.modules.daily_qa.scheduled_jobs import launch_detached

    choice = _choice(product)
    try:
        since_day, until_day = dash.parse_run_period(since, until)
    except dash.PeriodError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    result = launch_detached(choice.cfg.product, trigger="manual", since=since_day, until=until_day)
    messages = {
        "running": "QA Agent가 이미 실행 중입니다.",
        "disabled": "QA Agent 가 꺼져 있습니다 (daily_qa.enabled).",
        "not_configured": "Polarion 설정(POLARION_HOST / POLARION_TOKEN / 프로젝트)이 없습니다.",
    }
    if result["status"] in messages:
        return JSONResponse({"detail": messages[result["status"]], **result}, status_code=409)
    limit = dash.limit_info(choice)
    if limit:
        # 한도 중에도 변경 감지는 돈다. 무엇이 대기로 남는지 먼저 알린다 (REQ-QAINTEL-025).
        result["warning"] = f"{limit['description']} 이번 실행은 변경 감지만 하고 AI 분석은 대기로 남습니다."
    return JSONResponse(result, status_code=202)


@router.get("/runs/{run_id}", response_class=HTMLResponse)
def run_detail(request: Request, run_id: str):
    """실행 상세: 단계·이벤트·내려받을 파일 (REQ-QAINTEL-020)."""
    choice = dash.choose("")
    store = choice.store
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(404, "실행 기록이 없습니다.")
    from app.modules.daily_qa.report import STAGE_LABELS, STATUS_LABELS

    out_dir = choice.cfg.output_dir / run_id
    files = sorted(path.name for path in out_dir.glob("*") if path.suffix in DOWNLOADABLE_SUFFIXES) if out_dir.is_dir() else []
    findings = store.list_findings(run_id=run_id, limit=500)
    return templates.TemplateResponse(request, "run_detail.html", _dash_context(
        run=run, files=files, events=store.list_events(run_id=run_id), cards=[dash.card(item) for item in findings],
        stage_labels=STAGE_LABELS, status_labels=STATUS_LABELS, event_labels=dash.EVENT_LABELS))


@router.get("/runs/{run_id}/files/{name}")
def run_file(run_id: str, name: str):
    # 실행 번호 모양이 아니면(`..`, 경로 구분자) 실행 폴더 밖을 가리킬 수 있어 거절한다.
    if not RUN_ID_RE.fullmatch(run_id):
        raise HTTPException(404, "파일이 없습니다.")
    output_dir = dash.choose("").cfg.output_dir.resolve()
    base = (output_dir / run_id).resolve()
    target = (base / name).resolve()
    if base.parent != output_dir or target.parent != base or target.suffix not in DOWNLOADABLE_SUFFIXES or not target.is_file():
        raise HTTPException(404, "파일이 없습니다.")
    return FileResponse(target, filename=target.name)


@router.get("/findings/{finding_id}", response_class=HTMLResponse)
def finding_detail(request: Request, finding_id: int):
    """분석 상세. 분석 종류에 맞는 구획을 보인다 (REQ-QAINTEL-020)."""
    store = dash.choose("").store
    finding = store.get_finding(finding_id)
    if not finding:
        raise HTTPException(404, "Finding 이 없습니다.")
    events = store.list_events(ids=finding.get("event_ids") or []) if finding.get("event_ids") else []
    questions = store.list_questions(run_id=finding["run_id"], subject=finding["subject"])
    return templates.TemplateResponse(request, "finding_detail.html", _dash_context(
        finding=finding, card=dash.card(finding), events=events, questions=questions, event_labels=dash.EVENT_LABELS,
        axes=AXIS_LABELS))


@router.get("/period", response_class=HTMLResponse)
def period_view(request: Request, product: str = "", start: str = "", end: str = "", event_type: str = ""):
    """기간 분석 조회 (REQ-QAINTEL-024)."""
    choice = _choice(product)
    try:
        data = dash.period(choice, start, end, event_type)
    except dash.PeriodError as exc:
        raise HTTPException(400, str(exc)) from exc
    return templates.TemplateResponse(request, "period.html", _dash_context(
        products=choice.products, selected=choice.cfg.product, **data))


@router.get("/issue-analysis", response_class=HTMLResponse)
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
            "issue_types": QA_ISSUE_TYPES,
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
            "approval_history": approval_history.history_map(storage, job_id),
            "approval_stats": storage.qa_agent_approval_stats(),
        },
    )


@router.post("/analyses/{job_id}/approve")
def approve(
    job_id: str,
    claim_kind: str = Form(...),
    claim_label: str = Form(...),
    qa_decision: str = Form(...),
    qa_note: str = Form(""),
    return_tab: str = Form("action"),
):
    """QA 결정을 기록한다. AI 판정을 덮어쓰지 않고 별도 행으로 쌓는다 (규칙 §20).

    결정마다 기록 표에 새 줄을 더하고(이전 결정을 남긴다), 최신 결정 표는 새 값으로 바꾼다.
    기록한 뒤에는 폼이 있던 탭으로 돌려보낸다.
    """
    analysis = storage.get_analysis(job_id)
    if not analysis:
        raise HTTPException(404, "분석 작업을 찾을 수 없습니다.")
    if analysis["status"] != "DONE":
        raise HTTPException(409, "완료된 분석만 승인할 수 있습니다.")
    if qa_decision not in QA_DECISIONS:
        raise HTTPException(400, f"알 수 없는 결정입니다: {qa_decision}")
    note = qa_note.strip()
    approval_history.record(storage, job_id, claim_kind, claim_label, qa_decision, note)
    storage.save_qa_agent_approval(job_id, claim_kind, claim_label, qa_decision, note)
    tab = return_tab if return_tab in RESULT_TABS else "action"
    return RedirectResponse(f"/qa-agent/analyses/{job_id}?tab={tab}", status_code=303)


def _spent_tokens(analyzer) -> dict | None:
    """실패한 분석이 이미 쓴 토큰. AI 호출 전에 실패했으면 None (결과를 남기지 않는다).

    AI 호출 뒤에 실패해도 쓴 토큰은 요금에 들어가므로 하루 합계에 넣는다 (OPEN_QUESTIONS 8-10).
    """
    try:
        usage = dict(analyzer.ai_client.token_usage or {}) if analyzer is not None else {}
    except Exception:
        return None
    if not int(usage.get("total_tokens", 0) or 0):
        return None
    return {"token_usage": usage}


def _run_job(job_id: str, product: str, issue_id: str, issue_path: str, context: ExecutionContext, scope_note: str) -> None:
    analyzer = None
    try:
        storage.update_analysis(job_id, "RUNNING")
        analyzer = QaAgentAnalyzer(storage=storage)
        result = analyzer.run(
            product=product,
            issue_id=issue_id,
            issue_path=Path(issue_path) if issue_path else None,
            context=context,
            analysis_id=job_id,
            scope_note=scope_note,
        )
        storage.update_analysis(job_id, "DONE", result=result.as_dict())
    except Exception as exc:
        storage.update_analysis(job_id, "FAILED", result=_spent_tokens(analyzer), error=str(exc))


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
    qa_issue_type: str = Form(""),
):
    issue_id = issue_id.strip()
    qa_issue_type = qa_issue_type.strip()
    upload = issue_file if issue_file and issue_file.filename else None
    if not issue_id and upload is None:
        raise HTTPException(400, "Issue ID를 고르거나 Polarion Export JSON을 첨부하세요.")
    if upload is None and not _issue_export_available(product):
        raise HTTPException(400, "이 서버에는 Polarion Export 폴더가 없어 Issue ID만으로는 이슈를 읽을 수 없습니다. backup.json을 첨부하세요.")
    if qa_issue_type and not valid_qa_issue_type(qa_issue_type):
        raise HTTPException(400, f"알 수 없는 이슈 유형입니다: {qa_issue_type}")

    # 하루 한도는 한국 시간 0시 기준이다 (app/core/usage.py, OPEN_QUESTIONS 8-8).
    token_status = usage_module.daily_token_status(storage=storage)
    if token_status["exceeded"]:
        raise HTTPException(
            429,
            f"오늘 AI 누적 토큰 사용량({token_status['used']:,})이 설정한 한도({token_status['limit']:,})를 초과해 분석을 실행할 수 없습니다. "
            "config.yaml의 analysis.daily_token_limit을 조정하세요.",
        )
    _ensure_job_capacity()

    issue_path = ""
    if upload is not None:
        issue_path = str(save_upload(upload, get_settings().path("storage.upload_dir"), {".json"}))

    context = _context_from_form(
        can_execute,
        can_expose,
        has_dose_table,
        test_data_ready,
        observation_means,
        required_means,
        draft_allowed,
        environment_note,
        qa_issue_type,
    )
    job_id = uuid.uuid4().hex[:12]
    request_snapshot = {
        "product": product,
        "issue_id": issue_id,
        "issue_path": issue_path,
        "issue_file": upload.filename if upload else "",
        "scope_note": scope_note.strip(),
        "context": _context_snapshot(context),
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
    return HTMLResponse(
        "".join(f'<option value="{html.escape(issue_id, quote=True)}">{html.escape(issue_id)}</option>' for issue_id in issue_ids)
    )


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
