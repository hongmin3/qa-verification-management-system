"""QA Agent 사양–코드 불일치 수정 테스트 (docs/SPEC_CODE_MISMATCH.md 2절, OPEN_QUESTIONS 8-5·8-6·8-8·8-10).

Gemini 는 부르지 않는다. 분석 파이프라인은 mock responder, 화면·API 는 격리된 Storage 로 확인한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.modules.qa_agent.router as router_module
from app.core.knowledge_documents import LoadedKnowledge
from app.core.storage import Storage
from app.main import app
from app.modules.qa_agent import analyzer as analyzer_module
from app.modules.qa_agent import approval_history
from app.modules.qa_agent.ai_client import QaAgentAIClient
from app.modules.qa_agent.analyzer import QaAgentAnalyzer
from app.modules.qa_agent.gates import (
    BLOCK,
    NEED_INPUT,
    ExecutionContext,
    evaluate_g1,
    evaluate_g2,
    evaluate_g4,
    evaluate_g5,
)
from app.parsers.polarion_issue import (
    TYPE_CANNOT_REPRODUCE,
    TYPE_PROGRAM_FIXED,
    TYPE_SPEC_NOT_BUG,
    TYPE_UNCLASSIFIED,
    IssueRecord,
)
from tests.test_qa_agent_analyzer import _chunks, _cases, _issue, _knowledge, _response, _rule_set


def _ready_context(**overrides) -> ExecutionContext:
    defaults = {"can_execute": True, "test_data_ready": True}
    defaults.update(overrides)
    return ExecutionContext(**defaults)


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    return Storage(db_path=tmp_path / "test.db")


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []
    state = {"response": _response(), "knowledge": _knowledge()}

    def responder(prompt: str) -> dict:
        calls.append(prompt)
        return state["response"]

    monkeypatch.setattr(analyzer_module, "load_for_product", lambda *args, **kwargs: state["knowledge"])
    monkeypatch.setattr(analyzer_module, "load_rule_set", lambda product: _rule_set())
    return {"calls": calls, "responder": responder, "state": state}


def _analyzer(patched, storage) -> QaAgentAnalyzer:
    return QaAgentAnalyzer(ai_client=QaAgentAIClient(storage=storage, responder=patched["responder"]), storage=storage)


# --- MISMATCH 1: Test Data 를 모름으로 두면 G4 가 확인을 요청한다 --------------------


# Validates: REQ-QAAGENT-005
def test_g4_asks_for_input_when_test_data_is_unknown() -> None:
    result = evaluate_g4(_issue(), ExecutionContext(can_execute=True, test_data_ready=None))
    assert result.status == NEED_INPUT
    assert any(note.code == "test_data_unknown" and note.severity == "input" for note in result.notes)


# Validates: REQ-QAAGENT-005
def test_g4_keeps_asking_when_test_data_is_missing() -> None:
    result = evaluate_g4(_issue(), ExecutionContext(can_execute=True, test_data_ready=False))
    assert [note.code for note in result.notes if note.severity == "input"] == ["test_data_missing"]


# --- MISMATCH 2: B·C·D 유형에 AI 가 신규 TC 를 내면 G5 가 note 를 남긴다 -------------


def _spec_issue(**overrides) -> IssueRecord:
    values = {"lab_review_result": "lab_inspec", "issue_type": TYPE_SPEC_NOT_BUG, "issue_type_candidates": (TYPE_SPEC_NOT_BUG,)}
    values.update(overrides)
    return _issue(**values)


# Validates: REQ-QAAGENT-010
def test_g5_notes_new_tc_for_spec_issue() -> None:
    result = evaluate_g5(_spec_issue(), [{"kind": "tc_coverage", "tc_id": "TC-1", "text": "x", "evidence": ["c1"]}], new_tc_ids=["TC-1"])
    note = next(note for note in result.notes if note.code == "new_tc_for_spec_issue")
    assert note.severity == "note"
    assert "TC-1" in note.message


# Validates: REQ-QAAGENT-010
def test_g5_does_not_note_new_tc_for_program_fixed_issue() -> None:
    result = evaluate_g5(_issue(), [{"kind": "tc_coverage", "tc_id": "TC-1", "text": "x", "evidence": ["c1"]}], new_tc_ids=["TC-1"])
    assert not any(note.code == "new_tc_for_spec_issue" for note in result.notes)


# Validates: REQ-QAAGENT-010, NFR-QAAGENT-001
def test_pipeline_notes_new_tc_judgment_for_spec_issue(patched, storage) -> None:
    response = _response()
    response["tc_coverage"][0]["judgment"] = "NEW_TC"
    patched["state"]["response"] = response
    result = _analyzer(patched, storage).run(product="Acme", issue=_spec_issue(), context=_ready_context())

    codes = [note["code"] for note in result.gates["G5"]["notes"]]
    assert "new_tc_for_spec_issue" in codes


# --- MISMATCH 3 / 8-5: QA 가 이슈 유형을 고른다 -------------------------------------


# Validates: REQ-ISSUE-004, REQ-QAAGENT-005
def test_qa_chosen_issue_type_clears_g1_type_question(patched, storage) -> None:
    issue = _issue(lab_review_result="lab_noact", issue_type=TYPE_UNCLASSIFIED, issue_type_candidates=(TYPE_SPEC_NOT_BUG, TYPE_CANNOT_REPRODUCE))
    result = _analyzer(patched, storage).run(product="Acme", issue=issue, context=_ready_context(qa_issue_type=TYPE_CANNOT_REPRODUCE))

    g1_codes = [note["code"] for note in result.gates["G1"]["notes"]]
    assert "issue_type_unconfirmed" not in g1_codes
    assert "issue_type_by_qa" in g1_codes
    assert result.issue["type"] == TYPE_CANNOT_REPRODUCE
    assert result.issue["type_source"] == "qa"
    assert result.issue["needs_type_confirmation"] is False
    assert result.proceeded_to_ai is True


# Validates: REQ-ISSUE-004
def test_without_qa_choice_ambiguous_type_still_waits(patched, storage) -> None:
    issue = _issue(lab_review_result="lab_noact", issue_type=TYPE_UNCLASSIFIED, issue_type_candidates=(TYPE_SPEC_NOT_BUG, TYPE_CANNOT_REPRODUCE))
    result = _analyzer(patched, storage).run(product="Acme", issue=issue, context=_ready_context())

    assert result.gates["G1"]["status"] == NEED_INPUT
    assert result.issue["type_source"] == "lab_review"
    assert patched["calls"] == []


# Validates: REQ-QAAGENT-001
def test_qa_issue_type_survives_request_snapshot() -> None:
    """예약 복구(resume_queued_jobs)는 저장한 context 로 ExecutionContext 를 다시 만든다."""
    context = router_module._context_from_form("yes", "unknown", "unknown", "yes", [], [], False, "", TYPE_SPEC_NOT_BUG)
    snapshot = router_module._context_snapshot(context)
    assert ExecutionContext(**snapshot).qa_issue_type == TYPE_SPEC_NOT_BUG


# --- MISMATCH 6: 읽지 못한 문서를 G2 와 결과 화면에 보인다 -------------------------


def _knowledge_with_failure() -> LoadedKnowledge:
    knowledge = _knowledge()
    knowledge.specification_documents = knowledge.specification_documents + [
        {"id": 3, "kind": "specification", "name": "Acme 사양서2", "product": "Acme", "version": "1.0", "revision": "", "created_at": ""}
    ]
    knowledge.failures = [{"kind": "specification", "id": 3, "name": "Acme 사양서2", "reason": "파싱 실패"}]
    return knowledge


# Validates: REQ-QAAGENT-003, NFR-QAAGENT-001
def test_g2_notes_unreadable_documents() -> None:
    result = evaluate_g2(_issue(linked_srs=[]), evidence_count=3, exact_evidence_count=1, unreadable_documents=["Acme 사양서2"])
    note = next(note for note in result.notes if note.code == "documents_unreadable")
    assert "Acme 사양서2" in note.message


# Validates: REQ-QAAGENT-003, NFR-QAAGENT-001
def test_pipeline_reports_unreadable_specification_in_g2(patched, storage) -> None:
    patched["state"]["knowledge"] = _knowledge_with_failure()
    result = _analyzer(patched, storage).run(product="Acme", issue=_issue(), context=_ready_context())

    g2 = result.gates["G2"]
    assert any(note["code"] == "documents_unreadable" for note in g2["notes"])
    # 읽지 못한 문서는 "조사한 사양서" 비교의 분모에서 뺀다.
    assert not any(note["code"] == "split_spec_partial" and "2건 중" in note["message"] for note in g2["notes"])


# Validates: REQ-QAAGENT-005
def test_g1_says_documents_were_unreadable_when_none_could_be_read() -> None:
    result = evaluate_g1(_issue(), specification_count=0, testcase_count=5, rules_available=True, unreadable_specification_count=2)
    note = next(note for note in result.notes if note.code == "no_specification")
    assert result.status == BLOCK
    assert "2건" in note.message and "읽지 못" in note.message


# --- MISMATCH 8: 여러 권 사양서 문장 --------------------------------------------------


# Validates: REQ-QAAGENT-005
def test_split_spec_note_says_where_evidence_came_from() -> None:
    result = evaluate_g2(_issue(linked_srs=[]), evidence_count=3, exact_evidence_count=1, searched_document_count=2, total_document_count=5)
    note = next(note for note in result.notes if note.code == "split_spec_partial")
    assert "근거가 사양서 5건 중 2건에서만 나왔습니다" in note.message
    assert "조사했습니다" not in note.message
    assert "조사 완료 전" not in note.message


# --- 화면·API ------------------------------------------------------------------------


@pytest.fixture
def web(monkeypatch: pytest.MonkeyPatch, storage: Storage):
    monkeypatch.setattr(router_module, "storage", storage)
    return TestClient(app)


def _done_analysis(storage: Storage, job_id: str = "job-1", **result_overrides) -> None:
    result = {
        "analysis_id": job_id,
        "product": "Acme",
        "issue_id": "AP-1001",
        "status": "DONE",
        "issue": {"issue_id": "AP-1001", "type_label": "A. Program Fixed"},
        "gates": {},
        "evidence": {"claims": [{"kind": "tc_coverage", "label": "TC-100", "text": "x", "evidence": [], "findings": []}]},
        "decision": {},
    }
    result.update(result_overrides)
    storage.create_analysis(job_id, module="qa_agent", request={"product": "Acme"})
    storage.update_analysis(job_id, "DONE", result=result)


# MISMATCH 7
# Validates: REQ-QAAGENT-012
def test_approve_returns_to_the_tab_the_form_was_on(web, storage) -> None:
    _done_analysis(storage)
    response = web.post(
        "/qa-agent/analyses/job-1/approve",
        data={"claim_kind": "tc_coverage", "claim_label": "TC-100", "qa_decision": "APPROVED", "return_tab": "tc"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/qa-agent/analyses/job-1?tab=tc"


# Validates: REQ-QAAGENT-012
def test_approve_defaults_to_action_tab_and_ignores_unknown_tab(web, storage) -> None:
    _done_analysis(storage)
    response = web.post(
        "/qa-agent/analyses/job-1/approve",
        data={"claim_kind": "tc_coverage", "claim_label": "TC-100", "qa_decision": "APPROVED", "return_tab": "evil\"><script>"},
        follow_redirects=False,
    )
    assert response.headers["location"] == "/qa-agent/analyses/job-1?tab=action"


# MISMATCH 5 / 8-6
# Validates: REQ-QAAGENT-012
def test_rewriting_a_decision_keeps_the_previous_one(web, storage) -> None:
    _done_analysis(storage)
    for decision, note in (("REJECTED", "근거 부족"), ("APPROVED", "재확인")):
        web.post(
            "/qa-agent/analyses/job-1/approve",
            data={"claim_kind": "tc_coverage", "claim_label": "TC-100", "qa_decision": decision, "qa_note": note},
            follow_redirects=False,
        )

    history = approval_history.history_map(storage, "job-1")["tc_coverage|TC-100"]
    assert [row["qa_decision"] for row in history] == ["APPROVED", "REJECTED"]  # 최신이 먼저
    assert storage.qa_agent_approval_map("job-1")[("tc_coverage", "TC-100")]["qa_decision"] == "APPROVED"

    page = web.get("/qa-agent/analyses/job-1?tab=action").text
    assert "결정 기록 2건" in page
    assert "근거 부족" in page


# MISMATCH 6 (화면)
# Validates: REQ-QAAGENT-011
def test_result_screen_lists_unreadable_documents(web, storage) -> None:
    _done_analysis(storage, knowledge_failures=[{"kind": "specification", "id": 3, "name": "Acme 사양서2", "reason": "파싱 실패"}])
    page = web.get("/qa-agent/analyses/job-1").text
    assert "읽지 못한 문서" in page
    assert "Acme 사양서2" in page


# MISMATCH 11
# Validates: REQ-QAAGENT-001
def test_issue_list_escapes_folder_names(web, monkeypatch) -> None:
    monkeypatch.setattr(QaAgentAnalyzer, "available_issue_ids", lambda self, product: ['VP-1"><b>x'])
    body = web.get("/qa-agent/issues?product=Acme").text
    assert "<b>" not in body
    assert "&quot;&gt;&lt;b&gt;x" in body


def _readiness(export_available: bool) -> dict:
    return {
        "product": "Acme",
        "specifications": 1,
        "testcases": 1,
        "rules_available": True,
        "rule_revision": "Rev.1",
        "rule_documents": {},
        "knowledge_synced_at": "",
        "issue_export_available": export_available,
        "issue_export_dir": "",
    }


# MISMATCH 9
# Validates: REQ-QAAGENT-001
def test_home_without_export_folder_asks_for_file_only(web, monkeypatch, storage) -> None:
    monkeypatch.setattr(router_module, "_product_readiness", lambda product: _readiness(False))
    monkeypatch.setattr(QaAgentAnalyzer, "available_issue_ids", lambda self, product: [])
    page = web.get("/qa-agent/issue-analysis?product=VXvue").text
    assert 'name="issue_id"' not in page
    assert "Issue ID를 직접 입력" not in page
    assert "backup.json" in page


# MISMATCH 9
# Validates: REQ-QAAGENT-001
def test_start_rejects_issue_id_only_when_export_folder_is_missing(web, monkeypatch) -> None:
    monkeypatch.setattr(router_module, "_issue_export_available", lambda product: False)
    response = web.post("/qa-agent/analyses", data={"product": "Acme", "issue_id": "VP-1"})
    assert response.status_code == 400
    assert "backup.json" in response.json()["detail"]


# 8-5 (화면)
# Validates: REQ-QAAGENT-001, REQ-ISSUE-004
def test_home_offers_issue_type_choice(web, monkeypatch) -> None:
    monkeypatch.setattr(router_module, "_product_readiness", lambda product: _readiness(True))
    monkeypatch.setattr(QaAgentAnalyzer, "available_issue_ids", lambda self, product: ["VP-1"])
    page = web.get("/qa-agent/issue-analysis?product=VXvue").text
    assert 'name="qa_issue_type"' in page
    assert "G. Cannot Reproduce" in page


# Validates: REQ-QAAGENT-001
def test_start_rejects_unknown_issue_type(web, monkeypatch) -> None:
    monkeypatch.setattr(router_module, "_issue_export_available", lambda product: True)
    response = web.post("/qa-agent/analyses", data={"product": "Acme", "issue_id": "VP-1", "qa_issue_type": "Z_WHATEVER"})
    assert response.status_code == 400


# 8-8
# Validates: REQ-QAAGENT-001, NFR-IMPACT-001
def test_start_uses_the_core_daily_token_status(web, monkeypatch) -> None:
    """하루 한도는 app.core.usage 의 한국 시간 기준 함수로 검사한다 (옛 UTC 함수를 쓰지 않는다)."""
    import app.core.usage as usage

    seen: dict = {}

    def fake_status(storage=None, now=None) -> dict:
        seen["storage"] = storage
        return {"used": 900, "limit": 500, "exceeded": True}

    monkeypatch.setattr(usage, "daily_token_status", fake_status)
    monkeypatch.setattr(router_module, "_issue_export_available", lambda product: True)
    response = web.post("/qa-agent/analyses", data={"product": "Acme", "issue_id": "VP-1"})
    assert response.status_code == 429
    assert seen["storage"] is router_module.storage


# 8-10
# Validates: REQ-QAAGENT-003, NFR-IMPACT-001
def test_failed_run_keeps_spent_tokens(monkeypatch, storage) -> None:
    class _Client:
        token_usage = {"prompt_tokens": 100, "candidates_tokens": 23, "total_tokens": 123}

    class _FailingAnalyzer:
        def __init__(self, storage=None) -> None:
            self.ai_client = _Client()

        def run(self, **kwargs):
            raise RuntimeError("검증 중 오류")

    monkeypatch.setattr(router_module, "storage", storage)
    monkeypatch.setattr(router_module, "QaAgentAnalyzer", _FailingAnalyzer)
    storage.create_analysis("job-f", module="qa_agent", request={"product": "Acme"})

    router_module._run_job("job-f", "Acme", "VP-1", "", ExecutionContext(), "")

    analysis = storage.get_analysis("job-f")
    assert analysis["status"] == "FAILED"
    assert analysis["error"] == "검증 중 오류"
    assert analysis["result"]["token_usage"]["total_tokens"] == 123
    assert storage.tokens_used_since("2000-01-01T00:00:00+00:00") == 123


# Validates: REQ-QAAGENT-003
def test_failed_run_before_ai_call_leaves_no_result(monkeypatch, storage) -> None:
    class _Client:
        token_usage: dict = {}

    class _FailingAnalyzer:
        def __init__(self, storage=None) -> None:
            self.ai_client = _Client()

        def run(self, **kwargs):
            raise ValueError("Issue 를 찾을 수 없습니다: VP-1")

    monkeypatch.setattr(router_module, "storage", storage)
    monkeypatch.setattr(router_module, "QaAgentAnalyzer", _FailingAnalyzer)
    storage.create_analysis("job-g", module="qa_agent", request={"product": "Acme"})

    router_module._run_job("job-g", "Acme", "VP-1", "", ExecutionContext(), "")

    analysis = storage.get_analysis("job-g")
    assert analysis["status"] == "FAILED"
    assert analysis.get("result") is None


# --- MISMATCH 4: 조사 범위 글은 AI 참고와 화면 표시에만 쓴다 (사용법 화면) ---------------


# Validates: REQ-QAAGENT-014
def test_guide_explains_scope_note_does_not_narrow_search(web) -> None:
    page = web.get("/qa-agent/guide").text
    assert "검색 대상을 줄이지 않습니다" in page
    assert "그 범위 밖은 “확정 불가”로 남습니다" not in page
