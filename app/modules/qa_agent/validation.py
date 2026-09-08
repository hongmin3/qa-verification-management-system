"""모델이 만든 ID·번호를 믿지 않는다 (규칙 §14, §37 · 저장소 기존 원칙과 동일).

`impact_analyzer/validation.py` 가 하는 것과 같은 검증을 Issue 기반 판정에 적용한다.

- 응답의 `tc_id` 가 실제 TC 목록에 없으면 그 판정을 **결과에서 제외**한다.
- 근거 `chunk_id` 는 실제 Chunk ID 만 남기고 걸러낸다.
- 걸러낸 뒤 근거가 하나도 없으면 confidence 를 review 임계값 아래로 내리고 사람 확인으로 돌린다.
- Regression 축은 코드가 고정한 목록에 없는 값을 버린다.
- Step–Expected 번호 일치를 검사한다 (규칙 §14). LLM 출력에서 자주 어긋나는 지점이다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.config import get_settings
from app.modules.qa_agent.schemas import (
    AXIS_LABELS,
    PRIORITIES,
    PRIORITY_LOW,
    RELEVANCE_NOT_RELATED,
    RELEVANCES,
    TC_JUDGMENTS,
    TC_SPEC_REVIEW,
    QaAgentDecision,
    RegressionArea,
    SpecificationRelevance,
    TcCoverageDecision,
)

EXPECTED_NUMBER_RE = re.compile(r"^\s*(\d+)\s*[.)]")


@dataclass
class ValidationReport:
    """무엇을 걸러냈는지. 조용히 버리면 규칙 §55(검색 실패를 숨긴 판정)와 같은 문제가 된다."""

    dropped_tc_ids: list[str] = field(default_factory=list)
    dropped_chunk_ids: list[str] = field(default_factory=list)
    dropped_axes: list[str] = field(default_factory=list)
    downgraded_tc_ids: list[str] = field(default_factory=list)
    step_number_mismatches: list[str] = field(default_factory=list)

    @property
    def has_findings(self) -> bool:
        return bool(
            self.dropped_tc_ids or self.dropped_chunk_ids or self.dropped_axes or self.downgraded_tc_ids or self.step_number_mismatches
        )

    def as_dict(self) -> dict:
        return {
            "dropped_tc_ids": self.dropped_tc_ids,
            "dropped_chunk_ids": self.dropped_chunk_ids,
            "dropped_axes": self.dropped_axes,
            "downgraded_tc_ids": self.downgraded_tc_ids,
            "step_number_mismatches": self.step_number_mismatches,
        }


def _review_threshold() -> float:
    return float(get_settings().get("analysis.review_confidence", 0.60))


def validate_specification_relevance(
    entries: list[SpecificationRelevance], known_chunk_ids: set[str], report: ValidationReport
) -> list[SpecificationRelevance]:
    validated: list[SpecificationRelevance] = []
    for entry in entries:
        if entry.chunk_id not in known_chunk_ids:
            report.dropped_chunk_ids.append(entry.chunk_id)
            continue
        relevance = entry.relevance if entry.relevance in RELEVANCES else RELEVANCE_NOT_RELATED
        validated.append(entry.model_copy(update={"relevance": relevance}))
    return validated


def validate_tc_coverage(
    entries: list[TcCoverageDecision], known_tc_ids: set[str], known_chunk_ids: set[str], report: ValidationReport
) -> list[TcCoverageDecision]:
    validated: list[TcCoverageDecision] = []
    for entry in entries:
        if entry.tc_id not in known_tc_ids:
            report.dropped_tc_ids.append(entry.tc_id)
            continue
        kept_chunks = [chunk_id for chunk_id in entry.evidence_chunk_ids if chunk_id in known_chunk_ids]
        report.dropped_chunk_ids.extend(chunk_id for chunk_id in entry.evidence_chunk_ids if chunk_id not in known_chunk_ids)

        judgment = entry.judgment if entry.judgment in TC_JUDGMENTS else TC_SPEC_REVIEW
        confidence = entry.confidence
        if not kept_chunks:
            # 근거 없는 판정을 추천으로 올리지 않는다. 임계값 바로 아래로 내려 사람 확인 대상이 되게 한다.
            capped = round(_review_threshold() - 0.01, 4)
            if confidence > capped:
                confidence = capped
                report.downgraded_tc_ids.append(entry.tc_id)
            # 근거가 없으면 Root Cause 를 덮는다고 인정하지 않는다 (규칙 §33).
            if entry.covers_root_cause:
                validated.append(
                    entry.model_copy(
                        update={"judgment": judgment, "evidence_chunk_ids": kept_chunks, "confidence": confidence, "covers_root_cause": False}
                    )
                )
                continue
        validated.append(entry.model_copy(update={"judgment": judgment, "evidence_chunk_ids": kept_chunks, "confidence": confidence}))
    return validated


def validate_regression_areas(
    entries: list[RegressionArea], known_chunk_ids: set[str], known_tc_ids: set[str], report: ValidationReport
) -> list[RegressionArea]:
    """축은 코드가 고정한 목록만 남기고, 빠진 축은 '검토했으나 해당 없음'으로 채운다."""
    validated: dict[str, RegressionArea] = {}
    for entry in entries:
        if entry.axis not in AXIS_LABELS:
            report.dropped_axes.append(entry.axis)
            continue
        kept_chunks = [chunk_id for chunk_id in entry.evidence_chunk_ids if chunk_id in known_chunk_ids]
        report.dropped_chunk_ids.extend(chunk_id for chunk_id in entry.evidence_chunk_ids if chunk_id not in known_chunk_ids)
        kept_tcs = [tc_id for tc_id in entry.related_tc_ids if tc_id in known_tc_ids]
        report.dropped_tc_ids.extend(tc_id for tc_id in entry.related_tc_ids if tc_id not in known_tc_ids)
        priority = entry.priority if entry.priority in PRIORITIES else PRIORITY_LOW
        validated[entry.axis] = entry.model_copy(
            update={"priority": priority, "evidence_chunk_ids": kept_chunks, "related_tc_ids": kept_tcs}
        )

    for axis in AXIS_LABELS:
        validated.setdefault(
            axis,
            RegressionArea(axis=axis, applicable=False, priority=PRIORITY_LOW, reason="모델이 이 축을 판정하지 않았습니다 — QA 확인 필요"),
        )
    return [validated[axis] for axis in AXIS_LABELS]


def check_step_expected_numbers(steps: list[str], expected: list[str]) -> list[str]:
    """규칙 §14: Expected 번호가 실제 확인 Step 번호와 일치해야 한다.

    Expected 줄에 번호가 붙어 있으면 그 번호가 Step 범위 안이어야 한다. 번호가 없는 줄은
    검사하지 않는다 (단일 Expected 는 번호를 붙이지 않는 것이 보통이다).
    """
    if not steps:
        return []
    mismatches: list[str] = []
    for line in expected:
        match = EXPECTED_NUMBER_RE.match(line)
        if not match:
            continue
        number = int(match.group(1))
        if number < 1 or number > len(steps):
            mismatches.append(f"Expected '{line[:40]}' 의 번호 {number} 가 Step 범위(1~{len(steps)})를 벗어납니다")
    return mismatches


def validate_decision(
    decision: QaAgentDecision,
    known_chunk_ids: set[str],
    known_tc_ids: set[str],
    issue_steps: list[str] | None = None,
    issue_expected: list[str] | None = None,
) -> tuple[QaAgentDecision, ValidationReport]:
    """모델 응답 전체를 검증한 사본과 검증 리포트를 돌려준다."""
    report = ValidationReport()
    validated = decision.model_copy(
        update={
            "specification_relevance": validate_specification_relevance(decision.specification_relevance, known_chunk_ids, report),
            "tc_coverage": validate_tc_coverage(decision.tc_coverage, known_tc_ids, known_chunk_ids, report),
            "regression_areas": validate_regression_areas(decision.regression_areas, known_chunk_ids, known_tc_ids, report),
        }
    )
    report.step_number_mismatches = check_step_expected_numbers(issue_steps or [], issue_expected or [])
    report.dropped_chunk_ids = list(dict.fromkeys(report.dropped_chunk_ids))
    report.dropped_tc_ids = list(dict.fromkeys(report.dropped_tc_ids))
    return validated, report
