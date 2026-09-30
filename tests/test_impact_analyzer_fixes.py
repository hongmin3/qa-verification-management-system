"""Regression 영향 분석의 사양–코드 불일치 수정 (docs/SPEC_CODE_MISMATCH.md 1절, SPEC 13.1)."""

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.core import usage
from app.core.knowledge_documents import LoadedKnowledge
from app.core.storage import Storage
from app.main import app
from app.modules.impact_analyzer import regression_analyzer as analyzer_module
from app.modules.impact_analyzer import router as routes
from app.modules.impact_analyzer.change_analyzer import analyze_change_rules
from app.modules.impact_analyzer.html_report import create_html_report
from app.modules.impact_analyzer.regression_analyzer import RegressionAnalyzer
from app.modules.impact_analyzer.schemas import (
    ANALYSIS_STAGES,
    AnalysisResult,
    ChangeAnalysis,
    ImpactDecision,
    RevisionMark,
    SpecificationChunk,
    TestCase,
)
from app.modules.impact_analyzer.validation import attach_specification_references, classify_confidence


class _FakeStorage:
    def update_stage(self, *args, **kwargs):
        return None

    def latest_sync(self, *args, **kwargs):
        return None


def _fake_ai(decisions: list[dict]):
    return SimpleNamespace(
        analyze=lambda change, cases, chunks: [ImpactDecision.model_validate(item) for item in decisions],
        change_items=[], draft_test_cases=[], token_usage={"total_tokens": 10}, prompt_version=1,
        audit_snapshot={}, request_count=1,
    )


def _no_outputs(monkeypatch):
    monkeypatch.setattr(analyzer_module, "create_html_report", lambda result: None)
    monkeypatch.setattr(analyzer_module, "create_xlsx_export", lambda result: None)
    monkeypatch.setattr(analyzer_module, "create_tc_draft_markdown", lambda result: None)


def _decision(tc_id: str, **extra) -> dict:
    base = {"tc_id": tc_id, "impact": "HIGH", "confidence": .9, "reason": "영향", "relevant_specifications": ["spec-p1-0"], "recommended": True}
    base.update(extra)
    return base


# Validates: REQ-IMPACT-010 (MISMATCH 1-8: AI에게 보낸 TC 후보만 판정으로 받는다)
def test_decision_for_tc_not_sent_to_ai_is_dropped(monkeypatch):
    _no_outputs(monkeypatch)
    cases = [TestCase(tc_id="TC-SENT", feature="로그인"), TestCase(tc_id="TC-NOT-SENT", feature="출력")]
    monkeypatch.setattr(analyzer_module, "select_candidates_with_scores", lambda change, all_cases, limit: [(cases[0], 1.0)])
    chunks = [SpecificationChunk(chunk_id="spec-p1-0", document_id="spec", page=1, text="로그인 변경")]
    analyzer = RegressionAnalyzer(ai_client=_fake_ai([_decision("TC-SENT"), _decision("TC-NOT-SENT")]), storage=_FakeStorage())

    result = analyzer._execute([], chunks, cases, "", "spec.pdf", "tc.xlsx", "cand-test", user_notes="로그인 변경")

    assert [item.tc_id for item in result.decisions] == ["TC-SENT"]


# Validates: REQ-IMPACT-011 (MISMATCH 1-7: 취소선 근거는 검토 상태도 사람 확인 필요)
def test_strikethrough_evidence_sets_manual_review_status():
    decision = classify_confidence(ImpactDecision(tc_id="TC-1", impact="HIGH", confidence=.95, reason="r", relevant_specifications=["c1"]))
    assert decision.review_status == "AI_RECOMMENDATION_ACCEPTED"
    chunks = [SpecificationChunk(chunk_id="c1", document_id="spec", page=3, text="삭제된 기능", revision_marks=[RevisionMark.STRIKETHROUGH_DETECTED])]

    [result] = attach_specification_references([decision], chunks, {"spec": "spec.pdf"})

    assert result.manual_review_required is True
    assert result.review_status == "MANUAL_REVIEW_REQUIRED"


