from app.core.storage import Storage
from app.modules.manual_review.ai_client import ManualReviewAIClient
from app.modules.manual_review.docx_track_changes import TrackedChange
from app.modules.manual_review.schemas import ManualJudgment


def _responder(quick_response, detail_response):
    def responder(prompt: str) -> dict:
        if '"stage": "quick"' in prompt:
            return quick_response
        return detail_response

    return responder


def test_pass_decision_skips_detail_call(tmp_path):
    storage = Storage(tmp_path / "app.db")
    responder = _responder(
        {"decision": "PASS", "confidence": 0.95, "reason_codes": [], "requires_detail_generation": False},
        {"problem": "should not be called"},
    )
    client = ManualReviewAIClient(storage, responder=responder)
    change = TrackedChange("insertion", "연구소", "2026-08-01", "신규 문구 추가", 0)

    result = client.judge(change, [])

    assert result.decision is ManualJudgment.PASS
    assert result.problem == ""
    assert client.request_count == 1


def test_non_pass_decision_triggers_detail_call(tmp_path):
    storage = Storage(tmp_path / "app.db")
    responder = _responder(
        {"decision": "SUPPLEMENT_REQUIRED", "confidence": 0.6, "reason_codes": ["SPEC_CONDITION_MISSING"], "requires_detail_generation": True},
        {"problem": "조건 누락", "recommended_manual_text": "조건 추가", "qa_comment": "QA comment", "evidence": [], "needs_human_review": False},
    )
    client = ManualReviewAIClient(storage, responder=responder)
    change = TrackedChange("insertion", "연구소", "2026-08-01", "다른 신규 문구", 0)

    result = client.judge(change, [])

    assert result.decision is ManualJudgment.SUPPLEMENT_REQUIRED
    assert result.problem == "조건 누락"
    assert result.qa_comment == "QA comment"
    assert client.request_count == 2


def test_repeat_judge_uses_cache_and_makes_no_new_calls(tmp_path):
    storage = Storage(tmp_path / "app.db")
    responder = _responder(
        {"decision": "PASS", "confidence": 0.9, "reason_codes": [], "requires_detail_generation": False},
        {},
    )
    change = TrackedChange("insertion", "연구소", "2026-08-01", "캐시 확인용 문구", 0)

    first = ManualReviewAIClient(storage, responder=responder)
    first.judge(change, [])

    second = ManualReviewAIClient(storage, responder=lambda _: (_ for _ in ()).throw(AssertionError("cache miss — responder should not be called")))
    result = second.judge(change, [])

    assert result.decision.value == "PASS"


# Validates: REQ-MANUAL-011
def test_payload_includes_section_heading_and_paragraph_text(tmp_path):
    import json

    client = ManualReviewAIClient(Storage(tmp_path / "app.db"), responder=lambda _: {})
    change = TrackedChange("insertion", "연구소", "2026-08-01", "10분", 3, section_heading="잠금 정책", paragraph_text="가" * 1500)

    payload = json.loads(client._payload("quick", change, []))

    assert payload["manual_change"]["section_heading"] == "잠금 정책"
    assert len(payload["manual_change"]["paragraph_text"]) == 1000


# Validates: REQ-MANUAL-011
def test_prompts_follow_document_change_review_rules():
    from app.core.prompt_manager import load_prompt

    for name in ("manual_revision_quick", "manual_revision_detail"):
        prompt = load_prompt(name)
        assert prompt.version == 4
        for phrase in ("공통 설정 Coverage 규칙", "Manual-worthy Gate", "COVERED_ELSEWHERE", "NOT_MANUAL_WORTHY", "SPEC_CONFLICT", "section_heading"):
            assert phrase in prompt.system_instruction, (name, phrase)
