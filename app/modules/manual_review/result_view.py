"""결과 화면의 할 일 묶음 규칙 (REQ-MANUAL-013).

결과 화면과 QA 확정 JSON 답(REQ-MANUAL-014)이 같은 규칙을 쓰도록 한 곳에 둔다.
"""

from __future__ import annotations

from app.modules.manual_review.schemas import JUDGMENT_LABELS_KO, ManualJudgment

BUCKET_REQUEST = "request"
BUCKET_CHECK = "check"
BUCKET_PASS = "pass"
BUCKET_SIMPLE = "simple"
BUCKET_ORDER = (BUCKET_REQUEST, BUCKET_CHECK, BUCKET_PASS, BUCKET_SIMPLE)
BUCKET_LABELS = {
    BUCKET_REQUEST: "연구소 요청",
    BUCKET_CHECK: "QA 확인",
    BUCKET_PASS: "문제없음",
    BUCKET_SIMPLE: "단순 변경",
}

REQUEST_DECISIONS = {
    ManualJudgment.SUPPLEMENT_REQUIRED.value,
    ManualJudgment.MODIFICATION_REQUIRED.value,
    ManualJudgment.DELETE_REQUIRED.value,
    ManualJudgment.MISSING_SUSPECTED.value,
}
CHECK_DECISIONS = {
    ManualJudgment.SPEC_CONFIRMATION_REQUIRED.value,
    ManualJudgment.VERSION_CONFIRMATION_REQUIRED.value,
    ManualJudgment.UNABLE_TO_DETERMINE.value,
}

KIND_LABELS = {
    "insertion": "추가",
    "deletion": "삭제",
    "move_from": "이동(출발)",
    "move_to": "이동(도착)",
    "pdf_addition": "PDF 추가",
    "pdf_deletion": "PDF 삭제",
    "pdf_modification": "PDF 수정",
    "pdf_image_change": "PDF 이미지 변경",
}


def kind_label(kind: str) -> str:
    if kind.startswith("image_"):
        return f"이미지 변경({KIND_LABELS.get(kind.removeprefix('image_'), kind)})"
    return KIND_LABELS.get(kind, kind)


def judgment_label(value: str | None) -> str:
    return JUDGMENT_LABELS_KO.get(value, value or "-") if value else "-"


def _judgment(change: dict) -> dict:
    return change.get("ai_judgment") or {}


def text_issues(change: dict) -> list[dict]:
    return list(_judgment(change).get("text_issues") or [])


def needs_human_review(change: dict) -> bool:
    return bool(_judgment(change).get("needs_human_review") or change.get("review_required"))


def bucket_for(change: dict) -> str:
    """변경 한 건의 할 일 묶음. QA 확정이면 QA 판정만으로 정한다 (REQ-MANUAL-013 표)."""
    if not change.get("functional"):
        return BUCKET_SIMPLE
    qa_decision = change.get("qa_decision")
    if qa_decision:
        if qa_decision in REQUEST_DECISIONS:
            return BUCKET_REQUEST
        if qa_decision in CHECK_DECISIONS:
            return BUCKET_CHECK
        return BUCKET_PASS
    decision = change.get("decision")
    if decision in REQUEST_DECISIONS or text_issues(change):
        return BUCKET_REQUEST
    if not decision or decision in CHECK_DECISIONS or needs_human_review(change):
        return BUCKET_CHECK
    return BUCKET_PASS


def bucket_counts(changes: list[dict]) -> dict[str, int]:
    counts = {bucket: 0 for bucket in BUCKET_ORDER}
    for change in changes:
        counts[bucket_for(change)] += 1
    return counts


def progress(changes: list[dict]) -> dict[str, int]:
    """QA 확정 건수와 분석 대상 건수."""
    functional = [change for change in changes if change.get("functional")]
    return {"confirmed": sum(1 for change in functional if change.get("qa_decision")), "functional": len(functional)}


def default_bucket(counts: dict[str, int]) -> str:
    """처음 열 때 선택할 탭: 건수가 있는 첫 묶음(연구소 요청 → QA 확인 → 문제없음)."""
    for bucket in (BUCKET_REQUEST, BUCKET_CHECK, BUCKET_PASS):
        if counts.get(bucket):
            return bucket
    return BUCKET_SIMPLE if counts.get(BUCKET_SIMPLE) else BUCKET_REQUEST


def location(change: dict) -> str:
    """카드의 위치 표시. PDF 면 페이지, 아니면 절 제목과 문단 번호."""
    if change.get("source_page"):
        return f"PDF 페이지 {change['source_page']}"
    heading = (change.get("section_heading") or "").strip()
    paragraph = f"문단 #{change.get('paragraph_index', 0)}"
    return f"{heading} · {paragraph}" if heading else paragraph


def ordered_changes(changes: list[dict]) -> list[dict]:
    """묶음 순서, 그 안에서 문단 번호 순서."""
    rank = {bucket: index for index, bucket in enumerate(BUCKET_ORDER)}
    return sorted(changes, key=lambda change: (rank[bucket_for(change)], change.get("paragraph_index") or 0, change.get("id") or 0))


def summary_payload(changes: list[dict]) -> dict:
    """QA 확정 JSON 답에 붙이는 묶음 건수와 진행 상황."""
    return {"bucket_counts": bucket_counts(changes), **progress(changes)}