# Validates: REQ-IMPACT-005 (MISMATCH 1-3: 읽지 못한 문서를 결과에 남기고 사용한 문서로 적지 않는다)
def test_unreadable_knowledge_documents_are_reported_not_listed_as_used(monkeypatch):
    _no_outputs(monkeypatch)
    spec_ok = {"id": 1, "kind": "specification", "product": "VXvue", "version": "1", "revision": "", "name": "good-spec.pdf", "created_at": "t"}
    spec_bad = {"id": 2, "kind": "specification", "product": "VXvue", "version": "1", "revision": "", "name": "broken-spec.pdf", "created_at": "t"}
    tc_doc = {"id": 3, "kind": "testcase", "product": "VXvue", "version": "1", "revision": "", "name": "tc.xlsx", "created_at": "t"}
    knowledge = LoadedKnowledge(
        product="VXvue",
        chunks=[SpecificationChunk(chunk_id="spec-p1-0", document_id="good-spec", page=1, text="로그인")],
        cases=[TestCase(tc_id="TC-1", feature="로그인")],
        specification_documents=[spec_ok, spec_bad],
        testcase_documents=[tc_doc],
        failures=[{"kind": "specification", "id": 2, "name": "broken-spec.pdf", "reason": "파일 없음"}],
    )
    monkeypatch.setattr(analyzer_module, "load_for_product", lambda product, storage=None, with_text=False: knowledge)
    analyzer = RegressionAnalyzer(ai_client=_fake_ai([_decision("TC-1")]), storage=_FakeStorage())

    result = analyzer.run_for_product([], "VXvue", analysis_id="fail-docs", user_notes="로그인 변경")

    assert result.knowledge_failures == [{"kind": "specification", "id": 2, "name": "broken-spec.pdf", "reason": "파일 없음"}]
    assert "broken-spec.pdf" not in result.specification_file
    assert "good-spec.pdf" in result.specification_file
    assert [doc["name"] for doc in result.knowledge_documents] == ["good-spec.pdf", "tc.xlsx"]


def _result(analysis_id: str, **extra) -> AnalysisResult:
    return AnalysisResult(
        analysis_id=analysis_id, created_at=datetime.now(timezone.utc), change_file="c.pdf",
        specification_file="good-spec.pdf", testcase_file="tc.xlsx", change=ChangeAnalysis(),
        total_tc=1, candidate_tc=1, decisions=[], **extra,
    )


# Validates: REQ-IMPACT-013, REQ-IMPACT-005 (MISMATCH 1-3: 보고서 1절에 읽지 못한 문서)
def test_report_overview_lists_unreadable_documents():
    failures = [{"kind": "specification", "id": 2, "name": "broken-spec.pdf", "reason": "파일 없음"}]
    text = create_html_report(_result("report-failures", knowledge_failures=failures)).read_text(encoding="utf-8")
    overview = text.split("2. Change Summary")[0]
    assert "읽지 못한 문서" in overview
    assert "broken-spec.pdf" in overview


def test_report_overview_hides_unreadable_block_when_all_documents_read():
    text = create_html_report(_result("report-no-failures")).read_text(encoding="utf-8")
    assert "읽지 못한 문서" not in text


# Validates: REQ-IMPACT-005 (MISMATCH 1-3: 분석 상세 화면에 읽지 못한 문서)
def test_analysis_detail_shows_unreadable_documents(monkeypatch, tmp_path):
    persisted = Storage(tmp_path / "app.db")
    persisted.create_analysis("fail-view", request={"product": "VXvue"})
    failures = [{"kind": "specification", "id": 2, "name": "broken-spec.pdf", "reason": "파일 없음"}]
    persisted.update_analysis("fail-view", "DONE", result=_result("fail-view", knowledge_failures=failures).model_dump(mode="json"))
    monkeypatch.setattr(routes, "storage", persisted)

    text = TestClient(app).get("/analyses/fail-view/view").text

    assert "읽지 못한 문서" in text
    assert "broken-spec.pdf" in text
    assert "파일 없음" in text


