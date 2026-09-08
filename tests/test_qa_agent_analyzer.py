"""QA Agent 파이프라인 통합 테스트.

Gemini 는 mock responder 로 대체한다 (실제 호출 없음). 이 테스트가 지키는 성질:

1. Gate 가 막으면 **responder 가 호출되지 않는다** — 토큰 절약이 실제로 동작하는지.
2. 존재하지 않는 ID 는 결과에서 빠지고, 뺐다는 사실이 남는다.
3. 판정마다 근거가 붙고, 근거 없는 판정은 그 사실이 드러난다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.document_schemas import SpecificationChunk, TestCase
from app.core.knowledge_documents import LoadedKnowledge
from app.core.qa_rules import RuleSet, parse_rule_document
from app.modules.qa_agent import analyzer as analyzer_module
from app.modules.qa_agent.ai_client import QaAgentAIClient, issue_payload, testcase_payload
from app.modules.qa_agent.analyzer import QaAgentAnalyzer
from app.modules.qa_agent.evidence import (
    LEVEL_EXISTING_TC,
    LEVEL_HINT,
    LEVEL_SPECIFICATION,
    Claim,
    EvidenceStore,
    issue_evidence,
    specification_evidence,
    testcase_evidence,
)
from app.modules.qa_agent.gates import BLOCK, PASS, ExecutionContext
from app.modules.qa_agent.schemas import AXIS_LABELS
from app.parsers.polarion_issue import TYPE_PROGRAM_FIXED, IssueRecord

RULES_TEXT = """# Acme TC 설계 가이드

- 문서 버전: Rev.3.1

# 3. Skill Registry

## 3.1 S01 — Issue Analysis

Issue 유형과 Trigger 를 추출한다.

## 3.5 S05 — Regression Impact

Root Cause 기반 영향 범위를 분석한다.

# 13. 출력 규칙

