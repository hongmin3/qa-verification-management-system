"""Gate 엔진 테스트.

가장 중요한 성질은 **막으면 LLM 을 부르지 않는다**는 것이다 (토큰 절약이자 규칙 §53 준수).
그 다음이 **막은 이유가 결과에 남는다**는 것 — 규칙 §55 가 "문서 검색 실패를 숨긴 확정
판정"을 금지하기 때문이다.
"""

from __future__ import annotations

import pytest

from app.modules.qa_agent.gates import (
    BLOCK,
    NEED_INPUT,
    PASS,
    PASS_WITH_NOTE,
    PENDING,
    ExecutionContext,
    GateReport,
    GateResult,
    evaluate_g1,
    evaluate_g2,
    evaluate_g3,
    evaluate_g4,
    evaluate_g5,
    preflight,
)
from app.modules.qa_agent.standard_findings import (
    AUTOMATION_PROHIBITION_LABELS,
    FORBIDDEN_INFERENCES,
    STOP_CONDITION_LABELS,
    Finding,
    detect_forbidden_inferences,
    detect_overclaims,
)
from app.parsers.polarion_issue import (
    TYPE_PROGRAM_FIXED,
    TYPE_SPEC_NOT_BUG,
    TYPE_UNCLASSIFIED,
    IssueRecord,
)


def _issue(**overrides) -> IssueRecord:
    defaults = {
        "issue_id": "AP-1001",
        "title": "확인 팝업이 표시되지 않음",
        "steps": ["설정을 켠다"],
        "expected": ["팝업이 표시된다"],
        "actual": ["표시되지 않는다"],
        "occurrence_cause": "설정 반영 시점 누락",
        "action_details": "로그인 직후로 변경",
        "lab_review_result": "lab_fixed",
        "issue_type": TYPE_PROGRAM_FIXED,
        "issue_type_candidates": (TYPE_PROGRAM_FIXED,),
        "occurred_versions": ["Acme_1_1_0_001"],
        "target_versions": ["Acme_1_1_0_002"],
    }
    defaults.update(overrides)
    return IssueRecord(**defaults)


def _ready_context(**overrides) -> ExecutionContext:
    defaults = {"can_execute": True, "test_data_ready": True}
    defaults.update(overrides)
    return ExecutionContext(**defaults)


# --- 표준 표현·금지 추론 (규칙 §36, §37) ---------------------------------------


def test_standard_findings_use_the_rule_wording() -> None:
    """결과에 쓰는 문구가 규칙 문서와 같아야 QA 가 대조할 수 있다."""
    assert Finding.ROOT_CAUSE_COVERAGE_INSUFFICIENT == "Root Cause Coverage 부족"
    assert Finding.CANNOT_JUDGE_BEFORE_SPEC == "사양 확인 전 Pass/Fail 판정 불가"
    assert len(list(Finding)) == 15


def test_all_forbidden_inferences_from_the_rule_are_encoded() -> None:
    assert len(FORBIDDEN_INFERENCES) == 20
    assert all(inference.keywords for inference in FORBIDDEN_INFERENCES)


def test_stop_conditions_and_prohibitions_are_encoded() -> None:
    assert len(STOP_CONDITION_LABELS) == 9
    assert len(AUTOMATION_PROHIBITION_LABELS) == 8


def test_forbidden_inference_is_detected_without_evidence() -> None:
    violations = detect_forbidden_inferences("설정은 자동 저장된다.", has_evidence=False)
    assert [violation.code for violation in violations] == ["auto_save"]


def test_forbidden_inference_is_allowed_with_evidence() -> None:
    """규칙이 금지한 것은 이 주장 자체가 아니라 근거 없는 확정이다."""
    assert detect_forbidden_inferences("설정은 자동 저장된다.", has_evidence=True) == []


def test_overclaim_is_detected() -> None:
    violations = detect_overclaims("사양서를 전수 조사 완료했습니다.")
    assert [violation.code for violation in violations] == ["full_search_claim"]


def test_no_spec_claim_is_detected() -> None:
    assert [violation.code for violation in detect_overclaims("관련 사양이 없음")] == ["no_spec_claim"]


# --- G1 Source Completeness ---------------------------------------------------