# Validates: REQ-IMPACT-017 (MISMATCH 1-4: 후보 상한은 설정값을 보인다)
def test_analysis_detail_shows_configured_candidate_limit(monkeypatch, tmp_path):
    persisted = Storage(tmp_path / "app.db")
    persisted.create_analysis("limit-view", request={"product": "VXvue"})
    persisted.update_analysis("limit-view", "DONE", result=_result("limit-view").model_dump(mode="json"))
    monkeypatch.setattr(routes, "storage", persisted)
    monkeypatch.setitem(routes.get_settings().raw.setdefault("retrieval", {}), "candidate_limit", 77)

    text = TestClient(app).get("/analyses/limit-view/view").text

    assert "최대 77개" in text
    assert "앞 77개" in text
    assert "150개" not in text


def _failed_job(storage: Storage, job_id: str = "failed") -> None:
    storage.create_analysis(job_id, request={"product": "VXvue", "user_notes": "로그인 확인", "change_files": [], "change_paths": []})
    storage.update_analysis(job_id, "FAILED", error="temporary")


# Validates: REQ-IMPACT-018 (MISMATCH 1-5: 화면 버튼으로 재실행하면 새 분석 상세로 이동)
def test_retry_form_submit_redirects_to_new_analysis_view(monkeypatch, tmp_path):
    persisted = Storage(tmp_path / "app.db")
    _failed_job(persisted)
    monkeypatch.setattr(routes, "storage", persisted)
    monkeypatch.setattr(routes, "_run_job", lambda *args, **kwargs: None)

    response = TestClient(app).post("/analyses/failed/retry", data={"from_view": "1"}, follow_redirects=False)

    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("/analyses/") and location.endswith("/view")
    new_id = location.split("/")[2]
    assert new_id != "failed"
    assert persisted.get_analysis(new_id)["request"]["retry_of"] == "failed"


