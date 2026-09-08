"""Evidence Store (로드맵 §12, 규칙 §8 근거 수준 · §9.3 근거 위치).

모든 판정에 Source 를 붙인다. 규칙 §55 가 "Source 위치 없는 판단만 저장"을 금지하기 때문에
근거를 **판정과 같은 레코드에** 두고, QA 가 결과에서 원 근거로 바로 갈 수 있게 위치까지
남긴다 (파일명 → 절/Sheet/행/page).

근거 수준(§7 정보 우선순위 · §8 근거 수준)을 필드로 둔다. "연구소 Comment 는 탐색 근거이지
단독 Expected 근거가 아니다"처럼 **어떤 근거로 무엇을 확정할 수 있는지**가 규칙에 정해져
있어서, 수준을 잃으면 그 규칙을 적용할 수 없다.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

# --- 근거 수준 (규칙 §7 정보 우선순위) ----------------------------------------
# 숫자가 작을수록 강한 근거다. Expected 확정에 쓸 수 있는 하한이 규칙으로 정해져 있다.

LEVEL_USER_STATEMENT = 1  # 현재 요청에서 사용자가 명시한 신규·변경·삭제 사양
LEVEL_EXECUTION_RESULT = 2  # 실제 수행 절차·캡처·검증 결과
LEVEL_SPECIFICATION = 3  # 최신 유효 사양서
LEVEL_MANUAL = 4  # 매뉴얼 / Protocol / Integration 문서
LEVEL_EXISTING_TC = 5  # 기존 TC
LEVEL_LEGACY = 6  # 이전 버전 문서
LEVEL_HINT = 9  # 연구소 Comment/Resolution — 탐색 근거이지 Expected 근거가 아니다

LEVEL_LABELS = {
    LEVEL_USER_STATEMENT: "사용자 명시 사양",
    LEVEL_EXECUTION_RESULT: "실제 수행 결과",
    LEVEL_SPECIFICATION: "최신 유효 사양서",
    LEVEL_MANUAL: "매뉴얼/Protocol",
    LEVEL_EXISTING_TC: "기존 TC",
    LEVEL_LEGACY: "이전 버전 문서",
    LEVEL_HINT: "탐색 단서 (Expected 근거 아님)",
}

#: 이 수준까지만 Expected Result 를 확정할 수 있다 (규칙 §7).
EXPECTED_EVIDENCE_MAX_LEVEL = LEVEL_MANUAL

KIND_SPECIFICATION = "specification"
KIND_TESTCASE = "testcase"
KIND_MANUAL = "manual"
KIND_ISSUE = "issue"
KIND_RULE = "rule"

KIND_TO_LEVEL = {
    KIND_SPECIFICATION: LEVEL_SPECIFICATION,
    KIND_MANUAL: LEVEL_MANUAL,
    KIND_TESTCASE: LEVEL_EXISTING_TC,
    KIND_ISSUE: LEVEL_HINT,
    KIND_RULE: LEVEL_SPECIFICATION,
}


@dataclass
class Evidence:
    """근거 하나. `locator` 는 사람이 원문에서 찾아갈 수 있는 위치여야 한다 (규칙 §9.3)."""

    kind: str
    #: 검색·교차검증에 쓰는 식별자 (사양 Chunk ID, TC ID, Issue ID 등).
    ref: str
    document: str = ""
    locator: str = ""
    excerpt: str = ""
    level: int = LEVEL_SPECIFICATION
    #: 왜 이 근거가 뽑혔는지 ("'AP-5500' 정확 일치" / "용어 유사도 0.42").
    match_reason: str = ""

    @property
    def level_label(self) -> str:
        return LEVEL_LABELS.get(self.level, str(self.level))

    @property
    def can_support_expected(self) -> bool:
        """이 근거로 Expected Result 를 확정해도 되는지 (규칙 §7)."""
        return self.level <= EXPECTED_EVIDENCE_MAX_LEVEL

    @property
    def citation(self) -> str:
        """화면·보고서에 그대로 쓰는 인용 문구."""
        parts = [part for part in (self.document, self.locator) if part]
        return " · ".join(parts) if parts else self.ref

    def as_dict(self) -> dict:
        payload = asdict(self)
        payload["level_label"] = self.level_label
        payload["citation"] = self.citation
        return payload


@dataclass
class Claim:
    """근거가 붙은 판정 하나. 이 단위로 저장되고 QA 가 승인·반박한다."""

    #: `tc_coverage` / `regression` / `issue_analysis` / `spec_trace`
    kind: str
    #: 화면에서 이 판정을 가리키는 이름 (TC ID, Regression 축 등).
    label: str
    text: str
    evidence: list[Evidence] = field(default_factory=list)
    confidence: float = 0.0
    #: 규칙 §36 표준 표현 중 해당하는 것.
    findings: list[str] = field(default_factory=list)

    @property
    def has_evidence(self) -> bool:
        return bool(self.evidence)

    @property
    def can_support_expected(self) -> bool:
        return any(item.can_support_expected for item in self.evidence)

    @property
    def strongest_level(self) -> int:
        return min((item.level for item in self.evidence), default=LEVEL_HINT)

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "label": self.label,
            "text": self.text,
            "confidence": self.confidence,
            "findings": list(self.findings),
            "evidence": [item.as_dict() for item in self.evidence],
            "strongest_level": self.strongest_level,
            "can_support_expected": self.can_support_expected,
        }

    def for_cross_check(self) -> dict:
        """G5 Cross-check 가 검사하는 형태로 (gates.evaluate_g5 의 입력)."""
        return {"kind": self.kind, "tc_id": self.label, "text": self.text, "evidence": [item.ref for item in self.evidence]}


@dataclass
class EvidenceStore:
    """한 분석에서 만든 근거·판정 전체."""

    claims: list[Claim] = field(default_factory=list)

    def add(self, claim: Claim) -> Claim:
        self.claims.append(claim)
        return claim

    def of_kind(self, kind: str) -> list[Claim]:
        return [claim for claim in self.claims if claim.kind == kind]

    @property
    def unsourced(self) -> list[Claim]:
        """근거가 없는 판정. 규칙 §55 위반 후보라 화면에서 눈에 띄어야 한다."""
        return [claim for claim in self.claims if not claim.has_evidence]

    def summary(self) -> dict:
        by_level: dict[str, int] = {}
        for claim in self.claims:
            for item in claim.evidence:
                by_level[item.level_label] = by_level.get(item.level_label, 0) + 1
        return {
            "claims": len(self.claims),
            "unsourced": len(self.unsourced),
            "evidence_by_level": by_level,
        }

    def as_dict(self) -> dict:
        return {"claims": [claim.as_dict() for claim in self.claims], "summary": self.summary()}


def specification_evidence(chunk_id: str, document: str, page: int | str, heading: str, excerpt: str, match_reason: str = "") -> Evidence:
    """사양 Chunk → Evidence. 위치는 `p.12 · 4.3.2 로그인` 형태로 만든다."""
    locator_parts = [f"p.{page}" if page not in (None, "", 0) else "", heading]
    return Evidence(
        kind=KIND_SPECIFICATION,
        ref=chunk_id,
        document=document,
        locator=" · ".join(part for part in locator_parts if part),
        excerpt=excerpt[:400],
        level=LEVEL_SPECIFICATION,
        match_reason=match_reason,
    )


def testcase_evidence(tc_id: str, workbook: str, sheet: str, row: int | str, excerpt: str, match_reason: str = "") -> Evidence:
    """TC → Evidence. 규칙 §9.3 이 "Sheet명 + 실제 행 번호"를 우선하라고 정하고 있다."""
    return Evidence(
        kind=KIND_TESTCASE,
        ref=tc_id,
        document=workbook,
        locator=f"{sheet} · {row}행" if sheet else (f"{row}행" if row else ""),
        excerpt=excerpt[:400],
        level=LEVEL_EXISTING_TC,
        match_reason=match_reason,
    )


def issue_evidence(issue_id: str, field_name: str, excerpt: str) -> Evidence:
    """Issue 본문 → Evidence. 연구소 Comment/Resolution 은 탐색 단서 수준이다 (규칙 §7)."""
    return Evidence(
        kind=KIND_ISSUE,
        ref=issue_id,
        document=f"Issue {issue_id}",
        locator=field_name,
        excerpt=excerpt[:400],
        level=LEVEL_HINT,
        match_reason="Issue 본문",
    )


def rule_evidence(anchor: str, title: str, excerpt: str) -> Evidence:
    """QA 규칙 절 → Evidence. 어떤 규칙에 따라 이렇게 판정했는지 남긴다."""
    return Evidence(
        kind=KIND_RULE,
        ref=anchor,
        document=anchor.split("#", 1)[0],
        locator=title,
        excerpt=excerpt[:400],
        level=LEVEL_SPECIFICATION,
        match_reason="규칙 근거",
    )