def test_g1_passes_when_everything_is_present() -> None:
    result = evaluate_g1(_issue(), specification_count=5, testcase_count=100, rules_available=True)
    assert result.status == PASS


def test_g1_blocks_without_specification() -> None:
    result = evaluate_g1(_issue(), specification_count=0, testcase_count=100, rules_available=True)
    assert result.status == BLOCK
    assert any(note.code == "no_specification" for note in result.notes)


def test_g1_blocks_without_testcase() -> None:
    result = evaluate_g1(_issue(), specification_count=5, testcase_count=0, rules_available=True)
    assert result.status == BLOCK


def test_g1_blocks_without_product_rules() -> None:
    """제품 규칙 문서 없이 규칙 기반 판정을 하지 않는다."""
    result = evaluate_g1(_issue(), specification_count=5, testcase_count=100, rules_available=False)
    assert result.status == BLOCK
    assert any(note.code == "no_rules" for note in result.notes)


def test_g1_asks_for_input_when_issue_type_is_ambiguous() -> None:
    """`lab_noact` 처럼 유형이 갈리는 경우 추정하지 않고 QA 에게 묻는다 (규칙 §6, §37)."""
    issue = _issue(lab_review_result="lab_noact", issue_type=TYPE_UNCLASSIFIED, issue_type_candidates=(TYPE_SPEC_NOT_BUG, "G_CANNOT_REPRODUCE"))
    result = evaluate_g1(issue, specification_count=5, testcase_count=100, rules_available=True)
    assert result.status == NEED_INPUT
    assert any(note.code == "issue_type_unconfirmed" for note in result.notes)


def test_g1_asks_for_input_when_version_is_unknown() -> None:
    issue = _issue(occurred_versions=[], target_versions=[])
    result = evaluate_g1(issue, specification_count=5, testcase_count=100, rules_available=True)
    assert result.status == NEED_INPUT
    assert STOP_CONDITION_LABELS["version_or_env_unclear"] in [note.message for note in result.notes]


def test_g1_notes_incomplete_issue_body_without_blocking() -> None:
    """실제로 `[Step]` 이 없는 Issue 가 있다. 기록은 남기되 그것만으로 막지는 않는다."""
    result = evaluate_g1(_issue(steps=[]), specification_count=5, testcase_count=100, rules_available=True)
    assert result.status == PASS_WITH_NOTE
    note = next(note for note in result.notes if note.code == "body_incomplete")
    assert "Step" in note.message


# --- G2 Specification Evidence ------------------------------------------------


def test_g2_blocks_when_no_evidence_found() -> None:
    result = evaluate_g2(_issue(), evidence_count=0, exact_evidence_count=0)
    assert result.status == BLOCK
    assert any("사양 확인 전 Pass/Fail 판정 불가" in note.message for note in result.notes)


def test_g2_passes_with_exact_evidence() -> None:
    result = evaluate_g2(_issue(linked_srs=[]), evidence_count=4, exact_evidence_count=2)
    assert result.status == PASS


def test_g2_notes_when_only_similarity_evidence_exists() -> None:
    result = evaluate_g2(_issue(linked_srs=[]), evidence_count=4, exact_evidence_count=0)
    assert result.status == PASS_WITH_NOTE
    assert any(note.code == "no_exact_evidence" for note in result.notes)


def test_g2_notes_when_linked_srs_is_not_found_in_documents() -> None:
    result = evaluate_g2(_issue(linked_srs=["AP-5500"]), evidence_count=3, exact_evidence_count=0)
    assert any(note.code == "linked_srs_not_found" for note in result.notes)


def test_g2_notes_deprecated_specification() -> None:
    result = evaluate_g2(_issue(linked_srs=[]), evidence_count=3, exact_evidence_count=1, deprecated_hits=2)
    assert any(note.code == "deprecated_spec" for note in result.notes)


def test_g2_notes_partial_split_specification_search() -> None:
    """규칙 §10: 분할 사양서 일부만 봤으면 '사양 없음'을 확정하지 않는다."""
    result = evaluate_g2(_issue(linked_srs=[]), evidence_count=3, exact_evidence_count=1, searched_document_count=2, total_document_count=5)
    note = next(note for note in result.notes if note.code == "split_spec_partial")
    assert "5건 중 2건" in note.message