def test_retry_form_error_returns_to_detail_with_message(monkeypatch, tmp_path):
    persisted = Storage(tmp_path / "app.db")
    persisted.create_analysis("running", status="RUNNING", request={"product": "VXvue", "user_notes": "x"})
    monkeypatch.setattr(routes, "storage", persisted)
    client = TestClient(app)

    response = client.post("/analyses/running/retry", data={"from_view": "1"}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/analyses/running/view?retry_error=")
    page = client.get(response.headers["location"]).text
    assert "완료되거나 실패한 분석만 재실행할 수 있습니다." in page


def test_detail_retry_button_marks_form_submit(monkeypatch, tmp_path):
    persisted = Storage(tmp_path / "app.db")
    _failed_job(persisted)
    monkeypatch.setattr(routes, "storage", persisted)
    text = TestClient(app).get("/analyses/failed/view").text
    assert 'name="from_view"' in text


# Validates: NFR-IMPACT-001, REQ-IMPACT-018 (MISMATCH 1-9: 재실행도 하루 토큰 한도를 검사)
def test_retry_blocked_when_daily_token_limit_exceeded(monkeypatch, tmp_path):
    persisted = Storage(tmp_path / "app.db")
    used = _result("used-up").model_dump(mode="json")
    used["token_usage"] = {"total_tokens": 999}
    persisted.create_analysis("used-up")
    persisted.update_analysis("used-up", "DONE", result=used)
    _failed_job(persisted)
    monkeypatch.setattr(routes, "storage", persisted)
    monkeypatch.setattr(routes, "_run_job", lambda *args, **kwargs: None)
    monkeypatch.setitem(routes.get_settings().raw.setdefault("analysis", {}), "daily_token_limit", 500)

    response = TestClient(app).post("/analyses/failed/retry")

    assert response.status_code == 429
    assert persisted.active_analysis_count() == 0


# Validates: NFR-IMPACT-001 (SPEC 13.1-3 추천안: 하루는 한국 시간 0시부터, app.core.usage 로 계산)
def test_daily_token_status_uses_kst_day_start(monkeypatch):
    seen = {}

    class RecordingStorage:
        def tokens_used_since(self, since_iso):
            seen["since"] = since_iso
            return 0

    monkeypatch.setattr(routes, "storage", RecordingStorage())
    routes.daily_token_status()
    assert seen["since"] == usage.today_start_iso()


# Validates: NFR-IMPACT-001 (SPEC 13.1-3 / OPEN_QUESTIONS 8-10: 실패한 분석이 쓴 토큰도 합계에 넣는다)
def test_failed_analysis_keeps_token_usage_for_daily_total(monkeypatch, tmp_path):
    persisted = Storage(tmp_path / "app.db")
    persisted.create_analysis("boom", status="QUEUED")
    monkeypatch.setattr(routes, "storage", persisted)

    class FailingAnalyzer:
        def __init__(self):
            self.ai_client = SimpleNamespace(token_usage={"prompt_tokens": 30, "candidate_tokens": 10, "total_tokens": 40})

        def run_for_product(self, *args, **kwargs):
            raise RuntimeError("응답 형식 오류")

    monkeypatch.setattr(routes, "RegressionAnalyzer", FailingAnalyzer)

    routes._run_job("boom", [], "VXvue", "notes")

    job = persisted.get_analysis("boom")
    assert job["status"] == "FAILED"
    assert job["error"] == "응답 형식 오류"
    assert job["result"]["token_usage"]["total_tokens"] == 40
    assert persisted.tokens_used_since("1970-01-01T00:00:00+00:00") == 40


def test_failed_analysis_without_ai_call_stores_no_result(monkeypatch, tmp_path):
    persisted = Storage(tmp_path / "app.db")
    persisted.create_analysis("early", status="QUEUED")
    monkeypatch.setattr(routes, "storage", persisted)

    class EarlyFail:
        def __init__(self):
            self.ai_client = SimpleNamespace(token_usage={})

        def run_for_product(self, *args, **kwargs):
            raise ValueError("문서 없음")

    monkeypatch.setattr(routes, "RegressionAnalyzer", EarlyFail)
    routes._run_job("early", [], "VXvue", "")
    assert "result" not in persisted.get_analysis("early")


# Validates: REQ-IMPACT-006 (SPEC 13.1-1 추천안: 영어 변경 문서의 변경 낱말)
def test_english_change_lines_become_changed_features():
    change = analyze_change_rules("Added fingerprint login option\nImproved DICOM export speed\nFixed crash on startup\nCopyright notice")
    assert "Added fingerprint login option" in change.changed_features
    assert "Improved DICOM export speed" in change.changed_features
    assert "Fixed crash on startup" in change.changed_features
    assert "Copyright notice" not in change.changed_features


def test_english_words_match_whole_words_only():
    change = analyze_change_rules("Address book layout\nPrefix table")
    assert change.changed_features == []


# Validates: REQ-IMPACT-020, REQ-IMPACT-004 (MISMATCH 1-10: 사용법 화면의 진행 단계는 실제 8단계)
def test_guide_lists_every_analysis_stage():
    text = TestClient(app).get("/impact-analyzer/guide").text
    for name in ANALYSIS_STAGES:
        assert name in text
    assert "신규 TC 초안 검증" in text


def test_impact_analyzer_module_doc_does_not_claim_confidence_decides_recommendation():
    """OPEN_QUESTIONS 8-1: 추천 여부는 AI 값을 그대로 쓴다. 문서가 "0.80 이상은 추천"이라고 적지 않는다."""
    doc = (Path(__file__).resolve().parents[1] / "docs" / "modules" / "impact-analyzer.md").read_text(encoding="utf-8")
    assert "이상은 추천" not in doc
    assert "낮은\n확신을 조용히 추천으로 올리지 않는다" not in doc
