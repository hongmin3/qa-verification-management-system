"""AI 판정을 저장하기 직전에 붙이는 사람 확인 신호 (REQ-MANUAL-012).

화면 검증(`reviewer.py`)과 Claude 대화 검증(`scripts/manual_review_local.py`)이 같은 규칙을
쓰도록 한곳에 둔다.
"""

from __future__ import annotations

from app.modules.manual_review.schemas import ManualChangeJudgment, ManualJudgment

IMAGE_REASON = "IMAGE_CHANGE_REVIEW_REQUIRED"
PDF_DIFF_REASON = "PDF_DIFF_REVIEW_REQUIRED"
DESIGN_REVIEW_FAILED_REASON = "DESIGN_REVIEW_FAILED"
REVIEW_CONFIDENCE_CAP = 0.6

IMAGE_PASS_PROBLEM = "이미지 변경은 글자만으로 판단할 수 없어 문제없음 판정을 판정 불가로 바꿨습니다. QA가 원본 이미지를 직접 확인해야 합니다."
IMAGE_PASS_COMMENT = "이미지 변경입니다. 바뀐 그림이 최신 사양과 맞는지 확인이 필요합니다."


def is_image_change(kind: str) -> bool:
    return "image" in (kind or "")


def apply_human_review_signals(
    judgment: ManualChangeJudgment,
    kind: str,
    review_required: bool,
    release_context: list[dict] | None = None,
) -> ManualChangeJudgment:
    """판정에 사람 확인 신호를 더한다. 같은 신호는 두 번 넣지 않는다.

    - 짝지은 설계검토 항목이 FAIL이면 사람 검토 필요와 `DESIGN_REVIEW_FAILED`.
    - 사람 검토가 필요한 변경(그림, PDF)은 확신도를 0.6 이하로 낮추고 신호를 붙인다.
    - 그림 변경을 문제없음으로 판정했으면 판정 불가로 바꾼다. 원래 판정은
      `ai_original_decision`에 남긴다 (OPEN_QUESTIONS 8-3).
    """
    if any(item.get("result_status") == "FAIL" for item in release_context or []):
        judgment.needs_human_review = True
        if DESIGN_REVIEW_FAILED_REASON not in judgment.reason_codes:
            judgment.reason_codes.append(DESIGN_REVIEW_FAILED_REASON)
    if review_required:
        judgment.confidence = min(judgment.confidence, REVIEW_CONFIDENCE_CAP)
        judgment.needs_human_review = True
        reason = IMAGE_REASON if is_image_change(kind) else PDF_DIFF_REASON
        if reason not in judgment.reason_codes:
            judgment.reason_codes.append(reason)
    if is_image_change(kind) and judgment.decision == ManualJudgment.PASS:
        judgment.ai_original_decision = ManualJudgment.PASS
        judgment.decision = ManualJudgment.UNABLE_TO_DETERMINE
        judgment.needs_human_review = True
        judgment.confidence = min(judgment.confidence, REVIEW_CONFIDENCE_CAP)
        if IMAGE_REASON not in judgment.reason_codes:
            judgment.reason_codes.append(IMAGE_REASON)
        if not judgment.problem.strip():
            judgment.problem = IMAGE_PASS_PROBLEM
        if not judgment.qa_comment.strip():
            judgment.qa_comment = IMAGE_PASS_COMMENT
    return judgment
