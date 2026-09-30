"""VXvue QA 매뉴얼 개정 검증 도메인 모델.

원본 스펙 §18-23 판정/근거 스키마를 그대로 따른다. AI에게는 QuickJudgmentResponse/
DetailJudgmentResponse처럼 좁은 스키마만 강제하고, 사람이 읽는 화면/저장용 모델
(ManualChangeJudgment 등)은 서버 코드가 두 응답을 합쳐 조립한다 (impact_analyzer의
"AI는 ID만 인용, 서버가 표시 텍스트를 조립" 패턴과 동일 — 환각 방지).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class ManualJudgment(str, Enum):
    PASS = "PASS"
    SUPPLEMENT_REQUIRED = "SUPPLEMENT_REQUIRED"
    MODIFICATION_REQUIRED = "MODIFICATION_REQUIRED"
    DELETE_REQUIRED = "DELETE_REQUIRED"
    MISSING_SUSPECTED = "MISSING_SUSPECTED"
    SPEC_CONFIRMATION_REQUIRED = "SPEC_CONFIRMATION_REQUIRED"
    VERSION_CONFIRMATION_REQUIRED = "VERSION_CONFIRMATION_REQUIRED"
    UNABLE_TO_DETERMINE = "UNABLE_TO_DETERMINE"


# UI에는 한국어로 표시한다 (스펙 §19).
JUDGMENT_LABELS_KO: dict[str, str] = {
    ManualJudgment.PASS: "문제없음",
    ManualJudgment.SUPPLEMENT_REQUIRED: "보강 필요",
    ManualJudgment.MODIFICATION_REQUIRED: "수정 필요",
    ManualJudgment.DELETE_REQUIRED: "삭제 필요",
    ManualJudgment.MISSING_SUSPECTED: "누락 의심",
    ManualJudgment.SPEC_CONFIRMATION_REQUIRED: "사양 확인 필요",
    ManualJudgment.VERSION_CONFIRMATION_REQUIRED: "문서 버전 확인 필요",
    ManualJudgment.UNABLE_TO_DETERMINE: "판정 불가",
}


class EvidenceType(str, Enum):
    DIRECT_SPEC = "DIRECT_SPEC"
    RELATED_SPEC = "RELATED_SPEC"
    DELETED_SPEC_HISTORY = "DELETED_SPEC_HISTORY"
    RELEASE_NOTE = "RELEASE_NOTE"
    DESIGN_REVIEW = "DESIGN_REVIEW"
    LEGACY_MANUAL = "LEGACY_MANUAL"
    RELATED_MANUAL = "RELATED_MANUAL"


class VisualVerificationStatus(str, Enum):
    """SRS 원본의 취소선/밑줄 등 개정 표시 시각 확인 결과 (스펙 §16). 이번 단계는 PDF 시각
    검증을 구현하지 않으므로 항상 NOT_VERIFIED이고, QA가 UI에서 수동으로 갱신할 수 있다."""

    VERIFIED_NO_REVISION_MARK = "VERIFIED_NO_REVISION_MARK"
    VERIFIED_STRIKETHROUGH = "VERIFIED_STRIKETHROUGH"
    VERIFIED_UNDERLINE = "VERIFIED_UNDERLINE"
    VERIFIED_REPLACED = "VERIFIED_REPLACED"
    NOT_VERIFIED = "NOT_VERIFIED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class CommentStatus(str, Enum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    NOT_RESOLVED = "NOT_RESOLVED"
    REOPENED = "REOPENED"
    IGNORED_BY_QA = "IGNORED_BY_QA"


class Evidence(BaseModel):
    type: EvidenceType
    srs_no: str = ""
    srs_title: str = ""
    source_file: str = ""
    page: int = 0
    section: str = ""
    chunk_id: str = ""
    visual_verification_status: VisualVerificationStatus = VisualVerificationStatus.NOT_VERIFIED


class QuickJudgmentResponse(BaseModel):
    """1차 AI 호출 응답 (짧은 구조화 판정, 스펙 §18). PASS면 2차 호출을 하지 않는다."""

    decision: ManualJudgment
    confidence: float = Field(ge=0.0, le=1.0)
    reason_codes: list[str] = Field(default_factory=list)
    requires_detail_generation: bool = False


class DetailJudgmentResponse(BaseModel):
    """2차 AI 호출 응답 — 문제가 있는 건만 호출한다 (스펙 §18)."""

    problem: str = ""
    recommended_manual_text: str = ""
    qa_comment: str = ""
    evidence: list[Evidence] = Field(default_factory=list)
    needs_human_review: bool = False


class ManualChangeJudgment(BaseModel):
    """저장/화면 표시용으로 1차+2차 응답을 합친 최종 판정 (manual_changes.ai_judgment_json)."""

    decision: ManualJudgment
    confidence: float = Field(ge=0.0, le=1.0)
    reason_codes: list[str] = Field(default_factory=list)
    problem: str = ""
    recommended_manual_text: str = ""
    qa_comment: str = ""
    evidence: list[Evidence] = Field(default_factory=list)
    needs_human_review: bool = False
    prompt_version: int = 0
    # 그림 변경의 문제없음 판정을 판정 불가로 바꿨을 때 AI가 낸 원래 판정 (REQ-MANUAL-012).
    ai_original_decision: ManualJudgment | None = None


class ManualRevision(BaseModel):
    id: int | None = None
    product: str
    manual_name: str
    revision_label: str
    target_version: str = ""
    round_number: int = 0
    parent_revision_id: int | None = None
    baseline_revision_id: int | None = None
    source_path: str
    status: str = "REGISTERED"
    analysis_id: str | None = None
    created_at: datetime | None = None


class ManualChange(BaseModel):
    id: int | None = None
    revision_id: int
    kind: str  # insertion | deletion | move_from | move_to
    author: str = ""
    change_date: str = ""
    paragraph_index: int = 0
    source_page: int | None = None
    review_required: bool = False
    text: str = ""
    functional: bool = True
    decision: str | None = None
    confidence: float | None = None
    qa_decision: str | None = None
    qa_note: str | None = None
    ai_judgment: ManualChangeJudgment | None = None
    created_at: datetime | None = None


class ManualComment(BaseModel):
    id: int | None = None
    change_id: int
    round_number: int = 1
    comment_text: str
    status: CommentStatus = CommentStatus.OPEN
    resolved_in_revision_id: int | None = None
    created_at: datetime | None = None
