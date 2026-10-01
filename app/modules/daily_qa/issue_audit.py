"""이슈 기록이 없는 기간의 현재 상태 기준 점검 (SPEC REQ-QAINTEL-030).

기간 동안 이슈가 어떻게 바뀌었는지는 알 수 없다(이슈 스냅샷이 없다). 대신 지금 이슈 전체를 기간의 SRS
변화와 맞춰 본다. 무엇을 AI 에 보낼지는 여기서 코드로 정한다.

토큰 절약 (사용자 결정 2026-10-01):

1. 코드가 먼저 나눈다. 연결 SRS 도 기간 안 생성도 없는 이슈는 AI 에 보내지 않는다.
2. SRS 하나와 그 SRS 의 이슈 요약 카드를 한 작업에 넣는다. SRS 내용은 작업마다 한 번만 넣는다.
3. SRS 는 바뀐 문장과 이슈에 가까운 문단 몇 개만 넣는다.
4. 같은 이슈·같은 상태·같은 SRS 변화는 이벤트 지문이 같아 다시 분석하지 않는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from app.modules.daily_qa.change_events import ENTITY_ISSUE, ISSUE_AUDIT, ISSUE_AUDIT_TARGET, ChangeEvent
from app.modules.daily_qa.packages import Task, _chunks
from app.modules.daily_qa.product_adapter import RD_FIXED, RD_SPEC_LIKE
from app.modules.daily_qa.srs_snapshot import SrsDiff
from app.parsers.polarion_issue import WORK_ITEM_ID_RE

CAT_A_FIXED = "A-수정"
CAT_A_SPEC = "A-사양"
CAT_A_OTHER = "A-기타"
CAT_B = "B"
CAT_D = "D"
CATEGORIES = (CAT_A_FIXED, CAT_A_SPEC, CAT_A_OTHER, CAT_B, CAT_D)
CATEGORY_LABELS = {
    CAT_A_FIXED: "연결 SRS 변경 · 수정 완료",
    CAT_A_SPEC: "연결 SRS 변경 · Spec 판정",
    CAT_A_OTHER: "연결 SRS 변경 · 그 밖",
    CAT_B: "SRS 그대로 · Spec 판정",
    CAT_D: "기간 안 신규 이슈",
}
#: 판정 기준으로 쓰는 이슈 상태. 나머지는 참고만 한다 (지침 §6).
JUDGED_STATUSES = frozenset({"verified", "closed", "done", "resolved", "reviewed"})
SIGNAL_SRS_STALE = "판정 뒤 SRS 가 바뀌지 않음"
NOTICE = ("이 기간에는 이슈 변경 기록이 없어 이슈 변화를 현재 상태 기준 점검으로 대신했습니다. "
          "기간 중 상태 변화와 중간 댓글은 알 수 없습니다.")
BASIS_TAG = "현재 상태 기준(기간 이력 없음)"
TASK_PREFIX = "AUD"
SKILL = "qa-issue-spec-audit"

_SENTENCES = 8
_SENTENCE_CHARS = 300
_CARD_CHARS = 400
_EXCERPTS = 3
_EXCERPT_CHARS = 700
_TC_CANDIDATES = 3


def _cut(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _day(value: str) -> str:
    return str(value or "")[:10]


def linked_srs(issue: dict, srs_ids: set[str]) -> list[str]:
    """이슈에 연결된 SRS. 연결 항목과, 본문에 정확히 적힌 SRS 번호만 쓴다. 제목이 비슷하다고 잇지 않는다."""
    found: list[str] = []
    for value in issue.get("linked_ids") or []:
        if value in srs_ids and value not in found:
            found.append(value)
    text = " ".join(str(issue.get(name) or "") for name in ("title", "description", "reproduction_step"))
    for value in WORK_ITEM_ID_RE.findall(text):
        if value in srs_ids and value not in found and value != issue.get("id"):
            found.append(value)
    return found


def _srs_change(diff_item: dict | None, srs: dict, created: bool = False) -> dict:
    info = {"id": srs["id"], "title": srs.get("title", ""), "changed": diff_item is not None or created}
    if diff_item is not None:
        info["fields"] = list(diff_item.get("fields") or [])
        info["added_sentences"] = [_cut(text, _SENTENCE_CHARS) for text in (diff_item.get("added_sentences") or [])[:_SENTENCES]]
        info["removed_sentences"] = [_cut(text, _SENTENCE_CHARS) for text in (diff_item.get("removed_sentences") or [])[:_SENTENCES]]
    elif created:
        info["fields"] = ["new"]
    return info


@dataclass
class AuditPlan:
    events: list[ChangeEvent] = field(default_factory=list)
    issues_seen: int = 0
    created_in_period: int = 0
    updated_in_period: int = 0
    by_category: dict[str, int] = field(default_factory=dict)
    not_target: int = 0
    reference_only: int = 0
    changed_srs: int = 0

    def summary(self, since: date, until: date) -> dict:
        return {
            "since": since.isoformat(), "until": until.isoformat(), "notice": NOTICE,
            "issues_seen": self.issues_seen, "created_in_period": self.created_in_period,
            "updated_in_period": self.updated_in_period, "changed_srs": self.changed_srs,
            "by_category": {CATEGORY_LABELS[key]: self.by_category.get(key, 0) for key in CATEGORIES},
            "targets": len(self.events), "not_target": self.not_target, "reference_only": self.reference_only,
        }

    def note(self) -> str:
        parts = ", ".join(f"{CATEGORY_LABELS[key]} {self.by_category.get(key, 0)}" for key in CATEGORIES)
        return (f"{NOTICE} 이슈 전체 {self.issues_seen}건 중 기간 안 생성 {self.created_in_period}건, 수정 {self.updated_in_period}건. "
                f"점검 대상 {len(self.events)}건({parts}), 대상 아님 {self.not_target}건.")


def classify(issues: list[dict], srs_items: list[dict], diff: SrsDiff, since: date, until: date) -> AuditPlan:
    """이슈 전체를 묶음으로 나누고 점검 이벤트를 만든다. AI 를 쓰지 않는다."""
    srs_by_id = {item["id"]: item for item in srs_items}
    srs_ids = set(srs_by_id)
    modified = {item["id"]: item for item in diff.modified}
    created_srs = {item["id"] for item in diff.added}
    changed = set(modified) | created_srs
    start, end = since.isoformat(), until.isoformat()
    plan = AuditPlan(issues_seen=len(issues), changed_srs=len(changed))
    for issue in issues:
        created_in = start <= _day(issue.get("created")) <= end
        updated_in = start <= _day(issue.get("updated")) <= end
        plan.created_in_period += created_in
        plan.updated_in_period += updated_in
        links = linked_srs(issue, srs_ids)
        changed_links = [value for value in links if value in changed]
        rd = issue.get("rd_result", "")
        signals: list[str] = []
        if changed_links:
            category = CAT_A_FIXED if rd == RD_FIXED else CAT_A_SPEC if rd in RD_SPEC_LIKE else CAT_A_OTHER
            used = changed_links
        elif links and rd in RD_SPEC_LIKE:
            category, used = CAT_B, links
            if any(_day(srs_by_id[value].get("updated")) and _day(srs_by_id[value].get("updated")) < _day(issue.get("updated"))
                   for value in links):
                signals.append(SIGNAL_SRS_STALE)
        elif created_in:
            category, used = CAT_D, links
        else:
            plan.not_target += 1
            continue
        reference_only = str(issue.get("status") or "").lower() not in JUDGED_STATUSES
        plan.reference_only += reference_only
        if updated_in:
            signals.append("기간 안에 수정됨")
        plan.by_category[category] = plan.by_category.get(category, 0) + 1
        after = {
            "category": category,
            "srs": [_srs_change(modified.get(value), srs_by_id[value], value in created_srs) for value in used],
            "signals": signals,
            "issue_status": issue.get("status", ""),
            "rd_result": rd,
            "issue_updated": issue.get("updated", ""),
            "reference_only": reference_only,
        }
        plan.events.append(ChangeEvent(ENTITY_ISSUE, issue["id"], ISSUE_AUDIT_TARGET, {}, after, ["category"], True,
                                       f"{CATEGORY_LABELS[category]} · {BASIS_TAG}", [ISSUE_AUDIT]))
    return plan


# -- AI 입력 ---------------------------------------------------------------------


def issue_card(issue: dict, reference_only: bool) -> dict:
    """AI 에 보내는 이슈 요약 카드. 댓글 전체·첨부는 넣지 않는다."""
    from app.modules.daily_qa.intelligence import issue_sections

    sections = issue_sections(issue)
    comments = [comment for comment in issue.get("comments") or [] if len(str(comment.get("text") or "").strip()) >= 15][-2:]
    return {
        "id": issue["id"], "title": issue.get("title", ""), "status": issue.get("status", ""),
        "rd_result": issue.get("rd_result", ""), "created": issue.get("created", ""), "updated": issue.get("updated", ""),
        "expected": _cut(" ".join(sections["expected"]), _CARD_CHARS), "actual": _cut(" ".join(sections["actual"]), _CARD_CHARS),
        "occurrence_cause": _cut(issue.get("occurrence_cause", ""), 300), "action_details": _cut(issue.get("action_details", ""), 300),
        "last_comments": [{"id": comment.get("id", ""), "text": _cut(comment.get("text", ""), 300)} for comment in comments],
        "reference_only": reference_only,
    }


def build_item(target, corpus, known) -> dict | None:
    """점검 대상 이슈 하나의 입력. 대상이 오늘 스냅샷에 없으면 None."""
    issue = corpus.issue_by_id.get(target.entity_id)
    if issue is None:
        return None
    after: dict = {}
    for event in target.events:
        after.update(event.get("after") or {})
    srs = list(after.get("srs") or [])
    if not srs:
        # 연결 SRS 가 없는 신규 이슈: 사양 후보를 검색으로 3개까지 찾는다.
        from app.modules.daily_qa.intelligence import extract_terms

        terms = extract_terms(issue.get("title", ""), issue.get("description", ""), issue.get("reproduction_step", ""), exclude=issue["id"])
        found = corpus.spec_candidates(terms, f"{issue.get('title', '')} {issue.get('description', '')}", 3, 0, known)
        srs = [{"id": item["id"], "title": item.get("title", ""), "changed": False, "found_by_search": True} for item in found["srs"]]
    for comment in issue.get("comments") or []:
        if comment.get("id"):
            known.comment_ids.add(comment["id"])
    return {"target": issue["id"], "event_ids": target.event_ids, "category": after.get("category", ""),
            "signals": after.get("signals") or [], "issue": issue_card(issue, bool(after.get("reference_only"))),
            "srs_ids": [entry["id"] for entry in srs], "_srs": srs}


def _tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[0-9A-Za-z가-힣]{2,}", (text or "").lower())}


def _excerpts(srs_text: str, issues: list[dict]) -> list[str]:
    """이슈 내용과 낱말이 가장 많이 겹치는 SRS 문단."""
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n|\n", srs_text or "") if len(part.strip()) >= 10]
    wanted = _tokens(" ".join(f"{item['issue']['title']} {item['issue']['expected']} {item['issue']['actual']}" for item in issues))
    ranked = sorted(paragraphs, key=lambda part: -len(_tokens(part) & wanted))
    return [_cut(part, _EXCERPT_CHARS) for part in ranked[:_EXCERPTS]]


def build_tasks(items: list[dict], corpus, batch_size: int, answers: list[dict] | None = None,
                unreadable: list[str] | None = None) -> list[Task]:
    """SRS 단위로 묶는다. 이슈가 여러 SRS 에 걸리면 첫 SRS 묶음에 넣는다."""
    groups: dict[str, list[dict]] = {}
    for item in items:
        key = item["srs_ids"][0] if item["srs_ids"] else ""
        groups.setdefault(key, []).append(item)
    tasks: list[Task] = []
    number = 0
    for key in sorted(groups):
        for chunk in _chunks(groups[key], batch_size):
            number += 1
            srs_entries: dict[str, dict] = {}
            for item in chunk:
                for entry in item["_srs"]:
                    current = corpus.srs_by_id.get(entry["id"]) or {}
                    if entry["id"] not in srs_entries:
                        srs_entries[entry["id"]] = {**entry, "status": current.get("status", ""), "updated": current.get("updated", ""),
                                                    "old_id": current.get("old_id", ""), "excerpts": []}
            for srs_id, entry in srs_entries.items():
                current = corpus.srs_by_id.get(srs_id) or {}
                entry["excerpts"] = _excerpts(current.get("text", ""), [item for item in chunk if srs_id in item["srs_ids"]])
            tcs: list[dict] = []
            if key and any(item["category"] == CAT_A_FIXED for item in chunk):
                current = corpus.srs_by_id.get(key) or {}
                tcs = corpus.tc_candidates([], current.get("title", ""), _TC_CANDIDATES, srs_ids=(key, current.get("old_id", "")))
            payload_items = [{name: value for name, value in item.items() if name != "_srs"} for item in chunk]
            tasks.append(Task(task_id=f"{TASK_PREFIX}-{number:03d}", skill=SKILL, payload={
                "analysis_type": ISSUE_AUDIT, "basis": BASIS_TAG, "srs": list(srs_entries.values()), "tcs": tcs,
                "items": payload_items, "answered_questions": answers or [], "unreadable_documents": unreadable or []}))
    return tasks
