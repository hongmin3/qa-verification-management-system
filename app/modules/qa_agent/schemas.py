"""QA Agent 응답 스키마 (규칙 §46 Skill Input/Output Contract).

**LLM 호출은 분석 1건당 1회다.** S01(Issue 의미 분석) · S02(사양 관련도) · S03(TC Coverage)
· S05(Regression 영향)의 판단을 한 번에 받는다. Skill 마다 호출을 쪼개면 같은 근거를 네 번
보내게 되고, Skill 사이에서 모델이 앞 단계 결론을 다시 설명하느라 토큰이 늘어난다. 이 저장소
기존 기능(Regression 영향 분석)도 같은 규칙을 따른다.

모델에게 **묻지 않는 것**을 분명히 해 둔다.

- Issue 유형 분류 — `rndReviewResult` 로 코드가 이미 정했다 (§6).
- 검색 키워드 생성 — 정규식으로 뽑았다 (§9.1).
- 근거 충분성 — Gate 가 셌다 (§44).
- Step/Expected 번호 일치 — 코드가 검증한다 (§14).

그래서 스키마에는 **의미 판단만** 담는다.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# --- S03 TC Coverage 판정 (규칙 §16, 로드맵 §9) --------------------------------

TC_KEEP = "KEEP"
TC_MINOR_UPDATE = "MINOR_UPDATE"
TC_MUST_UPDATE = "MUST_UPDATE"
TC_NEW_TC = "NEW_TC"
TC_ISSUE_LINK_UPDATE = "ISSUE_LINK_UPDATE"
TC_SPEC_REVIEW = "SPEC_REVIEW"

TC_JUDGMENTS = (TC_KEEP, TC_MINOR_UPDATE, TC_MUST_UPDATE, TC_NEW_TC, TC_ISSUE_LINK_UPDATE, TC_SPEC_REVIEW)

TC_JUDGMENT_LABELS = {
    TC_KEEP: "유지",
    TC_MINOR_UPDATE: "경미 수정",
    TC_MUST_UPDATE: "수정 필수",
    TC_NEW_TC: "신규 TC 필요",
    TC_ISSUE_LINK_UPDATE: "Issue Link 수정",
    TC_SPEC_REVIEW: "사양 확인 필요",
}

#: QA 승인 없이 실제 TC 를 바꾸지 않는다 (규칙 §55). 이 판정들은 전부 "초안" 취급이다.
TC_JUDGMENTS_REQUIRING_APPROVAL = frozenset({TC_MINOR_UPDATE, TC_MUST_UPDATE, TC_NEW_TC, TC_ISSUE_LINK_UPDATE})

# --- S05 Regression 축 (규칙 §32) ---------------------------------------------
# 축은 코드가 고정한다. 모델은 "이 축에 해당하는가"와 그 근거만 채운다. 축을 모델이 만들면
# 실행마다 달라져 결과를 비교할 수 없다.

AXIS_DIRECT = "DIRECT"
AXIS_STATE = "STATE"
AXIS_DATA = "DATA"
AXIS_PERSISTENCE = "PERSISTENCE"
AXIS_INTEGRATION = "INTEGRATION"
AXIS_PERMISSION = "PERMISSION"
AXIS_PRIOR_ISSUE = "PRIOR_ISSUE"
AXIS_GENERATOR = "GENERATOR"

REGRESSION_AXES: tuple[tuple[str, str, str], ...] = (
    (AXIS_DIRECT, "직접", "변경된 기능 자체"),
    (AXIS_STATE, "상태", "A→B, B→A, A→B→A 전이 · Error→정상 · 연결→해제→재연결"),
    (AXIS_DATA, "데이터", "Cache · ErrorCode · List · Patient · Study 잔존·오염"),
    (AXIS_PERSISTENCE, "저장", "Save · Reload · 재진입 · 재시작 후 값 유지"),
    (AXIS_INTEGRATION, "연동", "DICOM · API · WebSocket · 외부 장치"),
    (AXIS_PERMISSION, "권한", "계정 권한별 노출·동작 차이"),
    (AXIS_PRIOR_ISSUE, "기존 Issue", "같은 Root Cause 로 과거에 났던 Issue 의 재발"),
    (AXIS_GENERATOR, "Generator", "Step · Size · Study · Dose Mode · Focal Spot · Save Dose"),
)

AXIS_LABELS = {code: label for code, label, _ in REGRESSION_AXES}
AXIS_DESCRIPTIONS = {code: description for code, _, description in REGRESSION_AXES}

PRIORITY_HIGH = "HIGH"
PRIORITY_MEDIUM = "MEDIUM"
PRIORITY_LOW = "LOW"
PRIORITIES = (PRIORITY_HIGH, PRIORITY_MEDIUM, PRIORITY_LOW)

RELEVANCE_MAIN = "MAIN"
RELEVANCE_RELATED = "RELATED"
RELEVANCE_NOT_RELATED = "NOT_RELATED"
RELEVANCES = (RELEVANCE_MAIN, RELEVANCE_RELATED, RELEVANCE_NOT_RELATED)


class IssueAnalysis(BaseModel):
    """S01 — Issue 의 의미 분석. 필드 추출이 아니라 판단만 담는다."""

    trigger: list[str] = Field(default_factory=list, description="이 결함을 실제로 유발하는 조건. Issue Step 에서 유도한다.")
    root_cause_summary: str = Field(default="", description="발생원인 본문을 근거로 한 Root Cause 요약. 본문에 없으면 빈 문자열.")
    change_target: str = Field(default="", description="수정 주체·대상 모듈. 조치내역 본문에 근거가 없으면 빈 문자열.")
    before: str = Field(default="", description="수정 전 동작. 근거 없으면 빈 문자열.")
    after: str = Field(default="", description="수정 후 동작. 근거 없으면 빈 문자열.")


class SpecificationRelevance(BaseModel):
    """S02 — 검색된 사양 Chunk 가 이 Issue 와 실제로 관련되는지."""

    chunk_id: str = Field(description="입력으로 준 사양 Chunk ID 그대로. 새로 만들지 않는다.")
    relevance: str = Field(description=f"{RELEVANCE_MAIN} / {RELEVANCE_RELATED} / {RELEVANCE_NOT_RELATED} 중 하나")
    reason: str = Field(default="", description="그렇게 본 근거. Chunk 본문 표현을 인용한다.")


class TcCoverageDecision(BaseModel):
    """S03 — 기존 TC 하나에 대한 판정."""

    tc_id: str = Field(description="입력으로 준 TC ID 그대로. 새로 만들지 않는다.")
    judgment: str = Field(description=" / ".join(TC_JUDGMENTS) + " 중 하나")
    covers_root_cause: bool = Field(default=False, description="이 TC 를 수행하면 수정 전 Root Cause 가 Fail 로 검출되는가")
    reason: str = Field(default="", description="판정 근거. Issue Trigger 와 TC Step 을 대조해 설명한다.")
    evidence_chunk_ids: list[str] = Field(default_factory=list, description="근거로 쓴 사양 Chunk ID. 입력에 있는 것만.")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class RegressionArea(BaseModel):
    """S05 — Regression 축 하나에 대한 판정."""

    axis: str = Field(description=" / ".join(AXIS_LABELS) + " 중 하나")
    applicable: bool = Field(default=False, description="이 Issue 의 Root Cause 로 이 축에 영향이 갈 수 있는가")
    priority: str = Field(default=PRIORITY_LOW, description=" / ".join(PRIORITIES))
    trigger: str = Field(default="", description="이 축을 검증할 실제 조작 조건")
    expected: str = Field(default="", description="관찰 가능한 단일 Expected. 문서 근거가 없으면 빈 문자열.")
    reason: str = Field(default="", description="이 축을 넣거나 뺀 근거")
    evidence_chunk_ids: list[str] = Field(default_factory=list)
    related_tc_ids: list[str] = Field(default_factory=list, description="입력으로 준 TC ID 중 이 축과 관련된 것")


class QaAgentDecision(BaseModel):
    """단일 구조화 호출의 응답 전체."""

    issue_analysis: IssueAnalysis = Field(default_factory=IssueAnalysis)
    specification_relevance: list[SpecificationRelevance] = Field(default_factory=list)
    tc_coverage: list[TcCoverageDecision] = Field(default_factory=list)
    regression_areas: list[RegressionArea] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(
        default_factory=list,
        description="판정에 실제로 영향을 주는데 입력에 없던 정보. 규칙 §5.2 에 따라 추정하지 말고 여기에 적는다.",
    )
