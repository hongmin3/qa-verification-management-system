"""작업 묶음 만들기와 결정적 계산 (SPEC REQ-DAILY-003 · 004 · 005 · 006).

AI 에 무엇을 보낼지는 여기서 코드로 정한다. 후보 TC 선택, 삭제 SRS 참조, 추적 공백처럼
규칙으로 계산되는 것은 AI 를 부르지 않는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.modules.daily_qa.schema import SKILL_B, SKILL_C, SKILL_E, SKILL_F
from app.modules.daily_qa.srs_snapshot import SrsDiff
from app.modules.daily_qa.tc_index import TcRow, by_srs

VP_ID_RE = re.compile(r"^[A-Z]{2,}-\d+$")
INVALID_STATUS_RE = re.compile(r"(delet|obsol|reject|cancel|removed|deprecat)", re.IGNORECASE)


@dataclass
class Task:
    task_id: str
    skill: str
    payload: dict

    @property
    def result_file(self) -> str:
        return f"out/{self.task_id}.json"


@dataclass
class DeterministicFinding:
    skill: str
    subject: str
    subject_title: str
    verdict: str
    summary: str
    evidence: list[dict]
    tc_ref: dict | None = None

    def as_dict(self) -> dict:
        return {
            "subject": self.subject,
            "subject_title": self.subject_title,
            "verdict": self.verdict,
            "summary": self.summary,
            "confidence": "Confirmed",
            "evidence": self.evidence,
            "tc_ref": self.tc_ref,
        }


@dataclass
class TraceGapResult:
    findings: list[DeterministicFinding] = field(default_factory=list)
    legacy_unmatched: int = 0
    srs_seen: int = 0
    srs_examined: int = 0
    tc_rows_seen: int = 0


def _chunks(items: list, size: int) -> list[list]:
    return [items[index:index + size] for index in range(0, len(items), size)]


def _tc_ref(row: TcRow) -> dict:
    return {"workbook": row.workbook, "sheet": row.sheet, "row": row.row, "tc_id": row.tc_id}


def candidates_for(srs: dict, index: dict[str, list[TcRow]], limit: int) -> list[TcRow]:
    """SRS ID 와 Legacy 번호로 TC 를 고른다. 같은 행이 두 번 들어가지 않는다."""
    seen: set[tuple[str, str, int]] = set()
    picked: list[TcRow] = []
    for key in (srs.get("id", ""), srs.get("old_id", "")):
        for row in index.get(key, []) if key else []:
            identity = (row.workbook, row.sheet, row.row)
            if identity in seen:
                continue
            seen.add(identity)
            picked.append(row)
    return picked[:limit]


def removed_srs_findings(diff: SrsDiff, rows: list[TcRow]) -> list[DeterministicFinding]:
    """삭제된 SRS 를 아직 가리키는 TC (REQ-DAILY-003 마지막 문단)."""
    index = by_srs(rows)
    findings: list[DeterministicFinding] = []
    for srs in diff.removed:
        for row in candidates_for(srs, index, limit=10_000):
            findings.append(
                DeterministicFinding(
                    skill=SKILL_B,
                    subject=srs["id"],
                    subject_title=srs.get("title", ""),
                    verdict="수정 필수",
                    summary=f"삭제된 SRS {srs['id']} 를 TC 가 아직 가리킵니다: {row.tc_id or row.title[:40]}",
                    evidence=[
                        {"source_type": "srs", "location": f"{srs['id']} (직전 스냅샷에만 있음)", "summary": srs.get("title", ""), "validity": "Deleted"},
                        {"source_type": "tc", "location": row.location(), "summary": row.title, "validity": "Current"},
                    ],
                    tc_ref=_tc_ref(row),
                )
            )
    return findings


def build_b_tasks(diff: SrsDiff, rows: list[TcRow], batch_size: int, candidate_limit: int, answers: list[dict]) -> list[Task]:
    index = by_srs(rows)
    changes: list[dict] = []
    for srs in diff.added:
        changes.append({"change": "added", "srs": srs, "candidates": [row.as_dict() for row in candidates_for(srs, index, candidate_limit)]})
    for item in diff.modified:
        srs = {"id": item["id"], "old_id": item.get("old_id", ""), "title": item.get("title", "")}
        changes.append(
            {
                "change": "modified",
                "srs": srs,
                "fields": item["fields"],
                "before": item["before"],
                "after": item["after"],
                "candidates": [row.as_dict() for row in candidates_for(srs, index, candidate_limit)],
            }
        )
    return [
        Task(task_id=f"B-{number:03d}", skill=SKILL_B, payload={"changes": chunk, "answered_questions": answers})
        for number, chunk in enumerate(_chunks(changes, batch_size), start=1)
    ]


def build_c_tasks(
    issues: list[dict], srs_by_id: dict[str, dict], rows: list[TcRow], batch_size: int, candidate_limit: int, answers: list[dict]
) -> list[Task]:
    index = by_srs(rows)
    packed: list[dict] = []
    for issue in issues:
        linked_srs = [srs_by_id[item] for item in issue.get("linked_ids", []) if item in srs_by_id]
        candidates: list[dict] = []
        seen: set[tuple] = set()
        for srs in linked_srs:
            for row in candidates_for(srs, index, candidate_limit):
                identity = (row.workbook, row.sheet, row.row)
                if identity not in seen:
                    seen.add(identity)
                    candidates.append(row.as_dict())
        packed.append({"issue": issue, "linked_srs": linked_srs, "candidates": candidates[:candidate_limit]})
    return [
        Task(task_id=f"C-{number:03d}", skill=SKILL_C, payload={"issues": chunk, "answered_questions": answers})
        for number, chunk in enumerate(_chunks(packed, batch_size), start=1)
    ]


def trace_gaps(srs_items: list[dict], rows: list[TcRow]) -> TraceGapResult:
    """추적 공백 (REQ-DAILY-005). AI 없이 계산한다."""
    result = TraceGapResult(srs_seen=len(srs_items), tc_rows_seen=len(rows))
    index = by_srs(rows)
    ids = {item["id"] for item in srs_items}
    old_ids = {item.get("old_id") for item in srs_items if item.get("old_id")}
    for item in srs_items:
        if item.get("is_category") or INVALID_STATUS_RE.search(item.get("status", "")):
            continue
        result.srs_examined += 1
        if item["id"] in index or (item.get("old_id") and item["old_id"] in index):
            continue
        result.findings.append(
            DeterministicFinding(
                skill=SKILL_E,
                subject=item["id"],
                subject_title=item.get("title", ""),
                verdict="TC 없음",
                summary=f"{item['id']} ({item.get('old_id') or 'Legacy 번호 없음'}) 를 가리키는 TC 가 없습니다.",
                evidence=[{"source_type": "srs", "location": f"{item['id']} / oldId {item.get('old_id', '')}", "summary": item.get("title", ""), "validity": "Current"}],
            )
        )
    legacy_unmatched: set[str] = set()
    for ref, tc_rows in index.items():
        if VP_ID_RE.match(ref):
            if ref in ids:
                continue
            for row in tc_rows:
                result.findings.append(
                    DeterministicFinding(
                        skill=SKILL_E,
                        subject=ref,
                        subject_title="",
                        verdict="삭제된 SRS 참조",
                        summary=f"TC 가 현재 Polarion 에 없는 {ref} 를 가리킵니다: {row.tc_id or row.title[:40]}",
                        evidence=[
                            {"source_type": "tc", "location": row.location(), "summary": row.title, "validity": "Current"},
                            {"source_type": "srs", "location": f"{ref} (오늘 스냅샷에 없음)", "summary": "", "validity": "Deleted"},
                        ],
                        tc_ref=_tc_ref(row),
                    )
                )
        elif ref not in old_ids:
            legacy_unmatched.add(ref)
    result.legacy_unmatched = len(legacy_unmatched)
    return result


def build_f_tasks(changed_srs: list[dict], manual_files: list[str], batch_size: int, answers: list[dict]) -> list[Task]:
    if not changed_srs or not manual_files:
        return []
    return [
        Task(
            task_id=f"F-{number:03d}",
            skill=SKILL_F,
            payload={"changed_srs": chunk, "manual_files": manual_files, "answered_questions": answers},
        )
        for number, chunk in enumerate(_chunks(changed_srs, batch_size), start=1)
    ]
