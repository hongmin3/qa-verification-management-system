"""분석 결과 근거 검증 (SPEC REQ-QAINTEL-017).

모델이 돌려준 이슈·SRS·TC 번호와 근거 위치가 실제 자료(오늘 스냅샷, TC 색인, 작업 입력으로 준
사양서 조각·댓글)에 있는지 코드가 확인한다. QA Agent `validation.py` 의 원칙과 같다.

- 없는 번호는 결과에서 빼고, 뺀 항목과 이유를 `sections.validation` 에 남긴다(숨기지 않는다).
- 판정을 뒷받침해야 하는데 근거가 하나도 남지 않으면 Finding 을 버린다.
- 근거가 없음을 뜻하는 판정은 남기되 신뢰도를 `Review Needed` 보다 높이지 않는다.
- 초안 규칙: 수정 완료(FIXED) 이슈 분석과 Coverage 부족 판정에만 초안을 허용한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.modules.daily_qa.change_events import COMMENT, FIXED_ISSUE, ISSUE_AUDIT, NEW_ISSUE, SPEC_COVERAGE, SPEC_DECISION
from app.modules.daily_qa.intelligence import DEFAULT_AXES, LEGACY_RE, KnownIds
from app.modules.daily_qa.product_adapter import RD_FIXED
from app.modules.daily_qa.schema import DRAFT_PERSPECTIVES, FORBIDDEN_ACTION_RE
from app.parsers.polarion_issue import WORK_ITEM_ID_RE

#: 근거가 없다는 것 자체가 판정인 값. 근거 없이도 남기되 신뢰도를 올리지 않는다.
ABSENCE_VERDICTS = frozenset({
    "SPEC_UNDEFINED", "SPEC_AMBIGUOUS", "INSUFFICIENT_EVIDENCE", "SPEC_NOT_FOUND", "SPEC_REVIEW_REQUIRED", "NO_DUPLICATE_FOUND",
})
#: 초안을 만들면 안 되는 사양 판정 (Expected 를 정할 근거가 없다, REQ-QAINTEL-013·016).
NO_DRAFT_VERDICTS = frozenset({"SPEC_UNDEFINED", "SPEC_AMBIGUOUS", "INSUFFICIENT_EVIDENCE", "FULLY_COVERED", "SPEC_REVIEW_REQUIRED"})
DUPLICATE_VERDICTS = ("STRONG_DUPLICATE", "POSSIBLE_DUPLICATE", "NO_DUPLICATE_FOUND", "INSUFFICIENT_EVIDENCE")
HISTORY_FLAGS = ("POSSIBLE_REGRESSION", "HISTORICAL_SPEC_DUPLICATE", "HISTORICAL_DUPLICATE_REREGISTERED", "HISTORICAL_DECISION_CONFLICT")
COMMENT_CLASSES = ("ROOT_CAUSE_INFORMATION", "RESOLUTION_INFORMATION", "REPRODUCTION_INFORMATION", "SPEC_CLAIM",
                   "REQUIREMENT_INFORMATION", "QA_ACTION_REQUIRED", "OTHER_SIGNIFICANT_INFORMATION", "NOT_SIGNIFICANT")
ISSUE_RELATIONS = ("EXISTING_DEFECT", "PAST_FIXED", "PAST_SPEC", "SAME_FUNCTION_REGRESSION")
TC_DECISIONS = ("KEEP", "UPDATE_EXISTING")
COVERAGE_ALERTS = ("ISSUE_WITHOUT_TC", "TC_EXPECTED_OUTDATED", "FIXED_ISSUE_WITHOUT_REGRESSION_TC", "SPEC_ISSUE_BEHAVIOR_CHANGED")
DRAFT_HOLD = "Checklist TC 생성 보류"
#: 이슈 정합성 점검의 TC 영향 판정 (QA 규칙 §43, REQ-QAINTEL-030).
AUDIT_TC_DECISIONS = ("유지", "경미 수정", "수정 필수", "Issue Link 수정", "신규 TC 필요", "사양 확인 필요")
CONFIDENCE_RANK = {"Unsupported": 0, "Review Needed": 1, "Confirmed": 2}


@dataclass
class ValidationRecord:
    removed: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def drop(self, kind: str, value, reason: str) -> None:
        self.removed.append({"kind": kind, "value": value if isinstance(value, str) else str(value)[:120], "reason": reason})

    def as_dict(self) -> dict:
        return {"removed": self.removed, "notes": self.notes}


def _tokens(text: str) -> set[str]:
    return set(WORK_ITEM_ID_RE.findall(text or "")) | set(LEGACY_RE.findall(text or ""))


def _valid_tc(entry: dict, known: KnownIds) -> bool:
    tc_id = str(entry.get("tc_id") or "").strip()
    location = str(entry.get("location") or "").strip()
    return (bool(tc_id) and tc_id in known.tc_ids) or (bool(location) and location in known.tc_locations)


def _valid_evidence(evidence: dict, known: KnownIds) -> str | None:
    """근거가 실제 자료를 가리키면 None, 아니면 버리는 이유."""
    kind = evidence.get("source_type")
    location = str(evidence.get("location") or "")
    ref = str(evidence.get("ref") or "")
    if kind == "srs":
        return None if _tokens(location + " " + ref) & known.srs_ids else "오늘 SRS 에 없는 번호"
    if kind == "issue":
        return None if set(WORK_ITEM_ID_RE.findall(location + " " + ref)) & known.issue_ids else "오늘 이슈 스냅샷에 없는 번호"
    if kind == "tc":
        if any(item in location for item in known.tc_locations if item) or (_tokens(location) & known.tc_ids) \
                or any(tc_id and tc_id in location for tc_id in known.tc_ids if len(tc_id) >= 4):
            return None
        return "TC 색인에 없는 TC 위치"
    if kind == "spec_doc":
        return None if ref in known.spec_refs else "작업 입력에 없는 사양서 조각"
    if kind == "comment":
        return None if (ref in known.comment_ids or location in known.comment_ids) else "작업 입력에 없는 댓글"
    if kind == "rules":
        return None if "§" in location else "규칙 절 번호(§)가 없는 근거"
    return None if location.strip() else "위치가 없는 근거"


def _issue_list(entries, known: KnownIds, subject: str, record: ValidationRecord, label: str) -> list[dict]:
    kept = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        issue_id = str(entry.get("issue_id") or "").strip()
        if issue_id == subject:
            record.drop(label, issue_id, "대상 이슈 자신")
        elif issue_id not in known.issue_ids:
            record.drop(label, issue_id, "오늘 이슈 스냅샷에 없는 번호")
        else:
            kept.append(entry)
    return kept


def _clean_new_issue(sections: dict, known: KnownIds, subject: str, record: ValidationRecord) -> None:
    duplicate = dict(sections.get("duplicate") or {})
    duplicate["candidates"] = _issue_list(duplicate.get("candidates"), known, subject, record, "duplicate")
    verdict = duplicate.get("verdict")
    if verdict not in DUPLICATE_VERDICTS:
        record.notes.append(f"알 수 없는 중복 판정 '{verdict}' 을 INSUFFICIENT_EVIDENCE 로 바꿨습니다.")
        duplicate["verdict"] = "INSUFFICIENT_EVIDENCE"
    elif verdict in ("STRONG_DUPLICATE", "POSSIBLE_DUPLICATE") and not duplicate["candidates"]:
        record.notes.append(f"{verdict} 인데 확인된 중복 후보가 없어 INSUFFICIENT_EVIDENCE 로 바꿨습니다.")
        duplicate["verdict"] = "INSUFFICIENT_EVIDENCE"
    sections["duplicate"] = duplicate
    _clean_history(sections, known, subject, record)


def _clean_history(sections: dict, known: KnownIds, subject: str, record: ValidationRecord) -> None:
    history = dict(sections.get("historical") or {})
    history["candidates"] = _issue_list(history.get("candidates"), known, subject, record, "historical")
    flags = [flag for flag in history.get("flags") or [] if flag in HISTORY_FLAGS]
    # 표시는 후보로만 붙는다. 뒷받침하는 과거 이슈가 없으면 표시를 남기지 않는다.
    if flags and not history["candidates"]:
        record.notes.append("과거 이슈 표시를 뒷받침하는 후보가 없어 표시를 뺐습니다.")
        flags = []
    history["flags"] = flags
    sections["historical"] = history


def _clean_axes(sections: dict, axes: tuple[str, ...], record: ValidationRecord) -> None:
    risk = dict(sections.get("regression_risk") or {})
    given = {str(entry.get("axis")): entry for entry in risk.get("axes") or [] if isinstance(entry, dict)}
    for extra in sorted(set(given) - set(axes)):
        record.drop("regression_axis", extra, "코드가 정한 축이 아님")
    # 축은 코드가 고정한다. 모델이 판정하지 않은 축은 "QA 확인 필요" 로 채운다.
    risk["axes"] = [
        given.get(axis) or {"axis": axis, "applicable": None, "reason": "QA 확인 필요 (분석이 판정하지 않음)"}
        for axis in axes
    ]
    sections["regression_risk"] = risk


def _clean_tc_entries(entries, known: KnownIds, record: ValidationRecord, label: str, decisions: tuple[str, ...] | None = None) -> list[dict]:
    kept = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        if not _valid_tc(entry, known):
            record.drop(label, entry.get("tc_id") or entry.get("location") or "", "TC 색인에 없는 TC")
            continue
        if decisions and entry.get("decision") not in decisions:
            record.drop(label, entry.get("tc_id") or entry.get("location") or "", f"알 수 없는 조치 '{entry.get('decision')}'")
            continue
        kept.append(entry)
    return kept


def _clean_audit(sections: dict, known: KnownIds, record: ValidationRecord) -> None:
    """TC 영향 판정은 허용 값만, TC 번호는 실제 TC 만 남긴다. 판정 근거가 현재 상태라는 표시를 붙인다."""
    from app.modules.daily_qa.issue_audit import BASIS_TAG

    impact = sections.get("tc_impact")
    if isinstance(impact, dict):
        decision = str(impact.get("decision") or "")
        if decision and decision not in AUDIT_TC_DECISIONS:
            record.drop("tc_impact", decision, "허용되지 않은 TC 영향 판정")
            impact = None
        else:
            tc_ids = [value for value in impact.get("tc_ids") or [] if value in known.tc_ids]
            for value in impact.get("tc_ids") or []:
                if value not in known.tc_ids:
                    record.drop("tc_impact", str(value), "입력에 없는 TC 번호")
            impact = {**impact, "tc_ids": tc_ids}
    sections["tc_impact"] = impact if isinstance(impact, dict) else {}
    sections["basis"] = BASIS_TAG


def validate_finding(finding: dict, analysis_type: str, known: KnownIds, targets: set[str],
                     axes: tuple[str, ...] = ()) -> tuple[dict | None, str]:
    """(검증을 마친 Finding 또는 None, 버린 이유). 이유가 `not_significant` 면 규칙 위반이 아니다."""
    record = ValidationRecord()
    subject = str(finding.get("subject") or "").strip()
    if subject not in targets:
        return None, f"작업 대상이 아닌 번호의 결과입니다: {subject}"
    sections = dict(finding.get("sections") or {})
    verdict = finding.get("verdict", "")

    evidence = []
    for item in finding.get("evidence") or []:
        reason = _valid_evidence(item, known)
        if reason:
            record.drop("evidence", f"{item.get('source_type')}:{item.get('location')}", reason)
        else:
            evidence.append(item)
    finding["evidence"] = evidence

    if analysis_type == NEW_ISSUE:
        _clean_new_issue(sections, known, subject, record)
    elif analysis_type == SPEC_DECISION:
        _clean_history(sections, known, subject, record)
    elif analysis_type == ISSUE_AUDIT:
        _clean_audit(sections, known, record)
    elif analysis_type == FIXED_ISSUE:
        _clean_axes(sections, axes or DEFAULT_AXES, record)
        sections["tc_coverage"] = _clean_tc_entries(sections.get("tc_coverage"), known, record, "tc_coverage")
        _clean_history(sections, known, subject, record)
    elif analysis_type == COMMENT:
        kept = []
        for entry in sections.get("comments") or []:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("comment_id") or "") not in known.comment_ids:
                record.drop("comment", entry.get("comment_id") or "", "작업 입력에 없는 댓글")
            elif entry.get("classification") not in COMMENT_CLASSES:
                record.drop("comment", entry.get("comment_id") or "", f"알 수 없는 분류 '{entry.get('classification')}'")
            else:
                kept.append(entry)
        sections["comments"] = kept
        significant = [entry for entry in kept if entry.get("classification") != "NOT_SIGNIFICANT"]
        if not significant:
            return None, "not_significant"
        verdict = finding["verdict"] = significant[0]["classification"]
    elif analysis_type == SPEC_COVERAGE:
        issue_cov = dict(sections.get("issue_coverage") or {})
        issues = _issue_list(issue_cov.get("issues"), known, subject, record, "issue_coverage")
        issue_cov["issues"] = [entry for entry in issues if entry.get("relation") in ISSUE_RELATIONS] + [
            {**entry, "relation": "EXISTING_DEFECT"} for entry in issues if entry.get("relation") not in ISSUE_RELATIONS]
        issue_cov["has_related"] = bool(issue_cov["issues"])
        sections["issue_coverage"] = issue_cov
        checklist = dict(sections.get("checklist_coverage") or {})
        checklist["tcs"] = _clean_tc_entries(checklist.get("tcs"), known, record, "checklist_coverage", TC_DECISIONS)
        sections["checklist_coverage"] = checklist
        sections["alerts"] = [alert for alert in sections.get("alerts") or []
                              if isinstance(alert, dict) and alert.get("type") in COVERAGE_ALERTS]
        if verdict == "FULLY_COVERED" and not checklist["tcs"]:
            record.notes.append("FULLY_COVERED 인데 확인된 TC 가 없어 판정을 버렸습니다.")
            return None, "FULLY_COVERED 를 뒷받침하는 TC 가 없습니다."

    for text in [finding.get("action") or "", *((sections.get("recommendation") or {}).get("actions") or [])]:
        if FORBIDDEN_ACTION_RE.search(str(text)):
            return None, f"금지된 조치를 담고 있습니다(QA 규칙 §55): {str(text)[:80]}"

    drafts = list(finding.get("draft_tcs") or [])
    if drafts:
        if analysis_type == FIXED_ISSUE and known.issue_rd.get(subject) != RD_FIXED:
            return None, "연구소 결과가 FIXED 가 아닌 이슈에 수정확인 TC 초안을 만들었습니다(REQ-QAINTEL-017)."
        if analysis_type not in (FIXED_ISSUE, SPEC_COVERAGE):
            return None, "이 분석 종류는 TC 초안을 만들 수 없습니다(REQ-QAINTEL-017)."
        if verdict in NO_DRAFT_VERDICTS:
            record.notes.append(f"{verdict} 판정이라 초안 {len(drafts)}건을 뺐습니다({DRAFT_HOLD}).")
            sections.setdefault("tc_hold", {"reason": "Expected Result 를 확정할 최신 사양 근거가 부족함.", "questions": []})
            drafts = []
        for draft in drafts:
            if draft.get("perspective") and draft["perspective"] not in DRAFT_PERSPECTIVES:
                record.notes.append(f"알 수 없는 관점 '{draft['perspective']}' 을 비웠습니다.")
                draft["perspective"] = ""
    finding["draft_tcs"] = drafts

    if not evidence:
        if verdict not in ABSENCE_VERDICTS and analysis_type != COMMENT:
            return None, "판정을 뒷받침하는 근거가 하나도 남지 않았습니다."
    if verdict in ABSENCE_VERDICTS or not evidence:
        if CONFIDENCE_RANK.get(finding.get("confidence", ""), 1) > 1:
            finding["confidence"] = "Review Needed"
            record.notes.append("근거 없음 판정이라 신뢰도를 Review Needed 로 낮췄습니다.")

    sections["validation"] = record.as_dict()
    finding["sections"] = sections
    finding["related_ids"] = related_ids(finding, analysis_type)
    return finding, ""


def related_ids(finding: dict, analysis_type: str) -> dict:
    """이 Finding 이 관련 있다고 본 이슈·SRS·TC·초안 (REQ-QAINTEL-016 13번 감사용)."""
    sections = finding.get("sections") or {}
    issues: list[str] = []
    for key in ("duplicate", "historical"):
        issues += [entry["issue_id"] for entry in (sections.get(key) or {}).get("candidates") or []]
    issues += [entry["issue_id"] for entry in (sections.get("issue_coverage") or {}).get("issues") or []]
    tcs = [entry.get("tc_id") or entry.get("location") for entry in sections.get("tc_coverage") or []]
    tcs += [entry.get("tc_id") or entry.get("location") for entry in (sections.get("checklist_coverage") or {}).get("tcs") or []]
    srs = sorted({token for item in finding.get("evidence") or [] if item.get("source_type") == "srs"
                  for token in _tokens(str(item.get("location")))})
    if analysis_type == SPEC_COVERAGE:
        srs = sorted(set(srs) | {finding["subject"]})
    return {
        "issues": list(dict.fromkeys(issues)),
        "srs": srs,
        "tcs": list(dict.fromkeys(item for item in tcs if item)),
        "drafts": [draft.get("title", "") for draft in finding.get("draft_tcs") or []],
    }


def draft_key(finding: dict) -> str:
    """같은 Finding 인지 가릴 때 쓰는 초안 제목 묶음 (REQ-QAINTEL-016 지킬 것)."""
    return "|".join(sorted(re.sub(r"\s+", " ", draft.get("title", "")).strip() for draft in finding.get("draft_tcs") or []))
