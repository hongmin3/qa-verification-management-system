"""Gate 엔진 (규칙 §44 Agentic Workflow Gate, 로드맵 Layer 5).

Gate 를 **프롬프트 안이 아니라 코드로** 판정한다. 이유가 두 가지다.

1. **토큰.** Gate 가 막으면 LLM 을 부르지 않는다. 근거가 부족한 Issue 는 API 호출 0회로
   "무엇이 없어서 못 한다"만 돌려준다. 규칙 §53 최종 중단 조건이 정확히 이 경우다.
2. **재현성.** "근거가 충분한가"를 LLM 에 물으면 같은 입력에 다른 답이 나온다. 있는지
   없는지는 세면 되는 문제다.

Gate 결과는 판정과 **차단 이유를 함께** 데이터로 남긴다 (로드맵 §11). Gate 가 막았다는
사실이 결과에 남지 않으면, 규칙 §55 가 금지한 "문서 검색 실패를 숨긴 확정 판정"이 된다.

    G1 Source Completeness   — Issue·문서·TC·버전이 갖춰졌는가          (LLM 전)
    G2 Specification Evidence — 공식 사양 근거를 찾았는가                (LLM 전)
    G3 TC & Root Cause Coverage — 기존 TC 가 Root Cause 를 덮는가        (LLM 후)
    G4 Execution Feasibility  — 실제로 수행할 수 있는가                  (LLM 전)
    G5 Cross-check            — Issue↔사양↔TC↔Expected 가 모순되지 않는가 (LLM 후)

G1·G2·G4 는 LLM 호출 전에 판정된다. G3·G5 는 판정 결과가 나와야 검사할 수 있어 이후다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.modules.qa_agent.standard_findings import (
    STOP_CONDITION_LABELS,
    Finding,
    detect_forbidden_inferences,
    detect_overclaims,
)
from app.parsers.polarion_issue import IssueRecord

# --- Gate 판정 ---------------------------------------------------------------

PASS = "PASS"
PASS_WITH_NOTE = "PASS_WITH_NOTE"
NEED_INPUT = "NEED_INPUT"
BLOCK = "BLOCK"
PENDING = "PENDING"

STATUS_LABELS = {
    PASS: "통과",
    PASS_WITH_NOTE: "통과 (주의사항 있음)",
    NEED_INPUT: "확인 필요 — QA 입력 대기",
    BLOCK: "중단",
    PENDING: "미판정",
}

#: 이 판정이면 LLM 을 호출하지 않는다.
BLOCKING_STATUSES = frozenset({BLOCK, NEED_INPUT})

GATE_TITLES = {
    "G1": "Source Completeness",
    "G2": "Specification Evidence",
    "G3": "TC & Root Cause Coverage",
    "G4": "Execution Feasibility",
    "G5": "Cross-check",
}


@dataclass
class GateNote:
    """Gate 가 남기는 항목 하나. 표현은 규칙 §36 표준 표현을 쓴다."""

    code: str
    message: str
    #: `block` 은 중단, `input` 은 QA 입력 필요, `note` 는 통과하되 기록.
    severity: str = "note"
    rule_ref: str = ""

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "severity": self.severity, "rule_ref": self.rule_ref}


@dataclass
class GateResult:
    gate: str
    status: str = PENDING
    notes: list[GateNote] = field(default_factory=list)

    @property
    def title(self) -> str:
        return GATE_TITLES.get(self.gate, self.gate)

    @property
    def blocks_llm(self) -> bool:
        return self.status in BLOCKING_STATUSES

    def add(self, code: str, message: str, severity: str = "note", rule_ref: str = "") -> None:
        self.notes.append(GateNote(code=code, message=message, severity=severity, rule_ref=rule_ref))

    def settle(self) -> "GateResult":
        """수집한 note 로 최종 판정을 정한다. block 이 하나라도 있으면 중단이다."""
        severities = {note.severity for note in self.notes}
        if "block" in severities:
            self.status = BLOCK
        elif "input" in severities:
            self.status = NEED_INPUT
        elif "note" in severities:
            self.status = PASS_WITH_NOTE
        else:
            self.status = PASS
        return self

    def as_dict(self) -> dict:
        return {"gate": self.gate, "title": self.title, "status": self.status, "notes": [note.as_dict() for note in self.notes]}


@dataclass
class GateReport:
    """Gate 5개의 판정 묶음. 이대로 저장되고 화면 상단에 표시된다."""

    results: dict[str, GateResult] = field(default_factory=dict)

    def set(self, result: GateResult) -> None:
        self.results[result.gate] = result

    def get(self, gate: str) -> GateResult:
        return self.results.setdefault(gate, GateResult(gate=gate))

    @property
    def blocks_llm(self) -> bool:
        """LLM 호출 전 Gate(G1·G2·G4) 중 하나라도 막았는가."""
        return any(self.results[gate].blocks_llm for gate in ("G1", "G2", "G4") if gate in self.results)

    @property
    def blocking_reasons(self) -> list[str]:
        return [
            f"{result.title}: {note.message}"
            for result in self.results.values()
            for note in result.notes
            if note.severity in ("block", "input")
        ]

    def as_dict(self) -> dict:
        return {gate: result.as_dict() for gate, result in sorted(self.results.items())}


# --- QA 가 입력해야 하는 환경 사실 (규칙 §5.2, §22, §27, §28) ------------------


@dataclass
class ExecutionContext:
    """규칙이 "QA 만 아는 사실"로 정해 둔 값들. 화면에서 입력받는다.

    자동으로 알아낼 수 없다 — 장비가 연결돼 있는지, 실제 촬영이 가능한지, Test Data 가
    있는지는 시스템 밖의 사실이다. 규칙 §5.2 는 이것이 최종 판정에 영향을 주면 **먼저
    질문하라**고 정하고 있고, G4 가 그 역할을 한다.
    """

    #: 실제 수행 가능 여부. None = 미입력.
    can_execute: bool | None = None
    #: 실제 X-ray Exposure 가능 여부 (§28 원격 검증).
    can_expose: bool | None = None
    #: Dose Table 제공 여부 (§27).
    has_dose_table: bool | None = None
    #: 확보된 관찰 수단 (예: "log", "api", "db", "device").
    observation_means: tuple[str, ...] = ()
    #: 이 검증에 반드시 필요한 관찰 수단.
    required_means: tuple[str, ...] = ()
    test_data_ready: bool | None = None
    environment_note: str = ""
    #: QA 가 초안 작성을 허용했는가 (§53 예외).
    draft_allowed: bool = False

    @property
    def missing_means(self) -> tuple[str, ...]:
        return tuple(mean for mean in self.required_means if mean not in self.observation_means)


# --- G1 Source Completeness ---------------------------------------------------


def evaluate_g1(issue: IssueRecord, specification_count: int, testcase_count: int, rules_available: bool) -> GateResult:
    """Issue·문서·TC·버전이 갖춰졌는지. LLM 호출 전에 판정한다."""
    result = GateResult(gate="G1")

    if not issue.issue_id:
        result.add("issue_missing", "Issue 를 식별할 수 없습니다", "block", "규칙 §5.1")
    if not issue.has_standard_body:
        missing = [
            name
            for name, value in (("Step", issue.steps), ("Expected Result", issue.expected), ("Actual Result", issue.actual))
            if not value
        ]
        result.add(
            "body_incomplete",
            f"{Finding.NOT_IN_DOCUMENT} — Issue 본문에 {', '.join(missing)} 구획이 없습니다",
            "note",
            "규칙 §29",
        )
    if not issue.occurred_versions and not issue.target_versions:
        result.add("version_unclear", STOP_CONDITION_LABELS["version_or_env_unclear"], "input", "규칙 §53")
    if issue.needs_type_confirmation:
        candidates = ", ".join(issue.issue_type_candidates) or "후보 없음"
        result.add(
            "issue_type_unconfirmed",
            f"Issue 유형이 확정되지 않았습니다 (연구소 검토 결과 '{issue.lab_review_result}' → 후보: {candidates})",
            "input",
            "규칙 §6",
        )
    if not specification_count:
        result.add("no_specification", f"{Finding.NOT_IN_DOCUMENT} — 등록된 사양서가 없습니다", "block", "규칙 §5.1")
    if not testcase_count:
        result.add("no_testcase", f"{Finding.NOT_IN_EXISTING_TC} — 등록된 TC 가 없습니다", "block", "규칙 §5.1")
    if not rules_available:
        result.add("no_rules", "이 제품의 QA 규칙 문서가 수집되지 않았습니다 (규칙 없이 판정하지 않습니다)", "block", "규칙 §1")

    return result.settle()


# --- G2 Specification Evidence ------------------------------------------------


def evaluate_g2(
    issue: IssueRecord,
    evidence_count: int,
    exact_evidence_count: int,
    deprecated_hits: int = 0,
    searched_document_count: int = 0,
    total_document_count: int = 0,
) -> GateResult:
    """공식 사양 근거를 찾았는지. 근거가 없으면 사양을 만들지 않는다 (규칙 §44 G2)."""
    result = GateResult(gate="G2")

    if not evidence_count:
        result.add("spec_evidence_insufficient", f"{Finding.CANNOT_JUDGE_BEFORE_SPEC} — 관련 사양 근거를 찾지 못했습니다", "block", "규칙 §44 G2")
    elif not exact_evidence_count:
        result.add("no_exact_evidence", f"{Finding.SRS_EXISTS_BODY_UNVERIFIED} — 용어 유사도로만 찾은 근거입니다", "note", "규칙 §8")

    if issue.linked_srs and not exact_evidence_count:
        result.add(
            "linked_srs_not_found",
            f"{Finding.SRS_EXISTS_BODY_UNVERIFIED} — Issue 에 연결된 SRS({', '.join(issue.linked_srs)})를 사양서 본문에서 찾지 못했습니다",
            "note",
            "규칙 §17",
        )
    if deprecated_hits:
        result.add("deprecated_spec", f"{Finding.DEPRECATED_SPEC_UNCERTAIN} — 취소선/삭제 표시가 있는 근거 {deprecated_hits}건", "note", "규칙 §11")
    if total_document_count and searched_document_count < total_document_count:
        result.add(
            "split_spec_partial",
            f"{Finding.SPLIT_SPEC_SEARCH_INCOMPLETE} — 사양서 {total_document_count}건 중 {searched_document_count}건만 조사했습니다",
            "note",
            "규칙 §10",
        )

    return result.settle()


# --- G4 Execution Feasibility -------------------------------------------------


def evaluate_g4(issue: IssueRecord, context: ExecutionContext) -> GateResult:
    """실제로 수행할 수 있는지. 필수 관찰 수단이 없으면 다른 수단으로 Pass 처리하지 않는다."""
    result = GateResult(gate="G4")

    if context.can_execute is None:
        result.add("execution_unknown", f"{Finding.NEEDS_EXECUTION} — 실제 수행 가능 여부가 입력되지 않았습니다", "input", "규칙 §5.2")
    elif context.can_execute is False:
        result.add("cannot_execute", f"{Finding.OUT_OF_SCOPE} — 이 환경에서 수행할 수 없습니다", "note", "규칙 §5.2")

    missing = context.missing_means
    if missing:
        result.add(
            "observation_means_missing",
            f"{Finding.NEEDS_API_VERIFICATION} — 필수 관찰 수단 없음: {', '.join(missing)}",
            "block",
            "규칙 §55",
        )

    if context.test_data_ready is False:
        result.add("test_data_missing", f"{Finding.NEEDS_EXECUTION} — Test Data 가 준비되지 않았습니다", "input", "규칙 §5.2")

    if context.can_expose is False:
        result.add(
            "exposure_unavailable",
            f"{Finding.OUT_OF_SCOPE} — 실제 Exposure 불가. 실제 X-ray 결과를 Expected 로 작성하지 않습니다",
            "note",
            "규칙 §28",
        )
    if context.has_dose_table is False:
        result.add(
            "dose_table_missing",
            f"{Finding.NEEDS_MORE_SPEC} — Dose Table 미제공. 공식 촬영 가능 조합·출력 정확도를 확정하지 않습니다",
            "note",
            "규칙 §27",
        )

    return result.settle()


# --- G3 TC & Root Cause Coverage ----------------------------------------------


def evaluate_g3(issue: IssueRecord, candidate_count: int, covering_tc_ids: list[str], link_only_tc_ids: list[str]) -> GateResult:
    """기존 TC 가 Root Cause 를 실제로 덮는지. Issue 번호 존재만으로 인정하지 않는다."""
    result = GateResult(gate="G3")

    if not candidate_count:
        result.add("no_tc_candidate", f"{Finding.NOT_IN_EXISTING_TC} — 관련 TC 후보를 찾지 못했습니다", "note", "규칙 §44 G3")
    if not issue.occurrence_cause and not issue.action_details:
        result.add("root_cause_unknown", STOP_CONDITION_LABELS["root_cause_unknown"], "input", "규칙 §53")
    if not covering_tc_ids:
        result.add("coverage_insufficient", str(Finding.ROOT_CAUSE_COVERAGE_INSUFFICIENT), "note", "규칙 §33")
    if link_only_tc_ids:
        result.add(
            "link_only_coverage",
            f"{Finding.ISSUE_LINK_MEANING_UNVERIFIED} — Issue 번호만 일치하는 TC {len(link_only_tc_ids)}건: {', '.join(link_only_tc_ids[:5])}",
            "note",
            "규칙 §17",
        )

    return result.settle()


# --- G5 Cross-check -----------------------------------------------------------


def evaluate_g5(issue: IssueRecord, claims: list[dict]) -> GateResult:
    """판정 결과의 상호 모순과 근거 없는 확정을 검사한다 (규칙 §37, §44 G5).

    `claims` 는 `{"text": ..., "evidence": [...], "tc_id": ...}` 형태의 판정 목록이다.
    """
    result = GateResult(gate="G5")

    if not claims:
        result.add("no_claim", "판정 결과가 없습니다", "note", "규칙 §44 G5")

    for claim in claims:
        text = str(claim.get("text") or "")
        has_evidence = bool(claim.get("evidence"))
        label = str(claim.get("tc_id") or claim.get("id") or "판정")

        if not has_evidence:
            result.add("claim_without_source", f"{label}: Source 위치 없는 판단입니다", "note", "규칙 §55")
        for violation in detect_forbidden_inferences(text, has_evidence):
            result.add(
                f"forbidden_{violation.code}",
                f"{label}: 근거 없이 '{violation.label}'을 확정했습니다 — \"{violation.excerpt}\"",
                "note",
                "규칙 §37",
            )
        for violation in detect_overclaims(text):
            result.add(
                f"overclaim_{violation.code}",
                f"{label}: 조사 범위를 넘어선 확정 표현 '{violation.matched}'",
                "note",
                "규칙 §8",
            )

    if issue.blocks_auto_runtime_tc and any(claim.get("kind") == "runtime_tc" for claim in claims):
        result.add(
            "runtime_tc_for_spec_issue",
            f"{Finding.RUNTIME_TC_UNNECESSARY} — {issue.type_label} 유형에 Runtime TC 를 만들지 않습니다",
            "block",
            "규칙 §6",
        )

    return result.settle()


# --- LLM 호출 전 판정 묶음 -----------------------------------------------------


@dataclass
class PreflightResult:
    """LLM 호출 전 Gate 판정 결과. `proceed=False` 면 API 를 호출하지 않는다."""

    report: GateReport
    proceed: bool
    draft_only: bool
    reasons: list[str]

    def as_dict(self) -> dict:
        return {"gates": self.report.as_dict(), "proceed": self.proceed, "draft_only": self.draft_only, "reasons": self.reasons}


def preflight(
    issue: IssueRecord,
    context: ExecutionContext,
    specification_count: int,
    testcase_count: int,
    rules_available: bool,
    evidence_count: int,
    exact_evidence_count: int,
    deprecated_hits: int = 0,
    searched_document_count: int = 0,
    total_document_count: int = 0,
) -> PreflightResult:
    """G1·G2·G4 를 판정해 LLM 호출 여부를 정한다.

    막히면 API 호출이 0회다. QA 가 초안을 허용했으면(`draft_allowed`) 입력 대기(NEED_INPUT)
    까지는 진행하되 `draft_only` 로 표시한다 — 규칙 §53 의 예외 조항이다. 중단(BLOCK)은
    초안 허용으로도 넘기지 않는다.
    """
    report = GateReport()
    report.set(evaluate_g1(issue, specification_count, testcase_count, rules_available))
    report.set(
        evaluate_g2(
            issue,
            evidence_count=evidence_count,
            exact_evidence_count=exact_evidence_count,
            deprecated_hits=deprecated_hits,
            searched_document_count=searched_document_count,
            total_document_count=total_document_count,
        )
    )
    report.set(evaluate_g4(issue, context))
    report.set(GateResult(gate="G3", status=PENDING))
    report.set(GateResult(gate="G5", status=PENDING))

    has_block = any(report.results[gate].status == BLOCK for gate in ("G1", "G2", "G4"))
    has_input = any(report.results[gate].status == NEED_INPUT for gate in ("G1", "G2", "G4"))
    proceed = not has_block and (not has_input or context.draft_allowed)
    return PreflightResult(
        report=report,
        proceed=proceed,
        draft_only=proceed and has_input,
        reasons=report.blocking_reasons,
    )
