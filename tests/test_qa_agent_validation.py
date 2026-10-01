"""QA Agent AI 응답 검사 (REQ-QAAGENT-008).

없앤 Regression 영향 분석에서 옮겨 온 규칙: 취소선이 있는 사양 조각을 근거로 한 TC 판정은
사람 확인 대상으로 돌린다. 취소선이 "그 사양이 없어졌다"는 뜻인지는 사람만 확정할 수 있다(QA 규칙 §11).
"""

from app.modules.qa_agent.schemas import QaAgentDecision, TcCoverageDecision
from app.modules.qa_agent.validation import validate_decision


def _decision(*entries: TcCoverageDecision) -> QaAgentDecision:
    return QaAgentDecision(tc_coverage=list(entries))


def test_struck_evidence_sends_tc_judgment_to_human_review():
    """Validates: REQ-QAAGENT-008 — 취소선 근거를 쓴 판정은 확신도를 사람 확인 기준 아래로 내리고 기록한다."""
    decision = _decision(
        TcCoverageDecision(tc_id="TC-1", judgment="KEEP", evidence_chunk_ids=["spec-p1-0"], confidence=0.92),
        TcCoverageDecision(tc_id="TC-2", judgment="KEEP", evidence_chunk_ids=["spec-p2-0"], confidence=0.92),
    )

    validated, report = validate_decision(
        decision, known_chunk_ids={"spec-p1-0", "spec-p2-0"}, known_tc_ids={"TC-1", "TC-2"},
        struck_chunk_ids={"spec-p1-0"},
    )

    by_id = {entry.tc_id: entry for entry in validated.tc_coverage}
    assert by_id["TC-1"].confidence == 0.59
    assert by_id["TC-2"].confidence == 0.92
    assert report.struck_evidence_tc_ids == ["TC-1"]
    assert report.has_findings
    assert report.as_dict()["struck_evidence_tc_ids"] == ["TC-1"]


def test_struck_evidence_is_ignored_when_not_given():
    """취소선 정보를 넘기지 않는 호출(이전 방식)은 결과가 바뀌지 않는다."""
    decision = _decision(TcCoverageDecision(tc_id="TC-1", judgment="KEEP", evidence_chunk_ids=["spec-p1-0"], confidence=0.92))

    validated, report = validate_decision(decision, known_chunk_ids={"spec-p1-0"}, known_tc_ids={"TC-1"})

    assert validated.tc_coverage[0].confidence == 0.92
    assert report.struck_evidence_tc_ids == []
