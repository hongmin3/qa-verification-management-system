"""고친 뒤의 문장에 남은 편집 잔재를 AI 없이 찾는다 (REQ-MANUAL-020).

QA 작성 규칙 가이드 S07 의 "변경 후 중복/잔재/번호 오류/문장 병합 오류" 확인을 돕는다.
번호 오류는 보지 않는다 — Word 자동 번호는 문서 글자에 들어 있지 않다.

화면 검증(`reviewer.py`)과 Claude 대화 검증(`local_review.py`)이 같은 함수를 쓴다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.modules.manual_review.review_signals import is_image_change

EXCERPT_LIMIT = 40
MIN_DUPLICATE_PHRASE = 8

LABELS = {
    "DUPLICATE_WORD": "같은 단어 반복",
    "DUPLICATE_PHRASE": "추가 문구 중복",
    "MERGED_SENTENCE": "문장 붙음",
    "DOUBLE_PUNCTUATION": "문장부호 겹침",
    "DOUBLE_SPACE": "공백 겹침",
}

# 두 글자 이상인 같은 단어(한글·영문)가 공백을 사이에 두고 바로 이어진다. 앞뒤가 다른 글자로 이어지면
# 단어 일부이므로 보지 않는다.
_DUPLICATE_WORD_RE = re.compile(r"(?<![0-9A-Za-z가-힣])([A-Za-z]{2,}|[가-힣]{2,})\s+\1(?![0-9A-Za-z가-힣])", re.IGNORECASE)
# 마침표 바로 뒤에 공백 없이 다음 문장이 이어진다. "다.다음" 과 "screen.Click" 만 본다 — 버전 숫자
# (1.1.0), 파일 이름(config.yaml), 약어(e.g.)를 잘못 잡지 않기 위해 범위를 좁혔다.
_MERGED_SENTENCE_RE = re.compile(r"(다\.[가-힣]|[a-z]\.[A-Z][a-z])")
# 쉼표 두 개, 마침표 정확히 두 개(말줄임 ... 은 제외), 공백 뒤에 바로 오는 쉼표·마침표(숫자 앞 .5 와
# 공백 뒤 말줄임 ... 은 제외).
_DOUBLE_PUNCTUATION_RE = re.compile(r",,|(?<!\.)\.\.(?!\.)|\s[,.](?![\d.])")
_DOUBLE_SPACE_RE = re.compile(r"(?<=\S) {2,}(?=\S)")


def _excerpt(text: str, start: int, end: int) -> str:
    room = max(0, EXCERPT_LIMIT - (end - start))
    left = max(0, start - room // 2)
    right = min(len(text), left + max(EXCERPT_LIMIT, end - start))
    return text[left:right].strip()[:EXCERPT_LIMIT]


def _issue(code: str, text: str, start: int, end: int) -> dict:
    return {"code": code, "label": LABELS[code], "excerpt": _excerpt(text, start, end)}


def paragraph_issues(paragraph_text: str) -> list[dict]:
    """문단 하나의 최종 문장에서 문단 규칙(추가 문구 중복을 뺀 네 가지)에 걸린 곳을 찾는다.
    같은 규칙은 문단마다 처음 걸린 곳 하나만 알린다."""
    text = paragraph_text or ""
    issues: list[dict] = []
    for code, pattern in (
        ("DUPLICATE_WORD", _DUPLICATE_WORD_RE),
        ("MERGED_SENTENCE", _MERGED_SENTENCE_RE),
        ("DOUBLE_PUNCTUATION", _DOUBLE_PUNCTUATION_RE),
        ("DOUBLE_SPACE", _DOUBLE_SPACE_RE),
    ):
        match = pattern.search(text)
        if match:
            issues.append(_issue(code, text, match.start(), match.end()))
    return issues


def duplicate_phrase_issue(kind: str, change_text: str, paragraph_text: str) -> dict | None:
    """이번에 추가한 글자가 최종 문장에 두 번 이상 나오면 알린다 (추가·이동 도착 변경만).

    한 단어짜리 추가("Exposure")는 정상 문장에도 두 번 나오므로 두 단어 이상인 문구만 본다."""
    if kind not in ("insertion", "move_to"):
        return None
    phrase = (change_text or "").strip()
    text = paragraph_text or ""
    if len(phrase) < MIN_DUPLICATE_PHRASE or len(phrase.split()) < 2 or text.count(phrase) < 2:
        return None
    start = text.index(phrase)
    return _issue("DUPLICATE_PHRASE", text, start, start + len(phrase))


def _checkable(kind: str, source_page) -> bool:
    return not source_page and not kind.startswith("pdf_") and not is_image_change(kind)


def text_issues_by_change(changes: Sequence[tuple[object, str, str, str, int, object, bool]]) -> dict[object, list[dict]]:
    """변경 목록의 문장 점검 결과를 변경 키별로 돌려준다.

    `changes` 의 줄은 (키, 종류, 변경 글자, 최종 문장, 문단 번호, PDF 페이지, 분석 대상 여부)다.
    문단 규칙 결과는 그 문단의 첫 분석 대상 변경에만 붙이고, 추가 문구 중복은 그 추가 변경에 붙인다
    (REQ-MANUAL-020 4번). 분석 대상이 아닌 변경에는 붙이지 않는다.
    """
    result: dict[object, list[dict]] = {}
    seen_paragraphs: set[int] = set()
    for key, kind, text, paragraph_text, paragraph_index, source_page, functional in changes:
        if not functional or not _checkable(kind, source_page):
            continue
        issues: list[dict] = []
        if paragraph_index not in seen_paragraphs:
            seen_paragraphs.add(paragraph_index)
            issues.extend(paragraph_issues(paragraph_text))
        phrase = duplicate_phrase_issue(kind, text, paragraph_text)
        if phrase:
            issues.append(phrase)
        if issues:
            result[key] = issues
    return result