# --- G4 Execution Feasibility -------------------------------------------------


def test_g4_asks_for_input_when_execution_is_unknown() -> None:
    result = evaluate_g4(_issue(), ExecutionContext())
    assert result.status == NEED_INPUT


def test_g4_passes_when_context_is_ready() -> None:
    assert evaluate_g4(_issue(), _ready_context()).status == PASS


def test_g4_blocks_when_required_observation_means_is_missing() -> None:
    """규칙 §55: 필수 관찰 수단 없이 Pass 처리하지 않는다."""
    context = _ready_context(required_means=("api", "log"), observation_means=("log",))
    result = evaluate_g4(_issue(), context)
    assert result.status == BLOCK
    note = next(note for note in result.notes if note.code == "observation_means_missing")
    assert "api" in note.message


def test_g4_notes_exposure_unavailable_without_blocking() -> None:
    result = evaluate_g4(_issue(), _ready_context(can_expose=False))
    assert result.status == PASS_WITH_NOTE
    assert any(note.code == "exposure_unavailable" for note in result.notes)


def test_g4_notes_missing_dose_table() -> None:
    result = evaluate_g4(_issue(), _ready_context(has_dose_table=False))
    assert any(note.code == "dose_table_missing" for note in result.notes)


def test_g4_asks_for_input_when_test_data_is_not_ready() -> None:
    assert evaluate_g4(_issue(), _ready_context(test_data_ready=False)).status == NEED_INPUT


# --- G3 TC & Root Cause Coverage ----------------------------------------------


def test_g3_passes_when_a_tc_covers_the_root_cause() -> None:
    result = evaluate_g3(_issue(), candidate_count=12, covering_tc_ids=["TC-100"], link_only_tc_ids=[])
    assert result.status == PASS


def test_g3_notes_when_no_tc_covers_the_root_cause() -> None:
    result = evaluate_g3(_issue(), candidate_count=12, covering_tc_ids=[], link_only_tc_ids=[])
    assert str(Finding.ROOT_CAUSE_COVERAGE_INSUFFICIENT) in [note.message for note in result.notes]


def test_g3_flags_tc_matched_only_by_issue_number() -> None:
    """규칙 §17: Issue 번호 존재만으로 Coverage 를 인정하지 않는다."""
    result = evaluate_g3(_issue(), candidate_count=12, covering_tc_ids=["TC-1"], link_only_tc_ids=["TC-7", "TC-8"])
    note = next(note for note in result.notes if note.code == "link_only_coverage")
    assert "TC-7" in note.message


def test_g3_asks_for_input_when_root_cause_is_unknown() -> None:
    issue = _issue(occurrence_cause="", action_details="")
    result = evaluate_g3(issue, candidate_count=5, covering_tc_ids=["TC-1"], link_only_tc_ids=[])
    assert result.status == NEED_INPUT


# --- G5 Cross-check -----------------------------------------------------------


def test_g5_passes_when_every_claim_has_evidence() -> None:
    claims = [{"tc_id": "TC-1", "text": "설정 화면에서 확인한다", "evidence": ["chunk-1"]}]
    assert evaluate_g5(_issue(), claims).status == PASS


def test_g5_flags_claim_without_source() -> None:
    """규칙 §55: Source 위치 없는 판단만 저장하지 않는다."""
    result = evaluate_g5(_issue(), [{"tc_id": "TC-1", "text": "정상 동작한다", "evidence": []}])
    assert any(note.code == "claim_without_source" for note in result.notes)


def test_g5_flags_forbidden_inference_in_claim() -> None:
    result = evaluate_g5(_issue(), [{"tc_id": "TC-1", "text": "값은 자동 저장된다", "evidence": []}])
    assert any(note.code == "forbidden_auto_save" for note in result.notes)


def test_g5_flags_overclaim_in_claim() -> None:
    result = evaluate_g5(_issue(), [{"tc_id": "TC-1", "text": "전수 조사 완료", "evidence": ["c1"]}])
    assert any(note.code.startswith("overclaim_") for note in result.notes)


