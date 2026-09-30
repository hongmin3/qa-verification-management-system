from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field

from app.core.document_schemas import RevisionMark, SpecificationChunk, TestCase

__all__ = [
    "RevisionMark",
    "SpecificationChunk",
    "TestCase",
    "ANALYSIS_STAGES",
    "Impact",
    "EvidenceLevel",
    "ChangeItem",
    "ChangeAnalysis",
    "DraftTestCase",
    "ImpactDecision",
    "GeminiAnalysisResponse",
    "AnalysisResult",
]


ANALYSIS_STAGES: tuple[str, ...] = (
    "입력 문서 분석",
    "변경사항 추출",
    "최신 사양서 조회",
    "TC 후보 검색",
    "AI 영향도 분석",
    "Regression TC 선정",
    "신규 TC 초안 검증",
    "HTML 결과 생성",
)


class Impact(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    NONE = "NONE"


class EvidenceLevel(str, Enum):
    """VXvue TC 설계 가이드 Rev.1.7 §7 근거 수준."""

    EXPLICIT = "EXPLICIT"  # 명시 사양: 최신 유효 사양서에 직접 명시
    EXPLICIT_CANDIDATE = "EXPLICIT_CANDIDATE"  # 명시 사양 후보: 검색은 됐으나 원본 유효성 미확인
    DELETED_HISTORY = "DELETED_HISTORY"  # 삭제 이력 근거: 취소선 등으로 삭제된 사양
    EXISTING_BEHAVIOR = "EXISTING_BEHAVIOR"  # 기존 동작: 매뉴얼/TC에서만 확인
    INFERRED = "INFERRED"  # 확인 권장: 직접 근거 없는 추론


class ChangeItem(BaseModel):
    """의미 단위로 묶은 변경사항 1건. 사용자 보고서의 Change Summary에 그대로 렌더링된다.

    모든 필드는 changed_features 원문 근거로만 채운다 (근거 없으면 빈 문자열/빈 리스트).
    """

    feature: str = ""
    related_modules: list[str] = Field(default_factory=list)
    change_type: str = ""
    issue: str = ""
    preconditions: list[str] = Field(default_factory=list)
    reproduction_steps: list[str] = Field(default_factory=list)
    problem: str = ""
    cause: str = ""
    fix: str = ""
    impact_area: str = ""


class ChangeAnalysis(BaseModel):
    user_notes: str = ""
    changed_features: list[str] = Field(default_factory=list)
    change_items: list[ChangeItem] = Field(default_factory=list)
    purpose: str = ""
    ui_changes: list[str] = Field(default_factory=list)
    interface_changes: list[str] = Field(default_factory=list)
    dicom_changes: list[str] = Field(default_factory=list)
    workflow_changes: list[str] = Field(default_factory=list)
    configuration_changes: list[str] = Field(default_factory=list)
    stored_data_changes: list[str] = Field(default_factory=list)
    compatibility_changes: list[str] = Field(default_factory=list)
    risk_keywords: list[str] = Field(default_factory=list)


class DraftTestCase(BaseModel):
    """근거가 없는 필드는 반드시 '확인 필요'로 남기고 생성하지 않는다 (VXvue TC 가이드 Rev.1.7 §2, §7.6)."""

    changed_feature: str
    srs_no: str = "SRS No 확인 필요"
    title: str = ""
    precondition: str = ""
    test_step: str = ""
    expected_result: str = ""
    test_data: str = ""
    evidence_chunk_ids: list[str] = Field(default_factory=list)


class ImpactDecision(BaseModel):
    tc_id: str
    impact: Impact
    confidence: float = Field(ge=0.0, le=1.0)
    direct_impact: bool = False
    indirect_impact: bool = False
    regression_needed: bool = False
    reason: str
    relevant_specifications: list[str] = Field(default_factory=list)
    specification_reference: str = ""
    verification_points: list[str] = Field(default_factory=list)
    recommended: bool = False
    manual_review_required: bool = False
    review_status: str = ""
    evidence_level: EvidenceLevel = EvidenceLevel.INFERRED
    revision_mark: RevisionMark = RevisionMark.UNVERIFIED


class GeminiAnalysisResponse(BaseModel):
    decisions: list[ImpactDecision]
    draft_test_cases: list[DraftTestCase] = Field(default_factory=list)
    change_items: list[ChangeItem] = Field(default_factory=list)


class AnalysisResult(BaseModel):
    analysis_id: str
    created_at: datetime
    change_file: str
    specification_file: str
    testcase_file: str
    change: ChangeAnalysis
    total_tc: int
    candidate_tc: int
    decisions: list[ImpactDecision]
    draft_test_cases: list[DraftTestCase] = Field(default_factory=list)
    token_usage: dict[str, int] = Field(default_factory=dict)
    prompt_version: int = 0
    report_path: Path | None = None
    draft_tc_path: Path | None = None
    spec_sync: dict | None = None
    tc_sync: dict | None = None
    knowledge_documents: list[dict] = Field(default_factory=list)
    #: 읽지 못한 Knowledge 문서 (종류·ID·이름·이유). 분석에 쓰지 못했으므로 knowledge_documents 에 넣지 않는다.
    knowledge_failures: list[dict] = Field(default_factory=list)
    ai_audit: dict = Field(default_factory=dict)
    candidate_ranking: list[dict] = Field(default_factory=list)

    @property
    def recommended_count(self) -> int:
        return sum(item.recommended for item in self.decisions)
