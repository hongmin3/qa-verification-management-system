"""QA Agent 대시보드 조회 (SPEC REQ-QAINTEL-019·020·022·024).

화면에 보일 값을 저장소에서 모으기만 한다. 결과를 바꾸는 일(승인·거절·질문 답변)은 1차 개편에서
화면에 두지 않는다. 모든 조회는 제품을 받는다 — 제품이 늘면 화면에 제품 선택만 더하면 된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import logging
import re
from urllib.parse import quote, urlparse

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

logger = logging.getLogger(__name__)

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
    "SPEC_CLAIM": "사양이라는 댓글 주장",
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
    "NO_QA_IMPACT": "QA 영향 없음",
    "QA_CHECK_NEEDED": "QA 확인 필요",
    "CONFLICTS_WITH_PAST_DECISION": "과거 판정과 다름",
    "SPEC_UNCLEAR": "사양 불명확",
    "Review Needed": "확인 필요",
    "Confirmed": "근거 확인됨",
    "Unsupported": "뒷받침 근거 없음",
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


def run_preview(choice: ProductChoice, since: str = "", until: str = "") -> dict:
    """Saved pending work only. Never collect, launch, or update state (REQ-QAINTEL-041)."""
    from math import ceil
    from app.modules.daily_qa.change_events import ISSUE_AUDIT, ISSUE_AUDIT_TARGET, group_targets, merge_comment_targets
    from app.modules.daily_qa.input_budget import select_backlog

    today = datetime.now(KST).date()
    _, past_until = parse_run_period(since, until, today)
    cfg = choice.cfg
    pending = choice.store.list_events(product=cfg.slug, statuses=("pending", "failed"))
    selected, _ = select_backlog(pending, today.strftime('%Y%m%d'), cfg.intelligence.backlog_max_events)
    if past_until:
        selected = []
    audit = [event for event in pending if event['event_type'] == ISSUE_AUDIT_TARGET]

    def describe(events, waiting):
        grouped = group_targets(events)
        merge_comment_targets(grouped)
        counts = {kind: len(targets) for kind, targets in grouped.items() if targets}
        # This is the count-only lower estimate. SRS grouping and input sizes can add batches.
        batches = sum(ceil(count / max(1, cfg.intelligence.audit_batch_size if kind == ISSUE_AUDIT else cfg.batch_size))
                      for kind, count in counts.items())
        return {"waiting": waiting, "selected": len(events), "deferred": waiting - len(events),
                "targets": counts, "batches_by_count": batches}

    return {"changes": describe(selected, len(pending)), "audit": describe(audit, len(audit)),
            "new_changes": None, "historical": bool(past_until), "max_tasks": cfg.max_tasks_per_run,
            "labels": ANALYSIS_LABELS}


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


# -- 결론 단계 (REQ-QAINTEL-033) -------------------------------------------------------

#: 화면에 보이는 결론. 분석마다 다른 판정 값을 다섯 단계로 줄인다. 목록도 이 순서로 보인다.
LEVEL_ORDER = ("사양과 다름", "검토 필요", "근거 부족", "참고", "문제 없음")
LEVEL_CLASSES = {"사양과 다름": "level-conflict", "검토 필요": "level-check", "근거 부족": "level-weak",
                 "참고": "level-info", "문제 없음": "level-ok"}
_LEVELS = {
    "사양과 다름": ("SPEC_VIOLATION", "CONTRADICTS_SPEC", "CONFLICTS_WITH_PAST_DECISION"),
    "근거 부족": ("INSUFFICIENT_EVIDENCE",),
    "참고": ("ROOT_CAUSE_INFORMATION", "RESOLUTION_INFORMATION", "REPRODUCTION_INFORMATION", "OTHER_SIGNIFICANT_INFORMATION"),
    "문제 없음": ("CONSISTENT_WITH_SPEC", "SUPPORTED_BY_SPEC", "FULLY_COVERED", "NO_QA_IMPACT", "NOT_SIGNIFICANT"),
}
VERDICT_LEVEL = {verdict: name for name, verdicts in _LEVELS.items() for verdict in verdicts}
TODO_LIMIT = 3


def level(finding: dict) -> str:
    """판정 값 → 결론 단계. 표에 없는 값(검토 쪽 판정, 옛 점검의 판정)은 `검토 필요` 다."""
    return VERDICT_LEVEL.get(finding.get("verdict", ""), "검토 필요")


def todos(finding: dict) -> list[str]:
    """QA 할 일. 권고 → 확인할 점 → `action` 줄 순서로 찾고 3줄까지 보인다."""
    sections = finding.get("sections") or {}
    found = list((sections.get("recommendation") or {}).get("actions") or []) + \
        list((sections.get("recommendation") or {}).get("checks") or []) + list((sections.get("qa_analysis") or {}).get("checks") or [])
    if not found:
        found = [line.strip(" -•\t") for line in str(finding.get("action") or sections.get("action") or "").splitlines()]
    return [str(item).strip() for item in found if str(item).strip()][:TODO_LIMIT]


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


LEGACY_KIND = "이전 형식"
NO_FRESHNESS = {"state": "unknown", "after_events": 0, "latest_update": ""}


def ago(value, now: datetime | None = None) -> str:
    """`N분 전`·`N시간 전`·`N일 전`. 시각을 읽지 못하면 빈 값이다. 미래 시각은 `방금` 으로 본다."""
    moment = _moment(value)
    if moment is None:
        return ""
    seconds = int(((now or datetime.now(timezone.utc)) - moment).total_seconds())
    if seconds < 60:
        return "방금"
    if seconds < 3600:
        return f"{seconds // 60}분 전"
    if seconds < 86400:
        return f"{seconds // 3600}시간 전"
    return f"{seconds // 86400}일 전"


def card(finding: dict, freshness: dict | None = None, trigger: list[dict] | None = None, now: datetime | None = None) -> dict:
    """결과 카드. 결론 단계, 두 줄 요약, QA 할 일 (REQ-QAINTEL-033). 세부 표시는 상세 화면의 `자세히` 에 있다.

    `freshness`·`trigger` 는 목록이 한꺼번에 읽어 넘긴다(REQ-QAINTEL-046). 없으면 `unknown`·빈 목록이다.
    """
    kind = finding.get("analysis_type", "")
    name = level(finding)
    return {
        "id": finding["id"],
        "subject": finding["subject"],
        "title": finding.get("subject_title", ""),
        "kind": ANALYSIS_TYPE_LABELS.get(kind, kind) if kind else LEGACY_KIND,
        "analysis_type": kind,
        "legacy": not kind,
        "is_spec": kind in SRS_SUBJECT_KINDS,
        "verdict": finding["verdict"],
        "verdict_label": label(finding["verdict"]),
        "level": name,
        "level_class": LEVEL_CLASSES[name],
        "summary": finding.get("summary", ""),
        "todos": todos(finding),
        "badges": badges(finding),
        "created": kst(finding.get("created_at")),
        "issue_updated": kst(finding.get('issue_updated_at')) if finding.get('issue_updated_at') else '미확인',
        "issue_updated_ago": ago(finding.get("issue_updated_at"), now),
        "confidence": finding.get("confidence", ""),
        "reporter": finding.get('reporter_name') or (f"이름 미확인 ({finding['reporter_id']})" if finding.get('reporter_id') else '이슈 작성자 정보 없음'),
        "reporter_id": finding.get("reporter_id", ""),
        "reporter_name": finding.get("reporter_name", ""),
        "processing_state": finding.get('processing_state', 'open'),
        "freshness": freshness or dict(NO_FRESHNESS),
        "trigger": trigger or [],
        "history_count": int(finding.get("history_count") or 1),
    }


#: 이슈가 아니라 사양 번호가 대상인 분석. 이슈 원문·댓글 구획을 두지 않는다 (REQ-QAINTEL-020).
SRS_SUBJECT_KINDS = ("SPEC_COVERAGE", "TC_CHECK", "SRS_REMOVED")
#: 신규 이슈의 판정 이름. 코드 값은 `qa-new-issue-analysis/SKILL.md` 의 출력 형식이다 (REQ-QAINTEL-020).
VALIDITY_LABEL = {
    "SPEC_VIOLATION": "이슈로 보임",
    "CONSISTENT_WITH_SPEC": "사양상 정상, 이슈 아닐 수 있음",
    "SPEC_UNDEFINED": "사양 미정의, 기획 확인 필요",
    "SPEC_AMBIGUOUS": "사양이 모호함, 기획 확인 필요",
    "INSUFFICIENT_EVIDENCE": "근거 부족, 판단 보류",
}
DUPLICATE_VERDICTS = ("STRONG_DUPLICATE", "POSSIBLE_DUPLICATE")
DUPLICATE_FLAGS = ("HISTORICAL_SPEC_DUPLICATE", "HISTORICAL_DUPLICATE_REREGISTERED")
#: 화면 질문 (REQ-QAINTEL-020 분석 종류별 AI 판단).
KIND_QUESTIONS = {
    "NEW_ISSUE": "사양으로 보면 이슈가 맞는가",
    "FIXED_ISSUE": "원인 설명과 수정이 사양에 맞는가",
    "TC_DRAFT": "원인 설명과 수정이 사양에 맞는가",
    "COMMENT": "새 댓글이 QA 판단을 바꾸는가",
    "SPEC_DECISION": "연구소 설명이 사양으로 뒷받침되는가",
    "SPEC_COVERAGE": "바뀐 사양을 TC가 덮는가",
    "TC_CHECK": "바뀐 사양을 TC가 덮는가",
}
FOLD_LINES = 8
VERDICT_NOT_SAVED = "판정 저장 안 됨"
COMMENT_BODY_MISSING = "본문 미수집"
PENDING_EVENT_STATUSES = ("pending", "failed")


def _moment(value) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def freshness(finding: dict, issue, events: list[dict]) -> dict:
    """분석 뒤에 이슈가 바뀌었는가 (Contract: FreshnessState, REQ-QAINTEL-020 2번).

    분석 시각 뒤에 생긴 같은 이슈의 대기 이벤트가 있거나 이슈의 최근 업데이트가 분석 시각보다 늦으면 `changed_after` 다.
    화면 표시만 하며 AI 를 부르지 않는다. 분석 시각이나 비교할 자료가 없으면 `unknown` 이다.
    """
    analyzed = _moment(finding.get("created_at"))
    subject = finding.get("subject")
    own = set(finding.get("event_ids") or [])
    later = []
    if analyzed:
        for event in events or []:
            created = _moment(event.get("created_at"))
            if (event.get("entity_id", subject) == subject and event.get("analysis_status") in PENDING_EVENT_STATUSES
                    and event.get("id") not in own and created and created > analyzed):
                later.append(event)
    updated = _moment(issue.updated) if issue is not None else None
    if later:
        latest = max(later, key=lambda event: _moment(event["created_at"]))["created_at"]
    else:
        latest = ""
    latest_update = (issue.updated if issue is not None and issue.updated else latest) or ""
    if not analyzed or (issue is None and not events):
        return {"state": "unknown", "after_events": 0, "latest_update": latest_update}
    changed = bool(later) or bool(updated and updated > analyzed)
    return {"state": "changed_after" if changed else "current", "after_events": len(later), "latest_update": latest_update}


def trigger_line(events: list[dict]) -> list[dict]:
    """`왜 분석했나` 한 줄. 같은 시각·같은 이름은 한 번만 적고 시간순으로 놓는다 (REQ-QAINTEL-020 3번)."""
    found: dict[tuple[str, str], datetime | None] = {}
    for event in events or []:
        kind = event.get("event_type", "")
        after = event.get("after") or {}
        if kind == ISSUE_COMMENT_ADDED and after.get("comments"):
            significant = after.get("significant_ids") or []
            entries = [(comment.get("created") or event.get("created_at"), "새 댓글") for comment in after["comments"]
                       if not significant or str(comment.get("id") or "") in significant]
        else:
            entries = [(event.get("created_at"), "신규 이슈 등록" if kind == ISSUE_CREATED else EVENT_LABELS.get(kind, kind))]
        for created, name in entries:
            found.setdefault((kst(created), name), _moment(created))
    ordered = sorted(found.items(), key=lambda item: (item[1] is None, item[1] or datetime.min.replace(tzinfo=timezone.utc)))
    return [{"time": time, "label": name} for (time, name), _ in ordered]


def person(name: str = "", person_id: str = "") -> str:
    """작성자 표시. 이름이 없으면 `이름 미확인 (ID)`, ID도 없으면 빈 값이다. 화면 표시용이며 AI 입력에는 쓰지 않는다."""
    if name:
        return name
    return f"이름 미확인 ({person_id})" if person_id else ""


def fold(text: str, limit: int = FOLD_LINES) -> dict:
    """긴 글은 `limit` 줄까지 보이고 나머지는 접는다. 접은 부분도 HTML 에 있어 자바스크립트 없이 읽힌다."""
    lines = str(text or "").strip("\n").splitlines()
    return {"head": "\n".join(lines[:limit]), "rest": "\n".join(lines[limit:]), "extra": max(0, len(lines) - limit)}


def workitem_url(polarion, item_id: str) -> str:
    host = (polarion.host if polarion else "").rstrip("/")
    parsed = urlparse(host)
    if parsed.scheme in ("http", "https") and parsed.netloc and not parsed.username and polarion.project_id:
        return f"{host}/polarion/#/project/{quote(polarion.project_id, safe='')}/workitem?id={quote(item_id, safe='')}"
    return ""


def issue_view(issue, finding: dict, card_info: dict, explanation: dict, polarion=None) -> dict:
    """머리·이슈 본문 구획에 보일 값 (REQ-QAINTEL-020 1·4번)."""
    kind = finding.get("analysis_type", "")
    if kind in SRS_SUBJECT_KINDS:
        return {"srs": True, "text": explanation["issue"], "url": workitem_url(polarion, finding["subject"])}
    fields = []
    if issue is not None:
        fields = [{"name": name, "text": fold(text)} for name, text in
                  (("설명", issue.description), ("재현 절차", issue.reproduction_step), ("발생 원인", issue.occurrence_cause),
                   ("조치 내용", issue.action_details)) if str(text or "").strip()]
    return {
        "srs": False, "available": issue is not None, "fields": fields,
        "occurred": ", ".join(issue.occurred_versions) if issue is not None else "",
        "target": ", ".join(issue.target_versions) if issue is not None else "",
        "author": (issue.reporter_name if issue is not None and issue.reporter_name else card_info["reporter"]),
        "created": kst(issue.created) if issue is not None else "",
        "updated": (kst(issue.updated) if issue is not None and issue.updated else card_info["issue_updated"]),
        "status": issue.status if issue is not None else "", "rd_result": issue.rd_result if issue is not None else "",
        "url": workitem_url(polarion, finding["subject"]),
    }


def verdict_view(finding: dict, view: str) -> dict:
    """분석 종류별 AI 판단 구획의 값 (REQ-QAINTEL-020 5번). 판정 값은 이름만 바꿔 보인다."""
    kind = finding.get("analysis_type", "")
    sections = finding.get("sections") or {}
    verdict = finding.get("verdict", "")
    out = {"question": KIND_QUESTIONS.get(kind, ""), "legacy": not kind, "verdict": label(verdict), "needs_review": False,
           "marks": [], "lines": [], "note": ""}
    if not kind:
        out["verdict"] = ""
        return out
    if kind == NEW_ISSUE:
        if not verdict:
            pass
        elif verdict in VALIDITY_LABEL:
            out["verdict"] = VALIDITY_LABEL[verdict]
        else:
            out["verdict"], out["needs_review"] = verdict, True
        duplicate = (sections.get("duplicate") or {}).get("verdict")
        flags = (sections.get("historical") or {}).get("flags") or []
        if duplicate in DUPLICATE_VERDICTS or any(flag in DUPLICATE_FLAGS for flag in flags):
            out["marks"].append("중복 의심")
    elif kind in (FIXED_ISSUE, "TC_DRAFT"):
        out["lines"] = [text for text in ((sections.get("root_cause_review") or {}).get("summary"),
                                          (sections.get("resolution_review") or {}).get("summary")) if text]
        axes = (sections.get("regression_risk") or {}).get("axes") or []
        if axes:
            out["marks"].append(f"회귀 위험 축 해당 {sum(axis.get('applicable') is True for axis in axes)}개")
    elif kind == COMMENT:
        entries = [entry for entry in sections.get("comments") or [] if isinstance(entry, dict)]
        out["marks"] = list(dict.fromkeys(label(entry["classification"]) for entry in entries if entry.get("classification")))
        out["lines"] = [entry["impact"] for entry in entries if entry.get("impact")]
    elif kind in (SPEC_COVERAGE, "TC_CHECK"):
        if view == "SPEC_SUMMARY" and not sections.get("tc_coverage"):
            out["note"] = "TC 비교는 하지 않은 분석입니다. [기존 TC 검토]로 요청할 수 있습니다."
        tcs = (sections.get("checklist_coverage") or {}).get("tcs")
        if tcs:
            out["marks"].append(f"Checklist {len(tcs)}건")
    if not verdict:
        # 판정을 지어내지 않는다 (REQ-QAINTEL-020 읽는 규칙).
        out["verdict"], out["needs_review"] = VERDICT_NOT_SAVED, False
    return out


def finding_explanation(finding: dict, events: list[dict], polarion=None, issue=None, names: dict | None = None) -> dict:
    """저장된 기록만 풀어 쓴다. 옛 결과의 확인 범위·작성자를 추측하지 않는다.

    `issue` 가 있으면 댓글은 이슈 원문의 전체 댓글을 시간순으로 보인다. 없으면 변경 이벤트에 저장된 댓글만 보인다.
    """
    names = names or {}
    sections = finding.get("sections") or {}
    interpretations = {str(entry.get("comment_id")): entry for entry in sections.get("comments") or []
                       if isinstance(entry, dict)}
    comments, seen, originals = [], set(), {}
    for event in events:
        after = event.get("after") or {}
        for comment in after.get("comments") or []:
            comment_id = str(comment.get("id") or "")
            significant = after.get("significant_ids") or []
            if comment_id in seen or (significant and comment_id not in significant):
                continue
            seen.add(comment_id)
            interpretation = interpretations.get(comment_id, {})
            author_id = str(comment.get("author") or "")
            originals[comment_id] = {"created": str(comment.get("created") or ""), "text": str(comment.get("text") or ""),
                                     "author": person(names.get(author_id, ""), author_id)}
            comments.append({"id": comment_id, "created": kst(comment.get("created")),
                             "author": person(names.get(author_id, ""), author_id) or "작성자 정보 없음", "text": comment.get("text") or "",
                             "summary": interpretation.get("summary") or "해석이 저장되지 않았습니다.",
                             "classification": interpretation.get("classification") or "",
                             "impact": interpretation.get("impact") or "", "follow_up": interpretation.get("follow_up") or ""})
    for comment_id, entry in interpretations.items():
        if comment_id not in seen:
            comments.append({"id": comment_id, "created": "", "author": "작성자 정보 없음", "text": "원문이 저장되지 않았습니다.",
                             "summary": entry.get("summary") or "", "classification": entry.get("classification") or "",
                             "impact": entry.get("impact") or "", "follow_up": entry.get("follow_up") or ""})
    checked = sections.get("checked")
    checked = checked if isinstance(checked, dict) else {}
    lists = {key: [text for text in checked.get(key, []) if isinstance(text, str) and text.strip()]
             if isinstance(checked.get(key), list) else [] for key in ("confirmed", "unconfirmed")}
    locations = {comment["id"]: f"댓글 {number}" + (f" ({comment['created']})" if comment["created"] else "")
                 for number, comment in enumerate(comments, start=1)}
    mentioned = re.findall(r"(?<![A-Za-z0-9-])([A-Z]{2,}-[0-9]+)(?![A-Za-z0-9-])",
                           "\n".join([finding.get("detail") or "", *(c["text"] for c in comments)]))
    ids = list(dict.fromkeys([*((finding.get("related_ids") or {}).get("srs") or []), *mentioned]))
    links = [{"id": value, "url": url} for value in ids if value != finding["subject"]
             for url in [workitem_url(polarion, value)] if url]
    kind = finding.get("analysis_type", "")
    rows = comment_rows(finding, issue, originals, seen, interpretations)
    return {"issue": sections.get("issue_summary") or finding.get("subject_title") or finding["subject"],
            "comments": comments, "comment_rows": rows, **lists, "has_checked": any(lists.values()),
            "comment_locations": locations, "srs_links": links,
            "memo": finding.get("detail") or "분석 메모가 저장되지 않았습니다.",
            "events": list(dict.fromkeys(EVENT_LABELS.get(event.get("event_type"), event.get("event_type", "")) for event in events)),
            "interprets_comments": kind == COMMENT}


def comment_rows(finding: dict, issue, originals: dict, used_ids: set, interpretations: dict) -> list[dict]:
    """댓글 구획의 줄 (REQ-QAINTEL-020 7번).

    이슈 스냅샷의 댓글을 바탕으로 하되, 수집기가 번호만 남긴 빈 댓글(`created`·`text`가 비어 있음)은 변경 이벤트에
    저장된 원문(`originals`, 번호별)으로 시각·본문·작성자를 채운다. 저장된 원문도 없으면 `본문 미수집`이다.
    스냅샷에 없는 이벤트 댓글과 해석만 저장된 댓글도 줄로 남긴다.
    줄 순서: 시각을 아는 댓글은 시간순, 시각을 모르는 댓글은 그 뒤에 스냅샷 순서 → 이벤트 순서 → 해석만 있는 순서로 놓는다.
    """
    analyzed = _moment(finding.get("created_at"))
    merged: dict[str, dict] = {}
    for entry in (issue.comments if issue is not None else []):
        merged[entry["id"]] = {"created": entry["created"], "text": entry["text"], "author": entry["author_name"]}
    for comment_id, original in originals.items():
        current = merged.setdefault(comment_id, {"created": "", "text": "", "author": ""})
        for key in ("created", "text", "author"):
            current[key] = current[key] or original[key]
    for comment_id in interpretations:
        merged.setdefault(comment_id, {"created": "", "text": "", "author": ""})
    rows = []
    for comment_id, entry in merged.items():
        interpretation = interpretations.get(comment_id) or {}
        moment = _moment(entry["created"])
        used = comment_id in used_ids or comment_id in interpretations
        rows.append({"id": comment_id, "created": kst(entry["created"]), "moment": moment,
                     "author": entry["author"] or "작성자 정보 없음", "text": entry["text"],
                     "body_missing": not entry["text"], "used": used, "interpreted": bool(interpretation),
                     "after": bool(not used and analyzed and moment and moment > analyzed),
                     "summary": interpretation.get("summary") or "", "classification": interpretation.get("classification") or "",
                     "impact": interpretation.get("impact") or "", "follow_up": interpretation.get("follow_up") or ""})
    rows.sort(key=lambda row: (row["moment"] is None, row["moment"] or datetime.min.replace(tzinfo=timezone.utc)))
    return rows


def sort_cards(cards: list[dict]) -> list[dict]:
    """결론 단계 순서, 같은 단계는 최신이 먼저."""
    return sorted(cards, key=lambda item: (LEVEL_ORDER.index(item["level"]), -item["id"]))


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


def next_run_text(cfg: DailyQaSettings, now: datetime | None = None, store=None) -> str:
    from app.modules.daily_qa import automation_policy
    from app.modules.daily_qa.store import DailyQaStore
    sched = schedule_settings()
    if not sched.get("enabled", True):
        return "예약 꺼짐 (이 서버는 자동 실행하지 않음)"
    store = store or DailyQaStore(cfg.db_path)
    if automation_policy.get_policy(store, cfg.slug)["mode"] == "off":
        return "자동 점검 중지"
    moment = automation_policy.next_run(cfg, store, sched, now or datetime.now(timezone.utc))
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
    running = is_running(cfg)
    cancel_requested = bool(last and last.get("cancel_requested_at") and last["status"] == "RUNNING")
    if last and last["status"] == "RUNNING" and not running:
        try:
            started = datetime.fromisoformat(last["started_at"])
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            if (datetime.now(timezone.utc) - started).total_seconds() >= 30:
                store.interrupt_run(last["id"])
                last = store.get_run(last["id"])
        except (ValueError, TypeError):
            pass
    progress = None
    if last:
        end = last.get("finished_at") if last["status"] != "RUNNING" else None
        try:
            started = datetime.fromisoformat(last["started_at"])
            elapsed = (datetime.fromisoformat(end) if end else datetime.now(timezone.utc)) - started
            seconds = max(0, int(elapsed.total_seconds()))
        except (ValueError, TypeError):
            seconds = 0
        progress = {**last.get("progress", {}), "run_id": last["id"], "started_at": last["started_at"],
                    "elapsed_seconds": seconds, "url": f"/qa-agent/runs/{last['id']}"}
        if running and last["status"] != "RUNNING":
            progress = {"phase": "preflight", "label": "준비", "elapsed_seconds": 0, "done": 0, "total": 0}
        if cancel_requested:
            progress.update({"cancel_requested": True, "label": "중단 요청 확인 중"})
    return {
        "product": cfg.product,
        "slug": cfg.slug,
        "running": running,
        "current_run_id": last["id"] if running and last and last["status"] == "RUNNING" else None,
        "cancel_requested": cancel_requested,
        "progress": progress,
        "last_run": {"id": last["id"], "status": last["status"], "status_label": last["status_label"],
                     "started": kst(last["started_at"]), "finished": kst(last.get("finished_at")),
                     # 대상 준비를 마친 실행이면 진행 표시가 `상세 분석 열기`를 보인다 (REQ-QAINTEL-042).
                     "prepared_plan": bool(last.get("summary", {}).get("collection_only") and last["summary"].get("plan_id")
                                           and last["status"] != "RUNNING"),
                     "result": " · ".join(filter(None, (last["status_label"],
                         "메일 발송 실패" if last.get("email_status") == "failed" else "",
                         claude_limits.KIND_LABELS.get(last["summary"]["claude_limit"].get("kind"), "AI 분석 중단")
                         if last.get("summary", {}).get("claude_limit") else "")))} if last else None,
        "next_run": next_run_text(cfg, store=store),
        "claude_limit": limit_info(choice),
        # 앞선 현재 상태 점검이 한도로 남긴 대상 (REQ-QAINTEL-030 순서 5).
        "issue_audit_remaining": len(store.list_events(product=cfg.slug, statuses=("pending", "failed"),
                                                       event_types=("ISSUE_AUDIT_TARGET",))),
    }


def prepared_plan(choice: ProductChoice) -> dict | None:
    """가장 최근에 준비한 대상 목록(화면용). 없으면 None. AI·외부 서비스를 부르지 않는다 (REQ-QAINTEL-042)."""
    from app.modules.daily_qa import execution_plan
    storage = choice.store.storage
    try:
        plan = storage.qa_get_plan(product=choice.cfg.slug)
        if not plan:
            return None
        reporters = storage.qa_get_reporters(choice.cfg.slug, [target.get('subject') for target in plan['targets']])
        return execution_plan.public(plan, reporters=reporters)
    except (ValueError, KeyError, TypeError, AttributeError):   # 깨진 목록 한 줄이 첫 화면을 막지 않는다
        logger.warning("qa_prepared_plan_unreadable product=%s", choice.cfg.slug)
        return None


def dashboard(choice: ProductChoice, sort: str = 'updated', filters: dict | None = None) -> dict:
    from app.modules.daily_qa import automation_policy
    from app.modules.daily_qa import workflow_settings
    cfg, store = choice.cfg, choice.store
    runs = store.list_runs(limit=10, product=cfg.slug, include_legacy=cfg.is_legacy_product)
    last = runs[0] if runs else None
    events = store.list_events(product=cfg.slug, run_id=last["id"]) if last else []
    from app.modules.qa_agent import result_views
    # 분석결과 구역은 모든 제품에서 결과 페이지와 같은 조회다. 맨 위 한 쪽만 보인다 (REQ-QAINTEL-019·046).
    listing = result_views.search(choice, {**(filters or {}), 'sort': sort, 'page': 1}, path='/qa-agent')
    return {
        "listing": listing,
        "states": result_views.STATES, "level_help": result_views.LEVEL_HELP,
        "status": status(choice),
        "result_sort": sort,
        "last_run": last,
        "runs": runs,
        "summary": change_summary(events),
        "cards": listing['cards'],
        "knowledge": knowledge_uploads(cfg.product, cfg.root, _documents_storage(cfg)),
        "event_labels": EVENT_LABELS,
        "run_period": run_period_defaults(min_day=oldest_snapshot_day(cfg)),
        "automation_policy": automation_policy.get_policy(store, cfg.slug),
        "deferred": deferred_summary(choice),
        "workflow": workflow_settings.for_choice(choice) if cfg.savings_enabled else {'enabled': False},
        # 점검 화면에는 건수와 링크만 둔다. 후보 목록은 `상세 분석` 화면에 있다 (REQ-QAINTEL-042).
        "prepared": prepared_plan(choice) if cfg.savings_enabled else None,
        "automation_history": [{**item, "changed_at": item.get("updated_at", ""),
                                "label": automation_policy.LABELS["off" if not item.get("enabled", True)
                                                                 else "daily" if item.get("validation_period", True) else "weekly"]}
                               for item in automation_policy.history(store, cfg.slug)[:10]],
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


def deferred_summary(choice: ProductChoice, since: str = "", until: str = "") -> dict:
    """밀린 변경(자동 분석이 꺼진 동안 저장된 변경)의 건수·기간·분석 종류별 수 (REQ-ALMCHG-005 3·4번).

    `since`·`until` 은 한국 날짜이고 둘 다 비워 둘 수 있다(비우면 전체). 잘못된 날짜는 PeriodError 다.
    """
    try:
        start = date.fromisoformat(since) if since else None
        end = date.fromisoformat(until) if until else None
    except ValueError as exc:
        raise PeriodError("날짜는 YYYY-MM-DD 로 입력하세요.") from exc
    if start and end and start > end:
        raise PeriodError("시작일이 종료일보다 늦습니다.")
    lower = _utc_bounds(start, start)[0] if start else None
    upper = _utc_bounds(end, end)[1] if end else None
    events = choice.store.list_events(product=choice.cfg.slug, statuses=("deferred",), since=lower, until=upper)
    days = sorted(kst(event["created_at"])[:10] for event in events)
    by_analysis: dict[str, int] = {}
    for event in events:
        for analysis in event.get("remaining_analyses") or event.get("analyses") or []:
            by_analysis[analysis] = by_analysis.get(analysis, 0) + 1
    return {"count": len(events), "first": days[0] if days else "", "last": days[-1] if days else "",
            "by_analysis": by_analysis}


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
