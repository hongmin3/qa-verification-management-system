"""Skill 결과 파일 형식과 검증 (SPEC REQ-DAILY-007).

형식은 QA 규칙 §46 공통 출력(Skill ID / Gate Status / Findings / Evidence / Open Questions /
Next Recommended Skill / Human Review Required)을 JSON 으로 옮긴 것이다. Skill 문서
(`skills/vxvue-qa-rules/references/output-contract.md`)가 같은 형식을 설명한다.

검증은 두 겹이다.
1. 파일 전체가 형식에 맞지 않으면(`ResultFileError`) 작업을 한 번 더 돌린다.
2. 형식은 맞지만 개별 Finding 이 규칙을 어기면 그 Finding 만 버리고 이유를 남긴다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

#: 개편 전 Skill (옛 Finding 표시·옛 결과 검증용). 새 실행은 SKILL_B·SKILL_C 를 돌리지 않는다.
SKILL_B = "vxvue-spec-change-impact"
SKILL_C = "vxvue-issue-verification"
LEGACY_SKILL_E = "vxvue-trace-gap"
LEGACY_SKILL_F = "vxvue-manual-completeness"
#: 제품 공통 Skill (NFR-QAINTEL-002).
SKILL_E = "qa-trace-gap"
SKILL_F = "qa-manual-completeness"
SKILL_COVERAGE = "qa-spec-coverage-analysis"

SKILL_LABELS = {
    SKILL_B: "사양 변경 영향 검토",
    SKILL_C: "이슈 수정확인 초안",
    SKILL_E: "사양–TC 연결 점검",
    SKILL_F: "매뉴얼 누락 후보 점검",
    LEGACY_SKILL_E: "사양–TC 연결 점검",
    LEGACY_SKILL_F: "매뉴얼 누락 후보 점검",
    "qa-new-issue-analysis": "신규 이슈 분석",
    "qa-fixed-issue-analysis": "수정 완료 이슈 분석",
    "qa-spec-decision-analysis": "Spec 판정 이슈 분석",
    "qa-comment-analysis": "새 댓글 분석",
    SKILL_COVERAGE: "사양 변경 Coverage 분석",
}

#: 같은 Finding 인지 가릴 때 함께 보는 옛 Skill 이름 (이름을 바꾼 뒤 옛 Finding 과 겹치지 않게).
SKILL_ALIASES: dict[str, tuple[str, ...]] = {
    SKILL_E: (LEGACY_SKILL_E,),
    SKILL_F: (LEGACY_SKILL_F,),
    SKILL_COVERAGE: (SKILL_B,),
}

VERDICTS: dict[str, tuple[str, ...]] = {
    SKILL_B: ("유지", "경미 수정", "수정 필수", "신규 TC 필요", "사양 확인 필요"),
    SKILL_C: ("신규 TC 필요", "기존 TC 보강", "유지", "사양 확인 필요", "판정만"),
    SKILL_E: ("TC 없음", "삭제된 SRS 참조"),
    SKILL_F: ("보강 권장", "타 문서 위임 적절", "유지", "사양 확인 필요"),
    LEGACY_SKILL_E: ("TC 없음", "삭제된 SRS 참조"),
    LEGACY_SKILL_F: ("보강 권장", "타 문서 위임 적절", "유지", "사양 확인 필요"),
    "qa-new-issue-analysis": ("SPEC_VIOLATION", "CONSISTENT_WITH_SPEC", "SPEC_UNDEFINED", "SPEC_AMBIGUOUS", "INSUFFICIENT_EVIDENCE"),
    "qa-fixed-issue-analysis": ("CONSISTENT_WITH_SPEC", "PARTIALLY_CONSISTENT", "CONTRADICTS_SPEC", "SPEC_UNDEFINED", "INSUFFICIENT_EVIDENCE"),
    "qa-spec-decision-analysis": ("SUPPORTED_BY_SPEC", "PARTIALLY_SUPPORTED", "SPEC_AMBIGUOUS", "SPEC_NOT_FOUND", "CONTRADICTS_SPEC"),
    "qa-comment-analysis": ("ROOT_CAUSE_INFORMATION", "RESOLUTION_INFORMATION", "REPRODUCTION_INFORMATION", "SPEC_CLAIM",
                            "REQUIREMENT_INFORMATION", "QA_ACTION_REQUIRED", "OTHER_SIGNIFICANT_INFORMATION", "NOT_SIGNIFICANT"),
    SKILL_COVERAGE: ("FULLY_COVERED", "PARTIALLY_COVERED", "NOT_COVERED", "SPEC_REVIEW_REQUIRED"),
}

#: 새 분석 Skill. 결과 검증은 `evidence_validation.py` 가 한다 (REQ-QAINTEL-017).
ANALYSIS_SKILL_NAMES = frozenset(
    {"qa-new-issue-analysis", "qa-fixed-issue-analysis", "qa-spec-decision-analysis", "qa-comment-analysis", SKILL_COVERAGE}
)

ISSUE_TYPES = (
    "Program Fixed",
    "Spec/Not Bug",
    "Spec Change/Document Fix",
    "Inquiry",
    "Duplicate",
    "Blocked",
    "Cannot Reproduce",
)

#: 근거 위치로 인정하는 모양: Work Item ID, Legacy SRS 번호, 행 번호, 절 번호, 시트.
LOCATION_RE = re.compile(
    r"(?:[A-Z]{2,}-\d+|\b\d{2}-\d{1,3}-\d{1,3}\b|\d+\s*행|row\s*\d+|§\s*\d+|\d+(?:\.\d+)*\s*절|sheet|시트)",
    re.IGNORECASE,
)

#: QA 규칙 §55 가 금지하는 조치. 이슈를 닫거나 종료하라는 말, TC 를 덮어쓰라는 말, 결과·이력을
#: 지우라는 말만 잡는다. "닫기 버튼", "Closed 상태", "앱 종료 시" 같은 화면·기능 이름은 잡지 않는다.
FORBIDDEN_ACTION_RE = re.compile(
    r"(?:"
    r"(?:이슈|issue|티켓|ticket|work\s*item|[A-Z]{2,}-\d+)\s*(?:을|를|은|는)?\s*(?:닫|종료|close\b|resolve\b)"
    r"|\bclose\s+(?:the\s+|this\s+|that\s+)?(?:issue|ticket|bug|work\s*item|[A-Z]{2,}-\d+)"
    r"|닫(?:는다|습니다|으세요|으십시오|아\s*주|아야|을\s*것|자\b)"
    r"|종료\s*처리\s*(?:한다|합니다|하세요|하십시오|할\s*것)"
    r"|덮어\s*(?:쓰|쓴|씀|써)"
    r"|overwrit"
    r"|(?:결과|이력|기록)\s*(?:을|를)?\s*(?:삭제|지운|지우)"
    r")",
    re.IGNORECASE,
)


class Evidence(BaseModel):
    #: `spec_doc`(Knowledge 사양서 조각)·`comment`(이슈 댓글)는 QA Intelligence 분석에서 쓴다.
    source_type: Literal["srs", "tc", "issue", "manual", "rules", "user_answer", "spec_doc", "comment"]
    location: str
    summary: str = ""
    validity: Literal["Current", "Deleted", "Deprecated", "Unknown"] = "Unknown"
    #: 작업 입력으로 받은 참조 번호 (`spec:<조각>`, 댓글 번호). 코드가 입력과 대조한다.
    ref: str = ""


#: 검증 TC 관점 (REQ-QAINTEL-013 · REQ-QAINTEL-016). 빈 값은 개편 전 초안이다.
DRAFT_PERSPECTIVES = (
    "DIRECT_FIX", "DIRECT_SPEC", "REGRESSION", "STATE_TRANSITION", "PERSISTENCE", "BOUNDARY", "NEGATIVE",
    "INTEGRATION", "CONFIGURATION", "ENVIRONMENT",
)


class DraftTc(BaseModel):
    kind: Literal["수정확인", "Regression"]
    srs_no: str = ""
    change: str = ""
    change_detail: str = ""
    title: str
    precondition: str = ""
    test_step: str
    expected_result: str
    test_data: str = ""
    perspective: str = ""
    #: 왜 이 TC 가 필요한가 (분석 근거와 연결)
    rationale: str = ""


class TcRef(BaseModel):
    workbook: str = ""
    sheet: str = ""
    row: int = 0
    tc_id: str = ""


class Finding(BaseModel):
    subject: str = Field(min_length=1)
    subject_title: str = ""
    verdict: str
    summary: str = Field(min_length=1)
    detail: str = ""
    confidence: Literal["Confirmed", "Review Needed", "Unsupported"] = "Review Needed"
    evidence: list[Evidence] = Field(default_factory=list)
    tc_ref: TcRef | None = None
    issue_type: str = ""
    action: str = ""
    draft_tcs: list[DraftTc] = Field(default_factory=list)
    #: QA Intelligence 분석 구획. 모양은 분석 종류마다 다르고 `evidence_validation.py` 가 검사한다.
    sections: dict = Field(default_factory=dict)


class OpenQuestion(BaseModel):
    subject: str = ""
    question: str = Field(min_length=1)
    why: str = ""


class SkillResult(BaseModel):
    skill: str
    task_id: str
    gate_status: dict[str, Literal["PASS", "FAIL", "CHECK", "DRAFT"]] = Field(default_factory=dict)
    findings: list[Finding] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    next_recommended_skill: str = ""
    human_review_required: bool = True


class ResultFileError(ValueError):
    """결과 파일이 없거나 형식이 틀렸다. 작업 재실행 대상이다."""


@dataclass
class CheckedResult:
    result: SkillResult
    accepted: list[Finding] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)


def rejection_reason(skill: str, finding: Finding) -> str | None:
    allowed = VERDICTS.get(skill, ())
    if finding.verdict not in allowed:
        return f"허용되지 않은 판정 값: {finding.verdict}"
    if finding.action and FORBIDDEN_ACTION_RE.search(finding.action):
        return f"금지된 조치를 담고 있습니다(QA 규칙 §55): {finding.action[:80]}"
    if skill in ANALYSIS_SKILL_NAMES:
        # 번호·근거가 실제 자료에 있는지는 작업 입력을 아는 evidence_validation.py 가 본다 (REQ-QAINTEL-017).
        return None
    if not finding.evidence:
        return "근거(evidence)가 없습니다."
    if not any(LOCATION_RE.search(item.location or "") for item in finding.evidence):
        return "근거에 문서 위치(SRS ID·시트/행·절)가 없습니다."
    if finding.action and FORBIDDEN_ACTION_RE.search(finding.action):
        return f"금지된 조치를 담고 있습니다(QA 규칙 §55): {finding.action[:80]}"
    if skill == SKILL_C and finding.issue_type and finding.issue_type not in ISSUE_TYPES:
        return f"알 수 없는 이슈 유형: {finding.issue_type}"
    if finding.draft_tcs and skill == SKILL_C and finding.issue_type != "Program Fixed":
        return "Program Fixed 가 아닌 이슈에 TC 초안을 만들었습니다(REQ-DAILY-004)."
    return None


def parse_result(path: Path, expected_skill: str, expected_task: str) -> CheckedResult:
    if not path.is_file():
        raise ResultFileError(f"결과 파일이 없습니다: {path.name}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ResultFileError(f"결과 파일이 JSON 이 아닙니다: {type(exc).__name__}") from exc
    try:
        result = SkillResult.model_validate(raw)
    except ValidationError as exc:
        raise ResultFileError(f"결과 형식 오류 {exc.error_count()}건: {exc.errors()[0]['loc']}") from exc
    if result.skill != expected_skill or result.task_id != expected_task:
        raise ResultFileError(f"결과의 skill/task_id 가 작업과 다릅니다: {result.skill}/{result.task_id}")
    checked = CheckedResult(result=result)
    for finding in result.findings:
        reason = rejection_reason(expected_skill, finding)
        if reason:
            checked.rejected.append({"subject": finding.subject, "verdict": finding.verdict, "reason": reason})
        else:
            checked.accepted.append(finding)
    return checked
