"""QA Agent 대시보드 조회 (SPEC REQ-QAINTEL-019·020·022·024).

화면에 보일 값을 저장소에서 모으기만 한다. 결과를 바꾸는 일(승인·거절·질문 답변)은 1차 개편에서
화면에 두지 않는다. 모든 조회는 제품을 받는다 — 제품이 늘면 화면에 제품 선택만 더하면 된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from app.modules.daily_qa import claude_limits, holidays
from app.modules.daily_qa.change_events import (
    ANALYSIS_LABELS,
    COMMENT,
    EVENT_LABELS,
    FIXED_ISSUE,
    ISSUE_COMMENT_ADDED,
    ISSUE_CREATED,
    ISSUE_RD_RESULT_CHANGED,
    NEW_ISSUE,
    SPEC_COVERAGE,
    SPEC_DECISION,
)
from app.modules.daily_qa.product_adapter import RD_FIXED, RD_LABELS, RD_SPEC_LIKE
from app.modules.daily_qa.scheduled_jobs import schedule_settings
from app.modules.daily_qa.settings import DailyQaSettings, configured_products, load
from app.modules.daily_qa.store import DailyQaStore

KST = holidays.KST
MAX_PERIOD_DAYS = 366

VERDICT_LABELS = {
    "SPEC_VIOLATION": "사양 위반 가능성",
    "CONSISTENT_WITH_SPEC": "사양과 일치",
    "SPEC_UNDEFINED": "사양 미정의",
    "SPEC_AMBIGUOUS": "사양 모호",
    "INSUFFICIENT_EVIDENCE": "근거 부족",
    "PARTIALLY_CONSISTENT": "일부만 일치",
    "CONTRADICTS_SPEC": "사양과 다름",
    "SUPPORTED_BY_SPEC": "사양이 뒷받침",
    "PARTIALLY_SUPPORTED": "일부만 뒷받침",
    "SPEC_NOT_FOUND": "뒷받침 사양 없음",
    "STRONG_DUPLICATE": "중복 가능성 높음",
    "POSSIBLE_DUPLICATE": "중복 후보 있음",
    "NO_DUPLICATE_FOUND": "중복 없음",
    "FULLY_COVERED": "기존 TC 로 충분",
    "PARTIALLY_COVERED": "일부만 커버",
    "NOT_COVERED": "커버하는 TC 없음",
    "SPEC_REVIEW_REQUIRED": "사양 검토 필요",
    "UPDATE_EXISTING": "기존 TC 수정",
    "KEEP": "유지",
    "CREATE_NEW": "신규 TC",
    "ROOT_CAUSE_INFORMATION": "원인 정보",
    "RESOLUTION_INFORMATION": "조치 정보",
    "REPRODUCTION_INFORMATION": "재현 조건 정보",
    "SPEC_CLAIM": "사양 주장",
    "REQUIREMENT_INFORMATION": "요구사항 정보",
    "QA_ACTION_REQUIRED": "QA 조치 필요",
    "OTHER_SIGNIFICANT_INFORMATION": "기타 의미 있는 정보",
    "NOT_SIGNIFICANT": "의미 없음",
    "POSSIBLE_REGRESSION": "과거 FIXED 재발 가능성",
    "HISTORICAL_SPEC_DUPLICATE": "과거 Spec 처리 이슈 재등록 가능성",
    "HISTORICAL_DUPLICATE_REREGISTERED": "과거 Duplicate 처리 이슈 재등록 가능성",
    "HISTORICAL_DECISION_CONFLICT": "과거 판정과 충돌",
    "ISSUE_WITHOUT_TC": "관련 이슈를 검증하는 TC 없음",
    "TC_EXPECTED_OUTDATED": "TC Expected 가 옛 사양 기준",
    "FIXED_ISSUE_WITHOUT_REGRESSION_TC": "과거 FIXED 이슈의 Regression TC 없음",
    "SPEC_ISSUE_BEHAVIOR_CHANGED": "과거 Spec 판정 동작이 최신 SRS 와 다름",
    "EXISTING_DEFECT": "해당 동작의 기존 결함",
    "PAST_FIXED": "과거 Fixed 이슈",
    "PAST_SPEC": "과거 Spec 이슈",
    "SAME_FUNCTION_REGRESSION": "같은 기능 Regression",
}
ANALYSIS_TYPE_LABELS = {**ANALYSIS_LABELS, "SRS_REMOVED": "삭제 SRS 참조 TC"}
#: 대시보드에 보일 수동 업로드 지식 문서의 종류. QA 규칙(.md)·지침 프롬프트는 지식 문서로 관리하지 않아
#: 보이지 않는다(사용자 결정 2026-09-30). 점검은 규칙 판 확인에 그 파일을 계속 쓴다(REQ-DAILY-010).
#: 사양서는 ALM 자동 추출 제품(출처 `alm_crawler` 등)이면 빼고, 그 밖의 제품이면 수동 문서로 보인다.
KIND_LABELS = {"specification": "사양서", "manual": "매뉴얼", "testcase": "TC·Checklist"}
MANUAL_KINDS = tuple(KIND_LABELS)


def kst(value: str | None, with_date: bool = True) -> str:
    if not value:
        return ""
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)[:16]
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(KST).strftime("%Y-%m-%d %H:%M" if with_date else "%H:%M")


def label(value: str) -> str:
    return VERDICT_LABELS.get(value, value)


@dataclass
class ProductChoice:
    cfg: DailyQaSettings
    store: DailyQaStore
    products: list[str]


def choose(product: str = "") -> ProductChoice | None:
    """주소의 제품 이름으로 제품을 고른다. 비우면 첫 제품이다. 모르는 제품이면 None."""
    products = configured_products()
    if product and product not in products:
        matches = [name for name in products if name.casefold() == product.casefold() or load(product=name).slug == product.casefold()]
        if not matches:
            return None
        product = matches[0]
    cfg = load(product=product or products[0])
    return ProductChoice(cfg, DailyQaStore(cfg.db_path), products)


# -- 지식 문서 목록 (REQ-QAINTEL-022) --------------------------------------------


def manual_kinds(product: str, root=None) -> tuple[str, ...]:
    """이 제품에서 사람이 올려야 하는 지식 문서 종류. 자동 출처(ALM 등) 종류는 뺀다 (REQ-KNOW-018)."""
    from app.core.product_config import AUTOMATIC_SOURCES
    from app.modules.daily_qa.product_adapter import _find_config

    config = _find_config(product, root)
    kinds = []
    for kind in MANUAL_KINDS:
        try:
            automatic = config is not None and config.source_of(kind) in AUTOMATIC_SOURCES
        except KeyError:
            automatic = False
        if not automatic:
            kinds.append(kind)
    return tuple(kinds)


def knowledge_uploads(product: str, root, storage=None) -> dict:
    """수동으로 올려야 하는 지식 문서와 서버에 올라온 시각 (REQ-QAINTEL-022).

    지식 폴더로 올라온 것은 수집 목록(manifest)에서, Knowledge 화면으로 등록한 것은 등록 문서 표에서 읽는다.
    """
    from app.core.product_knowledge import load_manifest

    kinds = manual_kinds(product, root)
    manifest = load_manifest(product, root)
    rows = []
    seen: set[tuple[str, str]] = set()
    for asset in manifest.get("assets", []):
        if asset.get("kind") not in kinds:
            continue
        seen.add((asset["kind"], asset.get("file_name", "")))
        rows.append({
            "kind": KIND_LABELS[asset["kind"]], "kind_code": asset["kind"], "file_name": asset.get("file_name", ""),
            "uploaded": kst(asset.get("collected_at")),
            "modified": kst(asset.get("modified")), "error": bool(asset.get("error")), "route": "지식 폴더",
        })
    if storage is not None:
        for kind in kinds:
            try:
                documents = storage.active_documents(kind, product)
            except Exception:  # 등록 문서 표를 읽지 못해도 목록의 나머지는 보인다
                documents = []
            for document in documents:
                name = str(document.get("name") or "")
                if (kind, name) in seen or any(name and name in file for (_, file) in seen):
                    continue
                rows.append({
                    "kind": KIND_LABELS[kind], "kind_code": kind, "file_name": name,
                    "uploaded": kst(document.get("created_at")), "modified": "", "error": False, "route": "Knowledge 화면 등록",
                })
    rows = _latest_revisions(rows)
    rows.sort(key=lambda row: (MANUAL_KINDS.index(row["kind_code"]), row["file_name"]))
    return {"available": bool(manifest) or bool(rows), "synced_at": kst(manifest.get("synced_at")), "rows": rows,
            "kinds": [KIND_LABELS[kind] for kind in kinds]}


def _latest_revisions(rows: list[dict]) -> list[dict]:
    """같은 논리 문서의 판이 여럿이면 확실히 더 오래된 판을 뺀다. 판을 비교할 수 없으면 둘 다 둔다.

    논리 문서 판정은 Knowledge 의 규칙을 그대로 쓴다 (REQ-KNOW-009).
    """
    from app.core.product_knowledge import document_logical_key, is_strictly_older_name

    keys = [document_logical_key(row["kind_code"], row["file_name"]) for row in rows]
    return [
        row for index, row in enumerate(rows)
        if not any(
            other is not row and keys[other_index] == keys[index] and is_strictly_older_name(row["file_name"], other["file_name"])
            for other_index, other in enumerate(rows)
        )
    ]


# -- 카드 --------------------------------------------------------------------------


def badges(finding: dict) -> list[str]:
    sections = finding.get("sections") or {}
    kind = finding.get("analysis_type", "")
    out: list[str] = []
    if kind == NEW_ISSUE:
        duplicate = sections.get("duplicate") or {}
        if duplicate.get("verdict") in ("STRONG_DUPLICATE", "POSSIBLE_DUPLICATE"):
            ids = ", ".join(entry["issue_id"] for entry in duplicate.get("candidates") or [])
            out.append(f"{label(duplicate['verdict'])}: {ids}")
        if any(entry.get("past_resolution") in ("SPEC", "NOT_BUG") for entry in duplicate.get("candidates") or []):
            out.append("기존 유사 Issue가 Spec으로 처리된 이력이 있음")
    if kind in (NEW_ISSUE, SPEC_DECISION, FIXED_ISSUE):
        out += [label(flag) for flag in (sections.get("historical") or {}).get("flags") or []]
    if kind == SPEC_COVERAGE:
        tcs = (sections.get("checklist_coverage") or {}).get("tcs") or []
        updates = sum(entry.get("decision") == "UPDATE_EXISTING" for entry in tcs)
        issues = (sections.get("issue_coverage") or {}).get("issues") or []
        out.append(f"기존 Issue {len(issues)}건 · 기존 Checklist {len(tcs)}건")
        if updates or finding.get("draft_tcs"):
            out.append(f"조치: 기존 TC 수정 {updates}건 · 신규 TC {len(finding.get('draft_tcs') or [])}건")
        out += [label(alert.get("type", "")) for alert in sections.get("alerts") or []]
    if kind == "ISSUE_AUDIT":
        # 이슈 기록 없이 지금 상태로 판정했다는 표시 (REQ-QAINTEL-030 결과).
        out.append(sections.get("basis") or "현재 상태 기준(기간 이력 없음)")
        decision = (sections.get("tc_impact") or {}).get("decision")
        if decision:
            out.append(f"TC 영향: {decision}")
    elif finding.get("draft_tcs"):
        out.append(f"검증 TC 초안 {len(finding['draft_tcs'])}건")
    if (sections.get("tc_hold") or {}).get("reason"):
        out.append("Checklist TC 생성 보류")
    return out


def card(finding: dict) -> dict:
    kind = finding.get("analysis_type", "")
    return {
        "id": finding["id"],
        "subject": finding["subject"],
        "title": finding.get("subject_title", ""),
        "kind": ANALYSIS_TYPE_LABELS.get(kind, kind or finding.get("skill", "")),
        "verdict": finding["verdict"],
        "verdict_label": label(finding["verdict"]),
        "summary": finding.get("summary", ""),
        "badges": badges(finding),
        "created": kst(finding.get("created_at")),
        "confidence": finding.get("confidence", ""),
    }


# -- 요약 --------------------------------------------------------------------------


def change_summary(events: list[dict]) -> dict:
    """오늘 변경 요약: 신규 이슈 · Fixed · Spec · 댓글 · SRS (REQ-QAINTEL-019 3번)."""
    summary = {"new_issue": 0, "fixed": 0, "spec": 0, "comment": 0, "srs": 0, "total": len(events)}
    for event in events:
        kind = event["event_type"]
        if kind == ISSUE_CREATED:
            summary["new_issue"] += 1
        elif kind == ISSUE_RD_RESULT_CHANGED:
            after = (event.get("after") or {}).get("rd_result")
            if after == RD_FIXED:
                summary["fixed"] += 1
            elif after in RD_SPEC_LIKE:
                summary["spec"] += 1
        elif kind == ISSUE_COMMENT_ADDED:
            summary["comment"] += 1
        elif kind.startswith("SRS_"):
            summary["srs"] += 1
    return summary


def next_run_text(cfg: DailyQaSettings, now: datetime | None = None) -> str:
    sched = schedule_settings()
    moment = holidays.next_run(now or datetime.now(timezone.utc), cfg.root, sched["day_of_week"], sched["time"],
                               sched["calendar"], sched["extra"], sched["skip_holidays"])
    return moment.strftime("%Y-%m-%d (%a) %H:%M") if moment else ""


def limit_info(choice: ProductChoice, now: datetime | None = None) -> dict | None:
    from app.modules.daily_qa.pipeline import active_limit

    info = active_limit(choice.store, choice.cfg, now)
    return info.as_dict() if info else None


def status(choice: ProductChoice) -> dict:
    from app.modules.daily_qa.pipeline import is_running

    cfg, store = choice.cfg, choice.store
    runs = store.list_runs(limit=1, product=cfg.slug, include_legacy=cfg.is_legacy_product)
    last = runs[0] if runs else None
    return {
        "product": cfg.product,
        "slug": cfg.slug,
        "running": is_running(cfg),
        "last_run": {"id": last["id"], "status": last["status"], "status_label": last["status_label"],
                     "started": kst(last["started_at"]), "finished": kst(last.get("finished_at"))} if last else None,
        "next_run": next_run_text(cfg),
        "claude_limit": limit_info(choice),
        # 앞선 현재 상태 점검이 한도로 남긴 대상 (REQ-QAINTEL-030 순서 5).
        "issue_audit_remaining": len(store.list_events(product=cfg.slug, statuses=("pending", "failed"),
                                                       event_types=("ISSUE_AUDIT_TARGET",))),
    }


def dashboard(choice: ProductChoice) -> dict:
    cfg, store = choice.cfg, choice.store
    runs = store.list_runs(limit=10, product=cfg.slug, include_legacy=cfg.is_legacy_product)
    last = runs[0] if runs else None
    events = store.list_events(product=cfg.slug, run_id=last["id"]) if last else []
    findings = store.list_findings(product=cfg.slug, include_legacy=cfg.is_legacy_product, only_analyses=True, limit=30)
    return {
        "status": status(choice),
        "last_run": last,
        "runs": runs,
        "summary": change_summary(events),
        "cards": [card(item) for item in findings],
        "knowledge": knowledge_uploads(cfg.product, cfg.root, _documents_storage(cfg)),
        "event_labels": EVENT_LABELS,
        "run_period": run_period_defaults(min_day=oldest_snapshot_day(cfg)),
    }


def oldest_snapshot_day(cfg: DailyQaSettings, kind: str = "srs") -> str:
    """가장 오래된 저장 스냅샷 날짜. 기간 입력 달력의 최솟값이다 (REQ-QAINTEL-027)."""
    from app.modules.daily_qa import snapshots

    path = snapshots.for_settings(cfg).oldest(kind)
    return path.stem if path else ""


def period_snapshot_check(cfg: DailyQaSettings, until: str) -> tuple[str, str]:
    """종료일이 지난 날인 기간 실행을 띄우기 전에 저장 스냅샷을 본다. (거절 이유, 경고) (REQ-QAINTEL-027)."""
    from app.modules.daily_qa import snapshots

    if not until:
        return "", ""
    store = snapshots.for_settings(cfg)
    if store.at_or_before(snapshots.KIND_SRS, until) is None:
        oldest = oldest_snapshot_day(cfg)
        if not oldest:
            return "저장된 SRS 스냅샷이 없습니다.", ""
        return f"{until} 이전에 저장된 SRS 스냅샷이 없어 이 기간은 분석할 수 없습니다. 가장 오래된 스냅샷: {oldest}", ""
    if store.at_or_before(snapshots.KIND_ISSUES, until) is None:
        return "", "이 기간에는 저장된 이슈 스냅샷이 없어 SRS 변경만 분석합니다."
    return "", ""


def _documents_storage(cfg: DailyQaSettings):
    from app.core.storage import Storage

    return Storage(db_path=cfg.db_path)


# -- 기간 조회 (REQ-QAINTEL-024) ----------------------------------------------------


class PeriodError(ValueError):
    pass


def parse_period(start: str, end: str, today: date | None = None) -> tuple[date, date]:
    today = today or datetime.now(KST).date()
    try:
        end_day = date.fromisoformat(end) if end else today
        start_day = date.fromisoformat(start) if start else end_day - timedelta(days=6)
    except ValueError as exc:
        raise PeriodError("날짜는 YYYY-MM-DD 로 입력하세요.") from exc
    if start_day > end_day:
        raise PeriodError("시작일이 종료일보다 늦습니다.")
    if (end_day - start_day).days + 1 > MAX_PERIOD_DAYS:
        raise PeriodError(f"조회 기간은 {MAX_PERIOD_DAYS}일까지입니다.")
    return start_day, end_day


def parse_run_period(since: str, until: str, today: date | None = None) -> tuple[str, str]:
    """[지금 실행]의 기간. 비우면 기간 없이 오늘 실행이다. 종료일이 오늘이면 새로 수집하므로 넘기지 않는다.

    시작일을 비우면 종료일 전날이다(종료일도 비면 오늘의 전날).
    """
    today = today or datetime.now(KST).date()
    if not since and not until:
        return "", ""
    try:
        until_day = date.fromisoformat(until) if until else today
        since_day = date.fromisoformat(since) if since else until_day - timedelta(days=1)
    except ValueError as exc:
        raise PeriodError("날짜는 YYYY-MM-DD 로 입력하세요.") from exc
    if until_day > today:
        raise PeriodError("종료일은 오늘보다 늦을 수 없습니다.")
    if since_day > until_day:
        raise PeriodError("시작일이 종료일보다 늦습니다.")
    if (until_day - since_day).days + 1 > MAX_PERIOD_DAYS:
        raise PeriodError(f"조회 기간은 {MAX_PERIOD_DAYS}일까지입니다.")
    return since_day.isoformat(), ("" if until_day == today else until_day.isoformat())


def run_period_defaults(today: date | None = None, min_day: str = "") -> dict:
    """실행 기간 입력칸의 기본값: 시작일 = 전날, 종료일 = 오늘. `min` 은 가장 오래된 스냅샷 날짜다."""
    today = today or datetime.now(KST).date()
    return {"since": (today - timedelta(days=1)).isoformat(), "until": today.isoformat(), "max": today.isoformat(),
            "min": min_day}


def _utc_bounds(start_day: date, end_day: date) -> tuple[str, str]:
    since = datetime.combine(start_day, time.min, tzinfo=KST).astimezone(timezone.utc)
    until = datetime.combine(end_day + timedelta(days=1), time.min, tzinfo=KST).astimezone(timezone.utc)
    return since.isoformat(timespec="seconds"), until.isoformat(timespec="seconds")


def period(choice: ProductChoice, start: str = "", end: str = "", event_type: str = "") -> dict:
    cfg, store = choice.cfg, choice.store
    start_day, end_day = parse_period(start, end)
    since, until = _utc_bounds(start_day, end_day)
    runs = store.list_runs(limit=1000, product=cfg.slug, include_legacy=cfg.is_legacy_product, since=since, until=until)
    events = store.list_events(product=cfg.slug, since=since, until=until,
                               event_types=(event_type,) if event_type else None)
    findings = store.list_findings(product=cfg.slug, include_legacy=cfg.is_legacy_product, only_analyses=True,
                                   since=since, until=until, limit=5000)
    grouped: dict[str, list[dict]] = {}
    for finding in findings:   # 최신이 앞이다
        grouped.setdefault(finding["subject"], []).append(card(finding))
    run_status: dict[str, int] = {}
    for run in runs:
        run_status[run["status_label"]] = run_status.get(run["status_label"], 0) + 1
    by_analysis: dict[str, int] = {}
    for finding in findings:
        key = ANALYSIS_TYPE_LABELS.get(finding["analysis_type"], finding["analysis_type"])
        by_analysis[key] = by_analysis.get(key, 0) + 1
    by_event: dict[str, int] = {}
    for event in store.list_events(product=cfg.slug, since=since, until=until):
        key = EVENT_LABELS.get(event["event_type"], event["event_type"])
        by_event[key] = by_event.get(key, 0) + 1
    return {
        "start": start_day.isoformat(),
        "end": end_day.isoformat(),
        "event_type": event_type,
        "summary": {
            "runs": len(runs),
            "no_change_runs": sum(run["status"] == "NO_CHANGE" for run in runs),
            "run_status": run_status,
            "by_event": by_event,
            "by_analysis": by_analysis,
            "drafts": sum(len(finding.get("draft_tcs") or []) for finding in findings),
        },
        "groups": [{"subject": subject, "latest": cards[0], "earlier": cards[1:]} for subject, cards in grouped.items()],
        "events": events,
        "event_labels": EVENT_LABELS,
        "rd_labels": RD_LABELS,
    }


def limit_labels() -> dict:
    return claude_limits.KIND_LABELS


__all__ = ["choose", "dashboard", "period", "status", "PeriodError", "card", "label", "kst", "knowledge_uploads",
           "COMMENT", "FIXED_ISSUE"]