def test_g5_blocks_runtime_tc_for_spec_issue() -> None:
    """규칙 §6: Spec/Not Bug 유형에 Runtime TC 를 자동 생성하지 않는다."""
    issue = _issue(lab_review_result="lab_inspec", issue_type=TYPE_SPEC_NOT_BUG, issue_type_candidates=(TYPE_SPEC_NOT_BUG,))
    result = evaluate_g5(issue, [{"kind": "runtime_tc", "text": "촬영을 시작한다", "evidence": ["c1"]}])
    assert result.status == BLOCK
    assert any(note.code == "runtime_tc_for_spec_issue" for note in result.notes)


# --- preflight: LLM 호출 여부 --------------------------------------------------


def _preflight(issue: IssueRecord | None = None, context: ExecutionContext | None = None, **overrides):
    kwargs = {
        "specification_count": 5,
        "testcase_count": 100,
        "rules_available": True,
        "evidence_count": 4,
        "exact_evidence_count": 2,
    }
    kwargs.update(overrides)
    return preflight(issue or _issue(linked_srs=[]), context or _ready_context(), **kwargs)


def test_preflight_proceeds_when_all_pre_llm_gates_pass() -> None:
    result = _preflight()
    assert result.proceed is True
    assert result.draft_only is False
    assert result.reasons == []


def test_preflight_stops_the_llm_call_when_evidence_is_missing() -> None:
    """이 테스트가 이 계층의 존재 이유다 — 근거가 없으면 API 호출이 0회여야 한다."""
    result = _preflight(evidence_count=0, exact_evidence_count=0)
    assert result.proceed is False
    assert result.report.blocks_llm is True


def test_preflight_records_why_it_stopped() -> None:
    """규칙 §55: 검색 실패를 숨긴 확정 판정을 만들지 않는다 — 이유가 남아야 한다."""
    result = _preflight(specification_count=0)
    assert result.reasons
    assert any("Source Completeness" in reason for reason in result.reasons)


def test_preflight_waits_for_qa_input_by_default() -> None:
    result = _preflight(context=ExecutionContext())
    assert result.proceed is False


def test_preflight_allows_draft_when_qa_permitted_it() -> None:
    """규칙 §53 예외: 사용자가 초안을 허용하면 '확인 필요' 상태로 작성할 수 있다."""
    result = _preflight(context=ExecutionContext(draft_allowed=True))
    assert result.proceed is True
    assert result.draft_only is True


def test_draft_permission_does_not_override_a_hard_block() -> None:
    """초안 허용은 입력 대기를 넘길 뿐, 중단 조건을 넘기지 않는다."""
    result = _preflight(context=ExecutionContext(draft_allowed=True), specification_count=0)
    assert result.proceed is False


def test_preflight_leaves_post_llm_gates_pending() -> None:
    result = _preflight()
    assert result.report.get("G3").status == PENDING
    assert result.report.get("G5").status == PENDING


def test_preflight_serialises_for_storage() -> None:
    """Gate 결과는 화면 표시용이 아니라 저장되는 데이터다 (로드맵 §11)."""
    payload = _preflight(specification_count=0).as_dict()
    assert set(payload) == {"gates", "proceed", "draft_only", "reasons"}
    assert payload["gates"]["G1"]["status"] == BLOCK
    assert payload["gates"]["G1"]["notes"][0]["rule_ref"]


# --- GateResult / GateReport 동작 ---------------------------------------------


def test_gate_result_settles_to_the_most_severe_note() -> None:
    result = GateResult(gate="G1")
    result.add("a", "기록", "note")
    result.add("b", "입력", "input")
    result.add("c", "중단", "block")
    assert result.settle().status == BLOCK


def test_gate_result_without_notes_passes() -> None:
    assert GateResult(gate="G1").settle().status == PASS


@pytest.mark.parametrize(("severity", "expected"), [("note", PASS_WITH_NOTE), ("input", NEED_INPUT), ("block", BLOCK)])
def test_gate_status_per_severity(severity: str, expected: str) -> None:
    result = GateResult(gate="G2")
    result.add("x", "메시지", severity)
    assert result.settle().status == expected


def test_report_ignores_post_llm_gates_when_deciding_llm_call() -> None:
    """G5 가 막아도 그것은 LLM 호출 이후 판정이라 호출 여부에 영향이 없다."""
    report = GateReport()
    g5 = GateResult(gate="G5")
    g5.add("x", "중단", "block")
    report.set(g5.settle())
    assert report.blocks_llm is False