근거와 판정을 분리한다.
"""


def _rule_set() -> RuleSet:
    from app.core.product_knowledge import KIND_QA_RULES

    return RuleSet(product="Acme", documents=[parse_rule_document(RULES_TEXT, name="Acme 가이드", kind=KIND_QA_RULES)])


def _issue(**overrides) -> IssueRecord:
    defaults = {
        "issue_id": "AP-1001",
        "title": "확인 팝업이 표시되지 않음",
        "steps": ["설정을 켠다", "로그인한다"],
        "expected": ["2. 팝업이 표시된다"],
        "actual": ["표시되지 않는다"],
        "occurrence_cause": "설정 반영 시점 누락",
        "action_details": "로그인 직후로 변경",
        "lab_review_result": "lab_fixed",
        "issue_type": TYPE_PROGRAM_FIXED,
        "issue_type_candidates": (TYPE_PROGRAM_FIXED,),
        "occurred_versions": ["Acme_1_1_0_001"],
        "target_versions": ["Acme_1_1_0_002"],
        "linked_srs": ["AP-5500"],
        "search_terms": ["AP-5500", "확인 팝업"],
    }
    defaults.update(overrides)
    return IssueRecord(**defaults)


def _chunks() -> list[SpecificationChunk]:
    return [
        SpecificationChunk(chunk_id="c1", document_id="Acme 사양서1", page=12, heading="4.3.2 로그인", text="AP-5500 첫 로그인 시 확인 팝업을 표시한다."),
        SpecificationChunk(chunk_id="c2", document_id="Acme 사양서1", page=13, heading="4.3.3 계정", text="계정 추가 시 권한을 설정한다."),
        SpecificationChunk(chunk_id="c3", document_id="Acme 사양서2", page=4, heading="7.1 보관", text="이 항목은 삭제되었다."),
    ]


def _cases() -> list[TestCase]:
    return [
        TestCase(tc_id="TC-100", category="Acme_TC.xlsx", feature="로그인", step="설정을 켜고 로그인한다", expected_result="확인 팝업이 표시된다"),
        TestCase(tc_id="TC-200", category="Acme_TC.xlsx", feature="계정", step="계정을 추가한다", expected_result="목록에 표시된다"),
    ]


def _knowledge() -> LoadedKnowledge:
    return LoadedKnowledge(
        product="Acme",
        chunks=_chunks(),
        cases=_cases(),
        specification_documents=[{"id": 1, "kind": "specification", "name": "Acme 사양서1", "product": "Acme", "version": "1.0", "revision": "", "created_at": ""}],
        testcase_documents=[{"id": 2, "kind": "testcase", "name": "Acme_TC.xlsx", "product": "Acme", "version": "1.0", "revision": "", "created_at": ""}],
    )


def _response(tc_ids: list[str] | None = None, chunk_ids: list[str] | None = None) -> dict:
    tc_ids = tc_ids if tc_ids is not None else ["TC-100"]
    chunk_ids = chunk_ids if chunk_ids is not None else ["c1"]
    return {
        "issue_analysis": {
            "trigger": ["설정을 켠 뒤 새 계정으로 첫 로그인"],
            "root_cause_summary": "설정 반영 시점이 로그인 이후였다",
            "change_target": "인증 모듈",
            "before": "팝업 없음",
            "after": "팝업 표시",
        },
        "specification_relevance": [{"chunk_id": chunk_id, "relevance": "MAIN", "reason": "확인 팝업을 직접 규정"} for chunk_id in chunk_ids],
        "tc_coverage": [
            {
                "tc_id": tc_id,
                "judgment": "MUST_UPDATE",
                "covers_root_cause": True,
                "reason": "Step 에 첫 로그인 조건이 없다",
                "evidence_chunk_ids": chunk_ids,
                "confidence": 0.9,
            }
            for tc_id in tc_ids
        ],
        "regression_areas": [
            {
                "axis": "DIRECT",
                "applicable": True,
                "priority": "HIGH",
                "trigger": "설정 후 첫 로그인",
                "expected": "확인 팝업이 표시된다",
                "reason": "변경된 기능 자체",
                "evidence_chunk_ids": chunk_ids,
                "related_tc_ids": tc_ids,
            }
        ],
        "unresolved_questions": [],
    }


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch):
    """지식 문서·규칙 로딩을 고정하고 responder 호출 횟수를 센다."""
    calls: list[str] = []

    def responder(prompt: str) -> dict:
        calls.append(prompt)
        return _response()

    monkeypatch.setattr(analyzer_module, "load_for_product", lambda *args, **kwargs: _knowledge())
    monkeypatch.setattr(analyzer_module, "load_rule_set", lambda product: _rule_set())
    return {"calls": calls, "responder": responder}


def _analyzer(patched, storage, responder=None) -> QaAgentAnalyzer:
    client = QaAgentAIClient(storage=storage, responder=responder or patched["responder"])
    return QaAgentAnalyzer(ai_client=client, storage=storage)


@pytest.fixture
def storage(tmp_path: Path):
    from app.core.storage import Storage

    return Storage(db_path=tmp_path / "test.db")


def _ready_context(**overrides) -> ExecutionContext:
    defaults = {"can_execute": True, "test_data_ready": True}
    defaults.update(overrides)
    return ExecutionContext(**defaults)


# --- Gate 가 막으면 LLM 을 부르지 않는다 ---------------------------------------


def test_gate_block_means_zero_llm_calls(patched, storage, monkeypatch) -> None:
    """이 테스트가 토큰 절약 설계의 핵심 검증이다."""
    monkeypatch.setattr(analyzer_module, "load_rule_set", lambda product: RuleSet(product="Acme"))  # 규칙 미수집
    result = _analyzer(patched, storage).run(product="Acme", issue=_issue(), context=_ready_context())

    assert result.status == "GATE_BLOCKED"
    assert result.proceeded_to_ai is False
    assert patched["calls"] == []
    assert result.request_count == 0


def test_gate_block_records_the_reason(patched, storage, monkeypatch) -> None:
    monkeypatch.setattr(analyzer_module, "load_rule_set", lambda product: RuleSet(product="Acme"))
    result = _analyzer(patched, storage).run(product="Acme", issue=_issue(), context=_ready_context())

    assert result.blocking_reasons
    assert result.gates["G1"]["status"] == BLOCK


def test_missing_execution_context_blocks_the_call(patched, storage) -> None:
    result = _analyzer(patched, storage).run(product="Acme", issue=_issue(), context=ExecutionContext())
    assert result.status == "GATE_BLOCKED"
    assert patched["calls"] == []


def test_draft_permission_lets_it_proceed(patched, storage) -> None:
    result = _analyzer(patched, storage).run(product="Acme", issue=_issue(), context=ExecutionContext(draft_allowed=True))
    assert result.proceeded_to_ai is True
    assert result.draft_only is True
    assert len(patched["calls"]) == 1


def test_no_specification_evidence_blocks_the_call(patched, storage, monkeypatch) -> None:
    """사양 근거를 하나도 찾지 못하면 사양을 만들지 않고 멈춘다 (G2).

    BM25 는 소표본에서 관련도와 무관하게 후보를 채우므로(기존 구현의 의도적 fallback),
    이 차단이 실제로 걸리는 조건은 "검색 결과가 아예 없다"다. 등록 문서가 있는데 Chunk 를
    못 만든 경우(파싱 실패 등)가 여기 해당한다.
    """
    empty = LoadedKnowledge(
        product="Acme",
        chunks=[],
        cases=_cases(),
        specification_documents=_knowledge().specification_documents,
        testcase_documents=_knowledge().testcase_documents,
    )
    monkeypatch.setattr(analyzer_module, "load_for_product", lambda *args, **kwargs: empty)
    result = _analyzer(patched, storage).run(product="Acme", issue=_issue(), context=_ready_context())

    assert result.status == "GATE_BLOCKED"
    assert patched["calls"] == []
    assert any(note["code"] == "spec_evidence_insufficient" for note in result.gates["G2"]["notes"])


def test_similarity_only_evidence_is_noted_not_blocked(patched, storage) -> None:
    """유사도로만 찾은 근거는 막지 않고 기록한다 — QA 가 판단할 몫이다 (규칙 §8).

    BM25 가 소표본에서 후보를 채우는 성질 때문에, 정확 일치가 없다는 사실이 결과에 남는
    것이 중요하다. 남지 않으면 약한 근거가 강한 근거처럼 보인다.
    """
    issue = _issue(linked_srs=[], search_terms=[])
    result = _analyzer(patched, storage).run(product="Acme", issue=issue, context=_ready_context())

    assert result.proceeded_to_ai is True
    codes = [note["code"] for note in result.gates["G2"]["notes"]]
    assert "no_exact_evidence" in codes


def test_linked_srs_not_found_in_documents_is_noted(patched, storage) -> None:
    """Issue 가 SRS 를 연결했는데 사양서 본문에서 못 찾으면 그 사실이 남아야 한다 (규칙 §17)."""
    issue = _issue(linked_srs=["AP-9999"], search_terms=["AP-9999"])
    result = _analyzer(patched, storage).run(product="Acme", issue=issue, context=_ready_context())

    codes = [note["code"] for note in result.gates["G2"]["notes"]]
    assert "linked_srs_not_found" in codes


# --- 정상 실행 ----------------------------------------------------------------


def test_pipeline_makes_exactly_one_llm_call(patched, storage) -> None:
    result = _analyzer(patched, storage).run(product="Acme", issue=_issue(), context=_ready_context())
    assert result.status == "DONE"
    assert len(patched["calls"]) == 1
    assert result.request_count == 1


def test_result_carries_gate_report_for_all_five_gates(patched, storage) -> None:
    result = _analyzer(patched, storage).run(product="Acme", issue=_issue(), context=_ready_context())
    assert set(result.gates) == {"G1", "G2", "G3", "G4", "G5"}
    assert result.gates["G4"]["status"] == PASS


def test_regression_matrix_always_contains_every_axis(patched, storage) -> None:
    """해당하지 않는 축도 '검토했으나 해당 없음'으로 남아야 한다 (규칙 §32)."""
    result = _analyzer(patched, storage).run(product="Acme", issue=_issue(), context=_ready_context())
    axes = [entry["axis"] for entry in result.decision["regression_areas"]]
    assert axes == list(AXIS_LABELS)


def test_unjudged_axes_are_marked_for_qa(patched, storage) -> None:
    result = _analyzer(patched, storage).run(product="Acme", issue=_issue(), context=_ready_context())
    state = next(entry for entry in result.decision["regression_areas"] if entry["axis"] == "STATE")
    assert state["applicable"] is False
    assert "QA 확인" in state["reason"]


def test_evidence_is_attached_to_every_claim(patched, storage) -> None:
    result = _analyzer(patched, storage).run(product="Acme", issue=_issue(), context=_ready_context())
    claims = result.evidence["claims"]
    tc_claim = next(claim for claim in claims if claim["kind"] == "tc_coverage")
    assert tc_claim["evidence"]
    assert any(item["kind"] == "specification" for item in tc_claim["evidence"])


def test_evidence_records_document_and_locator(patched, storage) -> None:
    """규칙 §9.3: 근거는 원문에서 찾아갈 수 있는 위치여야 한다."""
    result = _analyzer(patched, storage).run(product="Acme", issue=_issue(), context=_ready_context())
    spec_claim = next(claim for claim in result.evidence["claims"] if claim["kind"] == "spec_trace")
    citation = spec_claim["evidence"][0]["citation"]
    assert "Acme 사양서1" in citation and "p.12" in citation


def test_retrieval_summary_is_recorded(patched, storage) -> None:
    result = _analyzer(patched, storage).run(product="Acme", issue=_issue(), context=_ready_context())
    assert result.retrieval["specification"]["exact"] >= 1
    assert "AP-5500" in result.retrieval["specification"]["terms"]


def test_deprecated_specification_is_noted(patched, storage) -> None:
    """'삭제되었다' 표현이 있는 Chunk 가 근거에 섞이면 G2 가 기록을 남긴다 (규칙 §11)."""
    issue = _issue(search_terms=["AP-5500", "보관"])
    result = _analyzer(patched, storage).run(product="Acme", issue=issue, context=_ready_context())
    codes = [note["code"] for note in result.gates["G2"]["notes"]]
    assert "deprecated_spec" in codes


# --- ID 교차검증 --------------------------------------------------------------


def test_unknown_tc_id_is_dropped_and_recorded(patched, storage) -> None:
    result = _analyzer(patched, storage, responder=lambda prompt: _response(tc_ids=["TC-999"])).run(
        product="Acme", issue=_issue(), context=_ready_context()
    )
    assert result.decision["tc_coverage"] == []
    assert result.validation["dropped_tc_ids"] == ["TC-999"]


def test_unknown_chunk_id_is_dropped_and_confidence_lowered(patched, storage) -> None:
    result = _analyzer(patched, storage, responder=lambda prompt: _response(chunk_ids=["c999"])).run(
        product="Acme", issue=_issue(), context=_ready_context()
    )
    entry = result.decision["tc_coverage"][0]
    assert entry["evidence_chunk_ids"] == []
    assert entry["confidence"] < 0.6
    assert "c999" in result.validation["dropped_chunk_ids"]


def test_root_cause_coverage_is_not_accepted_without_evidence(patched, storage) -> None:
    """근거가 없으면 'Root Cause 를 덮는다'를 인정하지 않는다 (규칙 §33)."""
    result = _analyzer(patched, storage, responder=lambda prompt: _response(chunk_ids=["c999"])).run(
        product="Acme", issue=_issue(), context=_ready_context()
    )
    assert result.decision["tc_coverage"][0]["covers_root_cause"] is False


def test_step_expected_number_mismatch_is_reported(patched, storage) -> None:
    """규칙 §14: Expected 번호가 Step 범위를 벗어나면 기록한다."""
    issue = _issue(steps=["설정을 켠다"], expected=["3. 팝업이 표시된다"])
    result = _analyzer(patched, storage).run(product="Acme", issue=issue, context=_ready_context())
    assert result.validation["step_number_mismatches"]


# --- 보내는 양 (토큰) ---------------------------------------------------------


def test_rule_suffix_is_a_slice_not_the_whole_document(patched, storage) -> None:
    client = QaAgentAIClient(storage=storage, responder=patched["responder"])
    rule_set = _rule_set()
    suffix = client.build_rule_suffix(rule_set, char_budget=1200)
    total = sum(section.size for section in rule_set.sections)
    assert 0 < len(suffix)
    assert "S01 관련 규칙" in suffix
    assert len(suffix) < total + 600  # 헤더를 뺀 본문은 전문보다 작다


def test_rule_suffix_is_empty_without_rules(patched, storage) -> None:
    client = QaAgentAIClient(storage=storage, responder=patched["responder"])
    assert client.build_rule_suffix(RuleSet(product="Acme")) == ""


def test_prompt_does_not_include_tc_result_history(patched, storage) -> None:
    """TC 의 버전별 수행 결과·비고는 판정에 쓰지 않으므로 보내지 않는다."""
    payload = testcase_payload([TestCase(tc_id="TC-1", step="s", expected_result="e", result="Fail", remark="비고")])
    assert "result" not in payload[0]
    assert "remark" not in payload[0]


def test_prompt_limits_issue_comments(patched, storage) -> None:
    from app.parsers.polarion_issue import IssueComment

    issue = _issue(comments=[IssueComment(comment_id=str(index), author="", created="", text=f"댓글 {index}") for index in range(10)])
    payload = issue_payload(issue, max_comments=3)
    assert len(payload["recent_comments"]) == 3


def test_audit_snapshot_records_what_was_sent(patched, storage) -> None:
    result = _analyzer(patched, storage).run(product="Acme", issue=_issue(), context=_ready_context())
    audit = result.ai_audit
    assert audit["user_prompt_chars"] > 0
    assert audit["system_suffix_chars"] > 0
    assert json.loads(audit["user_prompt"])["issue"]["issue_id"] == "AP-1001"


def test_second_identical_run_hits_the_cache(patched, storage) -> None:
    """같은 입력은 API 호출이 발생하지 않는다."""
    analyzer = _analyzer(patched, storage)
    analyzer.run(product="Acme", issue=_issue(), context=_ready_context())
    calls_after_first = len(patched["calls"])
    second = _analyzer(patched, storage).run(product="Acme", issue=_issue(), context=_ready_context())
    assert len(patched["calls"]) == calls_after_first
    assert second.cache_hit is True


# --- Evidence 자료구조 --------------------------------------------------------


def test_specification_evidence_can_support_expected() -> None:
    evidence = specification_evidence("c1", "사양서1", 12, "4.3.2", "본문")
    assert evidence.level == LEVEL_SPECIFICATION
    assert evidence.can_support_expected is True


def test_issue_comment_evidence_cannot_support_expected() -> None:
    """규칙 §7: 연구소 Comment/Resolution 은 탐색 근거이지 Expected 근거가 아니다."""
    evidence = issue_evidence("AP-1", "발생원인", "본문")
    assert evidence.level == LEVEL_HINT
    assert evidence.can_support_expected is False


def test_testcase_evidence_locator_uses_sheet_and_row() -> None:
    evidence = testcase_evidence("TC-1", "TC.xlsx", "Generator", 42, "본문")
    assert evidence.locator == "Generator · 42행"
    assert evidence.level == LEVEL_EXISTING_TC


def test_store_reports_unsourced_claims() -> None:
    store = EvidenceStore()
    store.add(Claim(kind="tc_coverage", label="TC-1", text="근거 없음"))
    store.add(Claim(kind="tc_coverage", label="TC-2", text="근거 있음", evidence=[specification_evidence("c1", "d", 1, "h", "t")]))
    assert [claim.label for claim in store.unsourced] == ["TC-1"]
    assert store.summary()["unsourced"] == 1
