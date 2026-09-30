"""검증 완료된 리비전에서 문제로 판정된(PASS가 아닌) 변경사항마다 원본 DOCX에 Word Comment를
삽입한 새 파일을 만든다 (스펙 §25).

python-docx의 `Paragraph.runs`/`.text`는 `<w:ins>/<w:del>/<w:moveFrom>/<w:moveTo>`로 감싸진
run을 찾지 못하므로(직계 자식 `<w:r>`만 봄), 이 모듈은 lxml로 문단 안의 모든 `<w:r>`을 직접
찾아 `Run` 객체로 감싼 뒤 `Document.add_comment(...)`에 넘긴다.

각 TrackedChange는 문단 인덱스(paragraph_index)와 그 문단 안에서 몇 번째 <w:ins>/<w:del>/
<w:moveFrom>/<w:moveTo>인지(change_index_in_paragraph)를 함께 기록한다(2026-09-02). Comment는
그 특정 변경 요소의 run 범위에만 앵커링된다 — 문단에 변경이 여러 건이어도 서로 다른 위치에
Comment가 달린다. 원본 문서가 바뀌어 해당 인덱스의 요소를 더 이상 찾을 수 없으면(예:
paragraph_index는 유효하지만 그 안의 변경 개수가 줄어든 경우) 스펙 §25가 허용하는 fallback으로
"해당 문단 전체"에 앵커링한다.

원본 Track Changes와 기존 연구소 Comment는 전혀 수정하지 않는다 — 새 Comment만 추가한다.
"""

from __future__ import annotations

import re
from pathlib import Path

from docx import Document
from docx.text.paragraph import Paragraph
from docx.text.run import Run

from app.modules.manual_review.schemas import ManualJudgment

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
DEFAULT_AUTHOR = "QA AI"  # 제품명은 호출자(router)가 revision["product"]로 조립해 넘긴다 — 특정 제품에 고정하지 않는다

_UNSAFE_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]')


def output_filename(manual_name: str, revision_label: str) -> str:
    """스펙 §25 예시(예: 'VXvue Service Manual.V1.1.0W2_KO_AI검토.docx')와 같은 형태로 만든다.

    화면이 만드는 리비전 표기는 "V1.1.0 · W2"이므로 공백과 가운뎃점을 함께 지운다 (REQ-MANUAL-016)."""
    label = revision_label.replace(" ", "").replace("·", "")
    name = f"{manual_name}.{label}_KO_AI검토.docx"
    return _UNSAFE_FILENAME_CHARS.sub("_", name)


def _paragraph_run_elements(paragraph_element) -> list:
    """문단 안의 모든 <w:r>을 문서 순서대로 찾는다 (ins/del/moveFrom/moveTo 내부 포함)."""
    return list(paragraph_element.iter(f"{W_NS}r"))


_TRACKED_CHANGE_TAGS = (f"{W_NS}ins", f"{W_NS}del", f"{W_NS}moveFrom", f"{W_NS}moveTo")


def _nth_tracked_change_element(paragraph_element, index: int):
    """문단의 직계 자식 중 index번째 <w:ins>/<w:del>/<w:moveFrom>/<w:moveTo>를 찾는다.

    docx_track_changes.py가 추출 시점에 같은 원본 파일·같은 문단 안에서 똑같은 순서로 센
    change_index_in_paragraph와 짝을 맞춰야 한다."""
    count = 0
    for child in paragraph_element:
        if child.tag in _TRACKED_CHANGE_TAGS:
            if count == index:
                return child
            count += 1
    return None


def comment_text_for(change: dict) -> str | None:
    """QA가 재판정했으면 그 판정을, 아니면 AI 판정을 기준으로 Comment 문구를 만든다.
    PASS(문제없음)인 변경에는 Comment를 달지 않는다 (None 반환)."""
    decision = change.get("qa_decision") or change.get("decision")
    if not decision or decision == ManualJudgment.PASS.value:
        return None
    judgment = change.get("ai_judgment") or {}
    parts = []
    qa_comment = (judgment.get("qa_comment") or "").strip()
    if qa_comment:
        parts.append(qa_comment)
    note = (change.get("qa_note") or "").strip()
    if note:
        parts.append(f"(QA 메모: {note})")
    if not parts:
        parts.append(f"[{decision}] 검토가 필요합니다.")
    return " ".join(parts)


def insert_comments(revision_path: Path, changes: list[dict], output_path: Path, author: str = DEFAULT_AUTHOR) -> int:
    """문제로 판정된 변경마다 Comment를 넣은 사본을 만들고 넣은 개수를 돌려준다."""
    return len(insert_comments_detailed(revision_path, changes, output_path, author=author))


def insert_comments_detailed(revision_path: Path, changes: list[dict], output_path: Path, author: str = DEFAULT_AUTHOR) -> list[dict]:
    """문제로 판정된 변경마다 원본 위치(문단 단위)에 Comment를 삽입해 output_path에 저장한다.

    반환값은 실제로 넣은 Comment 목록(`{"change": 변경, "text": 문구}`). 이전 회차 지적사항으로
    저장할 때 쓴다 (REQ-MANUAL-015). 해당 문단을 찾지 못하거나(예: 문서가 바뀌어
    paragraph_index가 더 이상 유효하지 않음) 문단에 run이 전혀 없으면 그 변경은 조용히
    건너뛴다 — 전체 삽입을 실패시키지 않는다 (스펙 §38 부분 실패 허용 원칙과 동일).
    """
    document = Document(str(revision_path))
    paragraph_elements = list(document.element.body.iter(f"{W_NS}p"))

    inserted: list[dict] = []
    for change in changes:
        if not change.get("functional"):
            continue
        text = comment_text_for(change)
        if not text:
            continue
        index = change.get("paragraph_index")
        if index is None or not (0 <= index < len(paragraph_elements)):
            continue
        paragraph_element = paragraph_elements[index]
        change_element = _nth_tracked_change_element(paragraph_element, change.get("change_index_in_paragraph") or 0)
        run_elements = _paragraph_run_elements(change_element) if change_element is not None else []
        if not run_elements:
            # 특정 변경 요소를 못 찾으면(원본이 바뀌었거나 인덱스가 없던 옛 데이터) 문단
            # 전체로 fallback한다 — 스펙 §25가 허용하는 방식.
            run_elements = _paragraph_run_elements(paragraph_element)
        if not run_elements:
            continue
        paragraph = Paragraph(paragraph_element, document)
        anchor_runs = [Run(run_elements[0], paragraph), Run(run_elements[-1], paragraph)]
        document.add_comment(anchor_runs, text=text, author=author, initials="QA")
        inserted.append({"change": change, "text": text})

    output_path.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(output_path))
    return inserted
