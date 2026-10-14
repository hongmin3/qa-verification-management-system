"""QA Agent 화면과 API.

이 모듈은 `/qa-agent` prefix 아래에 붙는다 (`app/web/router.py` 가 prefix 만 결정한다).

- `/qa-agent` 는 변경 탐지 기반 QA Intelligence 대시보드다(SPEC REQ-QAINTEL-019). 예약·수동 실행이
  같은 파이프라인(`app/modules/daily_qa/pipeline.py`)을 쓰고, 이 화면은 결과를 읽기만 한다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.config import get_settings
from app.core.qa_rules import SKILL_TITLES, load_rule_set, rule_coverage
from app.core.storage import Storage
from app.modules.qa_agent import dashboard as dash
from app.modules.qa_agent import run_view
from app.modules.qa_agent.schemas import AXIS_LABELS
from app.modules.qa_agent.rule_capability import (
    MODE_LABELS,
    RULE_CAPABILITY,
    STATUS_LABELS,
    summary as capability_summary,
)

router = APIRouter()
templates = Jinja2Templates(
    directory=[Path(__file__).parent / "templates", get_settings().root / "app" / "web" / "templates"]
)
storage = Storage()

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


@router.get("/run-preview")
def run_preview(product: str = "", since: str = "", until: str = ""):
    try:
        return JSONResponse(dash.run_preview(_choice(product), since, until), headers={"Cache-Control": "no-store"})
    except dash.PeriodError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)


@router.get("/automation-policy")
def automation_policy_status(product: str = ""):
    from app.modules.daily_qa.automation_policy import get_policy
    choice = _choice(product)
    return get_policy(choice.store, choice.cfg.slug)


@router.post("/automation-policy")
def save_automation_policy(request: Request, product: str = Form(""), enabled: bool = Form(False),
                           validation_period: bool = Form(False)):
    from app.modules.daily_qa.automation_policy import save_policy
    choice = _choice(product)
    save_policy(choice.store, choice.cfg.slug, enabled=enabled, validation_period=validation_period,
                changed_by=request.client.host if request.client else "")
    return RedirectResponse(f"/qa-agent?product={quote(choice.cfg.product, safe='')}", status_code=303)


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, product: str = ""):
    """현재 제품의 실행에 중단 요청만 남긴다. 외부 PID 를 종료하지 않는다."""
    from app.modules.daily_qa.cancellation import validate_owner
    choice = _choice(product)
    run = choice.store.get_run(run_id)
    if not run or run.get("product") != choice.cfg.slug:
        raise HTTPException(404, "해당 제품의 실행 기록이 없습니다.")
    if run["status"] == "CANCELLED":
        return {"status": "CANCELLED", "run_id": run_id, "product": choice.cfg.product}
    latest = choice.store.list_runs(limit=1, product=choice.cfg.slug)
    if run["status"] != "RUNNING" or not latest or latest[0]["id"] != run_id or not validate_owner(choice.cfg, run_id):
        raise HTTPException(409, "현재 실행과 잠금·프로세스 정보를 확인하지 못했습니다. 상태를 다시 확인해 주세요.")
    if not choice.store.request_cancel(run_id):
        raise HTTPException(409, "실행을 마쳤거나 메일 발송을 이미 시작했습니다. 상태를 다시 확인해 주세요.")
    return JSONResponse({"status": "cancelling", "run_id": run_id, "product": choice.cfg.product}, status_code=202)


@router.post("/runs")
def run_now(product: str = Form(""), since: str = Form(""), until: str = Form("")):
    """[변경사항 점검하기]. 예약 실행과 같은 함수로 분리 프로세스를 띄우고 바로 답한다 (REQ-QAINTEL-021).

    `since`·`until` 을 주면 그 기간의 변경을 분석한다 (REQ-QAINTEL-027).
    """
    from app.modules.daily_qa.scheduled_jobs import launch_detached

    choice = _choice(product)
    try:
        since_day, until_day = dash.parse_run_period(since, until)
    except dash.PeriodError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    refusal, period_warning = dash.period_snapshot_check(choice.cfg, until_day)
    if refusal:
        return JSONResponse({"detail": refusal}, status_code=400)
    result = launch_detached(choice.cfg.product, trigger="manual", since=since_day, until=until_day)
    messages = {
        "running": "점검이 이미 실행 중입니다.",
        "disabled": "점검 기능이 꺼져 있습니다 (daily_qa.enabled).",
        "not_configured": "Polarion 설정(POLARION_HOST / POLARION_TOKEN / 프로젝트)이 없습니다.",
    }
    if result["status"] in messages:
        return JSONResponse({"detail": messages[result["status"]], **result}, status_code=409)
    limit = dash.limit_info(choice)
    if limit:
        # 한도 중에도 변경 감지는 돈다. 무엇이 대기로 남는지 먼저 알린다 (REQ-QAINTEL-025).
        result["warning"] = f"{limit['description']} 이번 실행은 변경 감지만 하고 AI 분석은 대기로 남습니다."
    if period_warning:
        result["warning"] = " ".join(text for text in (result.get("warning", ""), period_warning) if text)
    return JSONResponse(result, status_code=202)


@router.post("/issue-audit")
def issue_audit_now(product: str = Form(""), since: str = Form(""), until: str = Form("")):
    """[현재 이슈 전체 점검]. 이슈 기록이 없는 기간을 지금 이슈 상태 기준으로 점검한다 (REQ-QAINTEL-030)."""
    from app.modules.daily_qa import snapshots
    from app.modules.daily_qa.scheduled_jobs import launch_detached

    choice = _choice(product)
    try:
        since_day, until_day = dash.parse_run_period(since, until)
    except dash.PeriodError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    refusal, _ = dash.period_snapshot_check(choice.cfg, until_day)
    if refusal:
        return JSONResponse({"detail": refusal}, status_code=400)
    if until_day and snapshots.for_settings(choice.cfg).at_or_before(snapshots.KIND_ISSUES, until_day) is None:
        return JSONResponse({"detail": f"{until_day} 이전 이슈 스냅샷이 없어 현재 이슈 전체 점검을 할 수 없습니다. 종료일을 오늘로 고르세요."},
                            status_code=400)
    result = launch_detached(choice.cfg.product, trigger="manual", since=since_day, until=until_day, issue_audit=True)
    messages = {
        "running": "점검이 이미 실행 중입니다.",
        "disabled": "점검 기능이 꺼져 있습니다 (daily_qa.enabled).",
        "not_configured": "Polarion 설정(POLARION_HOST / POLARION_TOKEN / 프로젝트)이 없습니다.",
    }
    if result["status"] in messages:
        return JSONResponse({"detail": messages[result["status"]], **result}, status_code=409)
    result["warning"] = "이슈 변경 기록이 없는 기간은 현재 이슈 상태 기준으로 점검합니다. 기간 중 상태 변화와 중간 댓글은 알 수 없습니다."
    limit = dash.limit_info(choice)
    if limit:
        result["warning"] += f" {limit['description']} AI 점검은 대기로 남습니다. 설정한 자동 점검 주기의 예정 실행이나 [변경사항 점검하기]에서 이어 갈 수 있습니다."
    return JSONResponse(result, status_code=202)


#: 버튼 요청 → (원래 분석 종류, 거절 문구). 원래 분석이 맞지 않으면 띄우지 않는다 (REQ-QAINTEL-016·032).
_ON_DEMAND_SOURCES = {
    "tc-check": ("SPEC_COVERAGE", "사양 변경 분석에서만 기존 TC 검토를 할 수 있습니다."),
    "tc-draft": ("FIXED_ISSUE", "수정 완료 이슈 분석에서만 검증 TC 초안을 만들 수 있습니다."),
}
_LAUNCH_REFUSALS = {
    "running": "점검이 이미 실행 중입니다.",
    "disabled": "점검 기능이 꺼져 있습니다 (daily_qa.enabled).",
}


def _launch_on_demand(choice, on_demand: str, finding_id: int | None = None):
    from app.modules.daily_qa.scheduled_jobs import launch_detached

    result = launch_detached(choice.cfg.product, trigger="manual", on_demand=on_demand, finding_id=finding_id)
    if result["status"] in _LAUNCH_REFUSALS:
        return JSONResponse({"detail": _LAUNCH_REFUSALS[result["status"]], **result}, status_code=409)
    limit = dash.limit_info(choice)
    if limit:
        result["warning"] = f"{limit['description']} 한도가 풀린 뒤 다시 누르세요."
    return JSONResponse(result, status_code=202)


@router.post("/findings/{finding_id}/{action}")
def finding_on_demand(finding_id: int, action: str):
    """[기존 TC 검토]·[검증 TC 초안]. 사람이 누를 때만 TC 를 AI 에 보낸다 (REQ-QAINTEL-016·032)."""
    if action not in _ON_DEMAND_SOURCES:
        raise HTTPException(404, "없는 요청입니다.")
    store = dash.choose("").store
    finding = store.get_finding(finding_id)
    if not finding:
        raise HTTPException(404, "Finding 이 없습니다.")
    wanted, refusal = _ON_DEMAND_SOURCES[action]
    if finding.get("analysis_type") != wanted:
        return JSONResponse({"detail": refusal}, status_code=400)
    return _launch_on_demand(_choice(finding.get("product") or ""), action, finding_id)



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
    findings = store.list_findings(run_id=run_id, limit=-1)
    events = store.list_events(run_id=run_id)
    cards = [dash.card(item) for item in findings]
    return templates.TemplateResponse(request, "run_detail.html", _dash_context(
        run=run, files=files, events=events, cards=cards, view=run_view.build(run, events, cards, files),
        stage_labels=STAGE_LABELS, status_labels=STATUS_LABELS, event_labels=dash.EVENT_LABELS))


@router.get("/runs/{run_id}/findings", response_class=HTMLResponse)
def run_findings(request: Request, run_id: str, page: int = 1):
    """이 실행의 모든 결과. 예전 규칙 점검 결과도 포함한다 (REQ-QAINTEL-020)."""
    store = dash.choose("").store
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(404, "실행 기록이 없습니다.")
    cards = dash.sort_cards([dash.card(item) for item in store.list_findings(run_id=run_id, limit=-1)])
    pages = max(1, (len(cards) + 49) // 50)
    if page < 1 or page > pages:
        raise HTTPException(404, "결과 페이지가 없습니다.")
    return templates.TemplateResponse(request, "run_findings.html", _dash_context(
        run=run, cards=cards[(page - 1) * 50:page * 50], total=len(cards), page=page, pages=pages))


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
    choice = dash.choose(finding.get("product") or "")
    return templates.TemplateResponse(request, "finding_detail.html", _dash_context(
        finding=finding, card=dash.card(finding), explanation=dash.finding_explanation(
            finding, events, polarion=choice.cfg.polarion if choice else None),
        events=events, questions=questions, event_labels=dash.EVENT_LABELS,
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


@router.get("/guide", response_class=HTMLResponse)
def guide(request: Request):
    return templates.TemplateResponse(
        request,
        "guide.html",
        {},
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
