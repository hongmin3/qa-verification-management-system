"""외부 API 호출 직전에 도는 마스킹 계층.

이 프로젝트의 전제는 "원본 문서는 서버 밖으로 나가지 않는다"이지만, 검색으로 추린 발췌는
나간다. 그 발췌에 환자 이름·병원명·IP·계정 같은 값이 섞여 있을 수 있다 — 사양서 예시,
Issue 재현 절차, 로그 인용에 실제로 들어간다.

RAG 를 쓴다고 외부 전송이 0이 되는 것은 아니므로, **나가는 문자열을 마지막에 한 번 더 훑는다.**

설계 원칙 두 가지.

1. **지우지 않고 자리표로 바꾼다.** `[PATIENT_ID]` 처럼 남겨 두면 모델이 "여기에 환자 ID 가
   있었다"는 구조는 알 수 있다. 통째로 지우면 문장이 깨져 판정이 틀어진다.
2. **QA 판단에 필요한 값은 살린다.** 제품 모델명, 버전, ErrorCode, DICOM Tag, Command 번호,
   SRS/Issue ID 는 마스킹 대상이 아니다. 이것들을 지우면 분석 자체가 불가능해진다.

무엇이 몇 건 바뀌었는지 보고하고, 그 요약은 감사 기록에 남는다. 마스킹은 조용히 하면
"왜 이 판정이 나왔는지" 되짚을 수 없다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 마스킹 대상이 **아닌** 것. 아래 패턴에 걸리는 토큰은 다른 규칙이 잡아도 되돌린다.
# QA 판단에 반드시 필요한 식별자들이다 (규칙 §10 exact 값, §9.1 조사 키).
#
# 네 자리 점 표기 버전(`1.1.0.001`)은 여기 두지 않는다 — 보호 대상으로 넣으면 같은 모양의
# IPv4(`10.13.0.222`)까지 함께 보호돼 IP 가 그대로 나간다. 버전과 IP 의 구분은 `ip` 규칙의
# `skip_if` 가 한다.
KEEP_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b[A-Z]{2,5}-\d{1,6}\b"),  # VP-6573, SRS-1234
    re.compile(r"\b0[xX][0-9A-Fa-f]+\b"),  # 0x1234
    re.compile(r"\(\s*[0-9A-Fa-f]{4}\s*,\s*[0-9A-Fa-f]{4}\s*\)"),  # DICOM Tag
    re.compile(r"\b[Vv]\d+(?:\.\d+)+(?:[A-Za-z]\d*)?\b"),  # V1.0.11 (v 접두사가 붙은 것만)
)


def _looks_like_version(value: str) -> bool:
    """`1.1.0.001` 처럼 **0으로 채운 자리**가 있으면 IP 가 아니라 버전으로 본다.

    네 자리 점 표기는 IPv4 와 제품 버전이 모양으로 구분되지 않는다. 실제 제품 버전은
    마지막 자리를 `001` 처럼 0으로 채우고 IP 는 그렇게 쓰지 않으므로 그 차이를 기준으로 쓴다.
    """
    return any(len(part) > 1 and part.startswith("0") for part in value.split("."))


@dataclass(frozen=True)
class MaskRule:
    code: str
    placeholder: str
    pattern: re.Pattern[str]
    #: 정규식 그룹 번호. 0이면 전체 일치를 바꾼다.
    group: int = 0
    #: 일치했지만 마스킹하지 않을 조건. 규칙별 예외 판정에 쓴다.
    skip_if: object = None


# 순서가 의미를 갖는다. 경로 안에 IP 가 들어 있는 경우(`\\10.13.0.5\qa\...`)가 있어 경로를
# 먼저 통째로 바꿔야 한다 — IP 를 먼저 지우면 경로 패턴이 깨져 사내 폴더 체계가 남는다.
MASK_RULES: tuple[MaskRule, ...] = (
    # 사내 절대 경로. 부서 폴더 체계가 그대로 나가지 않게 한다.
    MaskRule("unc_path", "[NETWORK_PATH]", re.compile(r"\\\\[\w.-]+(?:\\[^\s\\|]+)+")),
    MaskRule("local_path", "[LOCAL_PATH]", re.compile(r"\b[A-Za-z]:\\(?:[^\s\\|]+\\){1,}[^\s\\|]*")),
    # 이메일이 가장 확실한 신호다 (도메인에 병원명이 들어가는 경우도 함께 가려진다).
    MaskRule("email", "[EMAIL]", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    # IPv4. 각 옥텟을 0~255로 제한하고, 0으로 채운 자리가 있으면 버전으로 보고 건너뛴다.
    MaskRule(
        "ip",
        "[IP_ADDRESS]",
        re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\b"),
        skip_if=_looks_like_version,
    ),
    # 라벨이 붙은 값만 잡는다. 라벨 없는 사람 이름은 오탐이 커서 대상으로 두지 않는다.
    MaskRule("patient_id", "[PATIENT_ID]", re.compile(r"(?i)\b(?:patient\s*id|환자\s*(?:등록)?번호|PID)\s*[:=]?\s*([\w-]{2,32})"), group=1),
    MaskRule("patient_name", "[PATIENT_NAME]", re.compile(r"(?i)\b(?:patient\s*name|환자\s*(?:이름|성명))\s*[:=]?\s*([^\s,;|]{1,40})"), group=1),
    MaskRule("hospital", "[HOSPITAL]", re.compile(r"(?i)\b(?:hospital|institution|병원명|기관명)\s*[:=]?\s*([^\s,;|]{1,40})"), group=1),
    MaskRule("customer", "[CUSTOMER]", re.compile(r"(?i)\b(?:customer|고객명|거래처)\s*[:=]?\s*([^\s,;|]{1,40})"), group=1),
    MaskRule("serial", "[SERIAL]", re.compile(r"(?i)\b(?:s\/n|serial\s*(?:no\.?|number)?|시리얼)\s*[:=]?\s*([\w-]{4,40})"), group=1),
    MaskRule("account", "[ACCOUNT]", re.compile(r"(?i)\b(?:password|passwd|pw|비밀번호|암호)\s*[:=]\s*(\S{1,64})"), group=1),
    MaskRule("api_key", "[SECRET]", re.compile(r"(?i)\b(?:api[_-]?key|token|secret)\s*[:=]\s*(\S{8,120})"), group=1),
)


@dataclass
class MaskReport:
    """무엇이 몇 건 바뀌었는지. 값 자체는 담지 않는다 — 로그에 원본이 남으면 의미가 없다."""

    counts: dict[str, int] = field(default_factory=dict)
    chars_before: int = 0
    chars_after: int = 0

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    @property
    def applied(self) -> bool:
        return self.total > 0

    def as_dict(self) -> dict:
        return {
            "applied": self.applied,
            "total": self.total,
            "counts": dict(sorted(self.counts.items())),
            "chars_before": self.chars_before,
            "chars_after": self.chars_after,
        }


def _keep_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for pattern in KEEP_PATTERNS:
        spans.extend(match.span() for match in pattern.finditer(text))
    return spans


def _overlaps(span: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    return any(span[0] < end and start < span[1] for start, end in spans)


def mask_text(text: str, rules: tuple[MaskRule, ...] = MASK_RULES) -> tuple[str, MaskReport]:
    """문자열 하나를 마스킹한다. QA 판단에 필요한 식별자는 건드리지 않는다."""
    report = MaskReport(chars_before=len(text))
    if not text:
        report.chars_after = 0
        return text, report

    result = text
    for rule in rules:
        keep = _keep_spans(result)
        pieces: list[str] = []
        cursor = 0
        changed = 0
        for match in rule.pattern.finditer(result):
            start, end = match.span(rule.group)
            if start < 0 or _overlaps((start, end), keep):
                continue
            if rule.skip_if is not None and rule.skip_if(result[start:end]):
                continue
            pieces.append(result[cursor:start])
            pieces.append(rule.placeholder)
            cursor = end
            changed += 1
        if changed:
            pieces.append(result[cursor:])
            result = "".join(pieces)
            report.counts[rule.code] = report.counts.get(rule.code, 0) + changed
    report.chars_after = len(result)
    return result, report


def mask_payload(payload: object, rules: tuple[MaskRule, ...] = MASK_RULES) -> tuple[object, MaskReport]:
    """dict/list/str 이 섞인 구조를 재귀적으로 마스킹한다. 키는 건드리지 않는다."""
    total = MaskReport()

    def walk(node: object) -> object:
        if isinstance(node, str):
            masked, report = mask_text(node, rules)
            total.chars_before += report.chars_before
            total.chars_after += report.chars_after
            for code, count in report.counts.items():
                total.counts[code] = total.counts.get(code, 0) + count
            return masked
        if isinstance(node, dict):
            return {key: walk(value) for key, value in node.items()}
        if isinstance(node, list):
            return [walk(value) for value in node]
        return node

    return walk(payload), total
