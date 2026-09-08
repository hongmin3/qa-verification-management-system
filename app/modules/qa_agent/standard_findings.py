"""규칙이 열거해 둔 목록을 코드로 옮긴 것 — 표준 표현·금지 추론·중단 조건·자동화 금지.

이 네 절은 규칙 중에서 **가장 결정적으로 구현 가능한 부분**이다. 판단이 아니라 목록이기
때문이다. 목록을 코드로 두면 세 가지가 생긴다.

1. 결과에 쓰는 표현이 담당자·실행마다 달라지지 않는다 (§36).
2. LLM 이 근거 없이 이 주장을 했는지 **출력을 검사해서** 잡아낼 수 있다 (§37).
3. 자동으로 확정하면 안 되는 경로를 코드에서 아예 막는다 (§53, §55).

원문 표현을 그대로 쓴다 — QA 가 결과를 볼 때 규칙 문서와 같은 문구여야 대조가 된다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class Finding(StrEnum):
    """규칙 §36 확인 필요 표준 표현. 결과에는 이 값만 쓴다."""

    NOT_IN_DOCUMENT = "문서상 확인되지 않음"
    NEEDS_MORE_SPEC = "추가 사양 확인 필요"
    NOT_IN_EXISTING_TC = "기존 TC에서 확인되지 않음"
    CANNOT_JUDGE_BEFORE_SPEC = "사양 확인 전 Pass/Fail 판정 불가"
    NEEDS_EXECUTION = "실제 수행 확인 필요"
    OUT_OF_SCOPE = "검증 범위 제외"
    UI_PATH_INCONSISTENCY = "UI 경로 간 동작 불일치"
    SRS_EXISTS_BODY_UNVERIFIED = "관련 SRS 존재는 확인되나 본문 직접 확인 필요"
    SPLIT_SPEC_SEARCH_INCOMPLETE = "전체 분할 사양서 조사 완료 전 존재 여부 확정 불가"
    DEPRECATED_SPEC_UNCERTAIN = "취소선/삭제 사양으로 현재 적용 여부 확인 필요"
    NEEDS_API_VERIFICATION = "별도 API/WebSocket 검증 필요"
    ISSUE_LINK_MEANING_UNVERIFIED = "Issue Link 의미 일치 확인 필요"
    ROOT_CAUSE_COVERAGE_INSUFFICIENT = "Root Cause Coverage 부족"
    DOCUMENT_FIX_TARGET = "문서 수정 확인 대상"
    RUNTIME_TC_UNNECESSARY = "Runtime TC 불필요"


@dataclass(frozen=True)
class ForbiddenInference:
    """규칙 §37 이 근거 없이 확정하지 말라고 정한 주장 하나."""

    code: str
    label: str
    #: 출력 텍스트에서 이 주장을 찾아내는 낱말들. 하나라도 걸리면 근거 유무를 따진다.
    keywords: tuple[str, ...]


# 규칙 §37 근거 없는 추론 금지 — 20개 항목.
FORBIDDEN_INFERENCES: tuple[ForbiddenInference, ...] = (
    ForbiddenInference("auto_save", "자동 저장", ("자동 저장", "자동저장", "autosave", "auto save")),
    ForbiddenInference("auto_restore", "자동 복원", ("자동 복원", "자동복원", "auto restore")),
    ForbiddenInference("retry_count", "Retry 횟수", ("retry", "재시도 횟수", "재시도횟수")),
    ForbiddenInference("timeout", "Timeout", ("timeout", "타임아웃", "제한 시간")),
    ForbiddenInference("popup", "특정 Popup", ("팝업이 표시", "popup 이 표시", "popup is shown", "팝업이 나타")),
    ForbiddenInference("generator_combo", "Generator 조합", ("generator 조합", "촬영 가능 조합", "허용 조합")),
    ForbiddenInference("focal_spot_range", "Focal Spot 공식 범위", ("focal spot", "focal_spot")),
    ForbiddenInference("dicom_vr", "DICOM VR/Type", (" vr ", "vr=", "value representation", "dicom type")),
    ForbiddenInference("aec_end", "AEC 종료 조건", ("aec", "자동노출")),
    ForbiddenInference("exposure_accuracy", "Exposure 정확도", ("exposure 정확", "선량 정확", "dose 정확")),
    ForbiddenInference("setting_priority", "설정 우선순위", ("설정 우선순위", "우선 적용된다")),
    ForbiddenInference("api_success", "API/WebSocket 성공", ("정상 응답한다", "성공을 반환", "success 를 반환", "200 을 반환")),
    ForbiddenInference("log_written", "로그 기록", ("로그에 기록된다", "로그가 남는다", "로그가 기록")),
    ForbiddenInference("db_saved", "DB 저장", ("db 에 저장된다", "db에 저장된다", "데이터베이스에 저장된다")),
    ForbiddenInference("strikethrough_valid", "취소선 유효 여부", ("취소선", "strikethrough")),
    ForbiddenInference("lab_spec_only", "연구소 Spec 판단만으로 정상 확정", ("연구소 판단", "연구소가 spec", "lab_inspec 이므로", "연구소 의견에 따라")),
    ForbiddenInference("lab_doc_only", "연구소 문서 수정 기록만으로 실제 반영 확정", ("문서가 수정되었으므로", "수정 기록이 있으므로")),
    ForbiddenInference("event_carries_data", "Event 가 실제 Data 를 전달한다고 추정", ("event 가 데이터를 전달", "event 로 데이터를 받는다")),
    ForbiddenInference("empty_vs_blank", '"" 와 " " 동일 취급', ("빈 문자열과 공백은 같", "공백과 빈 값은 동일")),
    ForbiddenInference("issue_number_only", "Issue 번호가 있다고 관련 TC 라고 판단", ("issue 번호가 있으므로", "issue 가 링크되어 있으므로")),
)

# 규칙 §53 최종 중단 조건 — 하나라도 핵심 판단에 필요하면 완성형 산출물을 확정하지 않는다.
STOP_CONDITIONS: tuple[tuple[str, str], ...] = (
    ("spec_not_verified", "Spec/Not Bug인데 최신 공식 문서 미확인"),
    ("split_spec_partial", "분할 사양 일부만 확인"),
    ("expected_evidence_missing", "Expected 근거 부족"),
    ("root_cause_unknown", "Root Cause/Resolution 미확인"),
    ("api_command_unknown", "API Command 역할 미확인"),
    ("tc_impact_incomplete", "필수 TC 영향 분석 미완료"),
    ("observation_means_missing", "필수 관찰 수단 제외"),
    ("version_or_env_unclear", "버전/환경 불명확"),
    ("document_fix_not_verified", "Document Fix인데 최신 문서 미확인"),
)

STOP_CONDITION_LABELS = dict(STOP_CONDITIONS)

# 규칙 §55 자동화 금지 원칙 — 코드 경로 자체가 없어야 하는 것들.
AUTOMATION_PROHIBITIONS: tuple[tuple[str, str], ...] = (
    ("auto_close", "AI 분석만으로 Issue 자동 Close"),
    ("auto_srs_link", "근거 없는 SRS 자동 연결"),
    ("auto_tc_delete", "TC 결과/이력 자동 삭제"),
    ("pass_without_means", "필수 관찰 수단 없이 Pass"),
    ("hide_search_failure", "문서 검색 실패를 숨긴 확정 판정"),
    ("claim_without_source", "Source 위치 없는 판단만 저장"),
    ("overwrite_tc", "QA 승인 없이 기존 TC 직접 덮어쓰기"),
    ("auto_expected", "Spec 근거 불충분 상태에서 자동 Expected 생성"),
)

AUTOMATION_PROHIBITION_LABELS = dict(AUTOMATION_PROHIBITIONS)

# "전수조사 완료" / "사양 없음" 처럼 조사 범위를 넘어선 확정 표현 (규칙 §8, §9.4).
OVERCLAIM_PATTERNS: tuple[tuple[str, str], ...] = (
    ("full_search_claim", r"전수\s*조사\s*완료"),
    ("no_spec_claim", r"사양\s*(?:이)?\s*없(?:음|다)"),
    ("all_verified_claim", r"모두\s*확인\s*(?:완료|했)"),
)


@dataclass(frozen=True)
class InferenceViolation:
    code: str
    label: str
    matched: str
    excerpt: str


def _excerpt(text: str, index: int, width: int = 60) -> str:
    start = max(index - width // 2, 0)
    return re.sub(r"\s+", " ", text[start : start + width]).strip()


def detect_forbidden_inferences(text: str, has_evidence: bool) -> list[InferenceViolation]:
    """규칙 §37: 근거 없이 이 주장을 했는지 검사한다.

    `has_evidence=True` 면(그 판정에 Source 가 붙어 있으면) 위반이 아니다 — 규칙이 금지한
    것은 이 주장 자체가 아니라 **근거 없는** 확정이다.
    """
    if has_evidence or not text:
        return []
    haystack = f" {text.casefold()} "
    violations: list[InferenceViolation] = []
    for inference in FORBIDDEN_INFERENCES:
        for keyword in inference.keywords:
            index = haystack.find(keyword.casefold())
            if index >= 0:
                violations.append(
                    InferenceViolation(code=inference.code, label=inference.label, matched=keyword, excerpt=_excerpt(text, max(index - 1, 0)))
                )
                break
    return violations


def detect_overclaims(text: str) -> list[InferenceViolation]:
    """규칙 §8·§9.4: 실제 조사 범위를 넘어서는 확정 표현을 잡는다."""
    if not text:
        return []
    violations: list[InferenceViolation] = []
    for code, pattern in OVERCLAIM_PATTERNS:
        match = re.search(pattern, text)
        if match:
            violations.append(InferenceViolation(code=code, label=match.group(0), matched=match.group(0), excerpt=_excerpt(text, match.start())))
    return violations
