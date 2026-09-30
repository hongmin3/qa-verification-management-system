from __future__ import annotations

import re

from app.modules.impact_analyzer.schemas import ChangeAnalysis
from app.retrieval.bm25_retriever import BM25Retriever

#: 변경 기능 줄을 고르는 낱말. 영어 변경 문서도 변경 기능이 비지 않게 영어 낱말을 함께 둔다 (SPEC 13.1-1).
#: 영어는 낱말 단위로만 맞춘다. "Address" 의 "Add", "Prefix" 의 "fix" 는 변경 낱말이 아니다.
CHANGE_WORDS = re.compile(
    r"변경|추가|개선|수정|지원"
    r"|\b(?:chang(?:e|es|ed|ing)|add(?:s|ed|ing)?|improv(?:e|es|ed|ing|ement|ements)"
    r"|fix(?:es|ed|ing)?|support(?:s|ed|ing)?|updat(?:e|es|ed|ing)|modif(?:y|ies|ied|ying|ication|ications))\b",
    re.I,
)

RISK_WORDS = ("저장", "설정", "호환", "마이그레이션", "DICOM", "인터페이스", "삭제", "변환", "workflow", "database", "UI")


def trim_by_relevance(text: str, query: str, top_k: int = 40) -> str:
    """변경 문서가 크고 사용자 요청(query)이 있을 때, BM25로 관련성 높은 줄만 추려 Gemini에
    보낼 텍스트 양(=토큰)을 줄인다. 이미 top_k줄 이하로 짧으면 원문을 그대로 둔다.

    원본 등장 순서를 유지해 문맥이 뒤섞이지 않게 한다.
    """
    if not text or not query:
        return text
    lines = [line for line in text.splitlines() if len(line.strip()) > 2]
    if len(lines) <= top_k:
        return text
    indexed = list(enumerate(lines))
    results = BM25Retriever(indexed, lambda item: item[1]).search(query, top_k)
    kept_indices = sorted(index for (index, _line), _score in results)
    return "\n".join(lines[index] for index in kept_indices)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _new_or_changed_lines(text: str, baseline_text: str) -> list[str]:
    """기준 사양서 텍스트에 없는(신규 또는 변경된) 줄만 남긴다.

    줄 단위 exact match 대신 공백을 제거한 값이 기준본 전체에 부분 문자열로
    존재하는지 확인한다. PDF/Word 추출 시 줄바꿈 위치가 리비전마다 달라질 수
    있어, 순서를 고려하는 diff보다 이 방식이 오탐을 줄인다.
    """
    baseline_blob = _normalize(baseline_text)
    result = []
    for raw_line in text.splitlines():
        line = raw_line.strip(" -\t")
        if len(line) <= 2:
            continue
        key = _normalize(line)
        if key and key in baseline_blob:
            continue
        result.append(line)
    return result


def analyze_change_rules(text: str, baseline_text: str | None = None, user_notes: str = "") -> ChangeAnalysis:
    if not text:
        lines = []
    elif baseline_text:
        lines = _new_or_changed_lines(text, baseline_text)
    else:
        lines = [line.strip(" -\t") for line in text.splitlines() if len(line.strip()) > 2]
    note_lines = [line.strip(" -\t") for line in user_notes.splitlines() if len(line.strip()) > 2]
    combined_lines = note_lines + lines
    keyword_source = "\n".join(combined_lines) if (baseline_text or note_lines) else text
    keywords = [word for word in RISK_WORDS if word.lower() in keyword_source.lower()]
    # 사용자가 직접 입력한 요청은 이미 변경사항으로 명시된 것이므로 키워드 필터 없이 그대로 포함한다.
    features = list(note_lines)
    for line in lines:
        if CHANGE_WORDS.search(line):
            features.append(line[:200])
    return ChangeAnalysis(
        user_notes=user_notes,
        changed_features=features[:20], purpose="; ".join(features[:3]),
        ui_changes=[line for line in combined_lines if re.search(r"UI|화면|버튼|표시", line, re.I)][:10],
        interface_changes=[line for line in combined_lines if re.search(r"API|interface|연동", line, re.I)][:10],
        dicom_changes=[line for line in combined_lines if "dicom" in line.lower()][:10],
        workflow_changes=[line for line in combined_lines if re.search(r"workflow|흐름|절차", line, re.I)][:10],
        configuration_changes=[line for line in combined_lines if re.search(r"설정|config", line, re.I)][:10],
        stored_data_changes=[line for line in combined_lines if re.search(r"저장|database|DB", line, re.I)][:10],
        compatibility_changes=[line for line in combined_lines if re.search(r"호환|compatib", line, re.I)][:10],
        risk_keywords=keywords,
    )
