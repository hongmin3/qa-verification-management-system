from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.main import app
from app.core.storage import Storage
from app.modules.impact_analyzer import router as routes
from app.modules.knowledge import router as knowledge_routes
from app.modules.impact_analyzer.schemas import AnalysisResult, ChangeAnalysis


def _result(analysis_id: str) -> dict:
    return AnalysisResult(
        analysis_id=analysis_id,
        created_at=datetime.now(timezone.utc),
        change_file="change.docx",
        specification_file="spec.pdf",
        testcase_file="tc.xlsx",
        change=ChangeAnalysis(changed_features=["Display"]),
        total_tc=10,
        candidate_tc=2,
        decisions=[],
    ).model_dump(mode="json")


def test_completed_analysis_survives_storage_recreation(tmp_path):
    db_path = tmp_path / "app.db"
    first = Storage(db_path)
    first.create_analysis("job-1")
    first.update_analysis("job-1", "DONE", result=_result("job-1"))

    restored = Storage(db_path).get_analysis("job-1")

    assert restored is not None
    assert restored["status"] == "DONE"
    assert restored["result"]["analysis_id"] == "job-1"
    assert restored["result"]["change_file"] == "change.docx"


def test_analysis_evaluation_survives_storage_recreation(tmp_path):
    db_path = tmp_path / "app.db"
    storage = Storage(db_path)
    storage.create_analysis("evaluated")
    storage.update_analysis("evaluated", "DONE", result=_result("evaluated"))
    storage.save_analysis_evaluation("evaluated", ["TC-1", "TC-2"], "QA 확인")

    evaluation = Storage(db_path).get_analysis_evaluation("evaluated")

    assert evaluation["expected_tc_ids"] == ["TC-1", "TC-2"]
    assert evaluation["qa_note"] == "QA 확인"


def test_completed_analysis_accepts_qa_gold_and_renders_metrics(monkeypatch, tmp_path):
    persisted = Storage(tmp_path / "app.db")
    result = _result("evaluated-web")
    result["decisions"] = [{"tc_id": "TC-1", "recommended": True}]
    persisted.create_analysis("evaluated-web")
    persisted.update_analysis("evaluated-web", "DONE", result=result)
    monkeypatch.setattr(routes, "storage", persisted)
    client = TestClient(app)

    response = client.post(
        "/analyses/evaluated-web/evaluation",
        data={"expected_tc_ids": "TC-1\nTC-2", "qa_note": "실행 결과 확정"},
        follow_redirects=False,
    )
    detail = client.get("/analyses/evaluated-web/view")

    assert response.status_code == 303
    assert persisted.get_analysis_evaluation("evaluated-web")["expected_tc_ids"] == ["TC-1", "TC-2"]
    assert "Precision 100.0%" in detail.text
    assert "Recall 50.0%" in detail.text
    assert "TC-2" in detail.text


def test_incomplete_analyses_are_failed_after_restart(tmp_path):
    storage = Storage(tmp_path / "app.db")
    storage.create_analysis("queued")
    storage.create_analysis("running", status="RUNNING")
    storage.create_analysis("done")
    storage.update_analysis("done", "DONE", result=_result("done"))

    assert storage.fail_incomplete_analyses() == 2
    assert storage.get_analysis("queued")["status"] == "FAILED"
    assert storage.get_analysis("running")["error"] == "서버 재시작으로 분석이 중단되었습니다."
    assert storage.get_analysis("done")["status"] == "DONE"


def test_restart_failure_can_target_only_running_jobs(tmp_path):
    storage = Storage(tmp_path / "app.db")
    storage.create_analysis("queued")
    storage.create_analysis("running", status="RUNNING")
    assert storage.fail_running_analyses() == 1
    assert storage.get_analysis("queued")["status"] == "QUEUED"
    assert storage.get_analysis("running")["status"] == "FAILED"


def test_job_status_reads_persisted_analysis(monkeypatch, tmp_path):
    persisted = Storage(tmp_path / "app.db")
    persisted.create_analysis("restored")
    persisted.update_analysis("restored", "DONE", result=_result("restored"))
    monkeypatch.setattr(routes, "storage", persisted)

    response = TestClient(app).get("/analyses/restored")

    assert response.status_code == 200
    assert response.json()["result"]["analysis_id"] == "restored"


def test_create_analysis_initializes_stage_tracking(tmp_path):
    storage = Storage(tmp_path / "app.db")
    storage.create_analysis("job-stage", stage_total=8)

    job = storage.get_analysis("job-stage")

    assert job["stage"] == "대기 중"
    assert job["stage_index"] == 0
    assert job["stage_total"] == 8
    assert job["started_at"]


def test_analysis_request_snapshot_survives_storage_recreation(tmp_path):
    db_path = tmp_path / "app.db"
    storage = Storage(db_path)
    storage.create_analysis(
        "audited",
        request={"product": "VXvue", "user_notes": "로그인 변경 확인", "change_files": ["change.pdf"]},
    )

    restored = Storage(db_path).get_analysis("audited")

    assert restored["request"]["product"] == "VXvue"
    assert restored["request"]["change_files"] == ["change.pdf"]


def test_update_stage_advances_progress(tmp_path):
    storage = Storage(tmp_path / "app.db")
    storage.create_analysis("job-stage-2", stage_total=8)

    storage.update_stage("job-stage-2", 3, "TC 후보 검색", 8)
    job = storage.get_analysis("job-stage-2")

    assert job["stage"] == "TC 후보 검색"
    assert job["stage_index"] == 3


def test_sync_log_tracks_running_and_finish(tmp_path):
    storage = Storage(tmp_path / "app.db")
    assert storage.is_sync_running("VXvue", "specification") is False

    sync_id = storage.sync_start("VXvue", "specification", "alm_crawler")
    assert storage.is_sync_running("VXvue", "specification") is True

    storage.sync_finish(sync_id, "SUCCESS", "3 files updated")
    assert storage.is_sync_running("VXvue", "specification") is False
    latest = storage.latest_sync("VXvue", "specification")
    assert latest["status"] == "SUCCESS"
    assert latest["detail"] == "3 files updated"


def test_active_documents_keeps_multiple_distinct_documents(tmp_path):
    storage = Storage(tmp_path / "app.db")
    storage.add_document("specification", "VXvue", "1.0", "Rev.1", "사양서1.pdf", tmp_path / "s1.pdf")
    storage.add_document("specification", "VXvue", "1.0", "Rev.2", "사양서2.pdf", tmp_path / "s2.pdf")
    storage.add_document("specification", "VXvue", "1.0", "Rev.3", "사양서3.pdf", tmp_path / "s3.pdf")

    docs = storage.active_documents("specification", "VXvue")

    assert {d["name"] for d in docs} == {"사양서1.pdf", "사양서2.pdf", "사양서3.pdf"}


def test_active_analysis_count_tracks_queue_and_running(tmp_path):
    storage = Storage(tmp_path / "app.db")
    storage.create_analysis("queued")
    storage.create_analysis("running", status="RUNNING")
    storage.create_analysis("done")
    storage.update_analysis("done", "DONE", result=_result("done"))
    assert storage.active_analysis_count() == 2


def test_record_sync_log_endpoint(monkeypatch, tmp_path):
    persisted = Storage(tmp_path / "app.db")
    monkeypatch.setattr(knowledge_routes, "storage", persisted)

    response = TestClient(app).post("/knowledge/sync-log", data={"product": "VXvue", "kind": "specification", "source": "alm_crawler", "status": "SUCCESS", "detail": "3 files"})

    assert response.status_code == 200
    latest = persisted.latest_sync("VXvue", "specification")
    assert latest["status"] == "SUCCESS"


def test_trigger_specification_sync_blocked_when_unavailable(monkeypatch, tmp_path):
    persisted = Storage(tmp_path / "app.db")
    monkeypatch.setattr(knowledge_routes, "storage", persisted)
    import app.modules.impact_analyzer.vxvue_spec_sync as vxvue_spec
    monkeypatch.setattr(vxvue_spec, "is_available_on_this_host", lambda *a, **k: False)

    response = TestClient(app).post("/knowledge/sync/specification")

    assert response.status_code == 400


def test_trigger_specification_sync_blocked_when_already_running(monkeypatch, tmp_path):
    persisted = Storage(tmp_path / "app.db")
    persisted.sync_start("VXvue", "specification", "alm_crawler")
    monkeypatch.setattr(knowledge_routes, "storage", persisted)

    response = TestClient(app).post("/knowledge/sync/specification")

    assert response.status_code == 409


def test_start_analysis_requires_file_or_notes():
    response = TestClient(app).post("/analyses", data={"product": "VXvue"})
    assert response.status_code == 400


def test_delete_document_removes_row_and_file(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "app.db")
    file_path = tmp_path / "spec.pdf"
    file_path.write_bytes(b"%PDF-")
    doc_id = storage.add_document("specification", "VXvue", "1.0", "Rev.1", "spec.pdf", file_path)
    monkeypatch.setattr(knowledge_routes, "storage", storage)
    monkeypatch.setattr(knowledge_routes.document_cache, "delete", lambda document_id: None)

    response = TestClient(app).post(f"/knowledge/delete/{doc_id}", follow_redirects=False)

    assert response.status_code == 303
    assert storage.get_document(doc_id) is None
    assert not file_path.exists()


def test_delete_missing_document_returns_404(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "app.db")
    monkeypatch.setattr(knowledge_routes, "storage", storage)

    response = TestClient(app).post("/knowledge/delete/999", follow_redirects=False)

    assert response.status_code == 404


def test_tokens_used_since_sums_done_analyses(tmp_path):
    storage = Storage(tmp_path / "app.db")
    old = _result("old")
    old["token_usage"] = {"total_tokens": 100}
    new = _result("new")
    new["token_usage"] = {"total_tokens": 250}
    storage.create_analysis("old")
    storage.update_analysis("old", "DONE", result=old)
    storage.create_analysis("new")
    storage.update_analysis("new", "DONE", result=new)
    storage.create_analysis("running", status="RUNNING")

    assert storage.tokens_used_since("1970-01-01T00:00:00+00:00") == 350


def test_start_analysis_blocked_when_daily_token_limit_exceeded(monkeypatch, tmp_path):
    persisted = Storage(tmp_path / "app.db")
    used = _result("used-up")
    used["token_usage"] = {"total_tokens": 999}
    persisted.create_analysis("used-up")
    persisted.update_analysis("used-up", "DONE", result=used)
    monkeypatch.setattr(routes, "storage", persisted)
    routes.get_settings().raw.setdefault("analysis", {})["daily_token_limit"] = 500
    try:
        response = TestClient(app).post("/analyses", files={"change_files": ("c.pdf", b"%PDF-", "application/pdf")}, data={"product": "VXvue"})
        assert response.status_code == 429
    finally:
        routes.get_settings().raw["analysis"]["daily_token_limit"] = 0


def test_analysis_history_renders_persisted_results(monkeypatch, tmp_path):
    persisted = Storage(tmp_path / "app.db")
    persisted.create_analysis("history-job")
    persisted.update_analysis("history-job", "DONE", result=_result("history-job"))
    monkeypatch.setattr(routes, "storage", persisted)

    response = TestClient(app).get("/analyses")

    assert response.status_code == 200
    assert "history-job" in response.text
    assert "XLSX" in response.text
    assert f'/analyses/history-job/view' in response.text


def test_list_analyses_filters_by_status(tmp_path):
    storage = Storage(tmp_path / "app.db")
    storage.create_analysis("done-job")
    storage.update_analysis("done-job", "DONE", result=_result("done-job"))
    storage.create_analysis("failed-job", status="FAILED")

    rows, total = storage.list_analyses(status="DONE")

    assert total == 1
    assert [row["id"] for row in rows] == ["done-job"]


def test_list_analyses_filters_by_product(tmp_path):
    storage = Storage(tmp_path / "app.db")
    storage.create_analysis("vxvue-job", request={"product": "VXvue"})
    storage.update_analysis("vxvue-job", "DONE", result=_result("vxvue-job"))
    storage.create_analysis("other-job", request={"product": "Bellalun Viewer"})
    storage.update_analysis("other-job", "DONE", result=_result("other-job"))

    rows, total = storage.list_analyses(product="VXvue")

    assert total == 1
    assert rows[0]["id"] == "vxvue-job"


def test_list_analyses_search_matches_id_or_change_file(tmp_path):
    storage = Storage(tmp_path / "app.db")
    matching = _result("job-a")
    matching["change_file"] = "release-note.pdf"
    storage.create_analysis("job-a")
    storage.update_analysis("job-a", "DONE", result=matching)
    other = _result("job-b")
    other["change_file"] = "unrelated.pdf"
    storage.create_analysis("job-b")
    storage.update_analysis("job-b", "DONE", result=other)

    by_id, _ = storage.list_analyses(search="job-a")
    by_file, _ = storage.list_analyses(search="release-note")

    assert [row["id"] for row in by_id] == ["job-a"]
    assert [row["id"] for row in by_file] == ["job-a"]


def test_list_analyses_paginates_with_limit_and_offset(tmp_path):
    storage = Storage(tmp_path / "app.db")
    for index in range(5):
        job_id = f"job-{index}"
        storage.create_analysis(job_id)
        storage.update_analysis(job_id, "DONE", result=_result(job_id))

    first_page, total = storage.list_analyses(limit=2, offset=0)
    second_page, _ = storage.list_analyses(limit=2, offset=2)

    assert total == 5
    assert len(first_page) == 2
    assert len(second_page) == 2
    assert {row["id"] for row in first_page}.isdisjoint({row["id"] for row in second_page})


def test_analysis_history_status_filter_excludes_other_statuses(monkeypatch, tmp_path):
    persisted = Storage(tmp_path / "app.db")
    persisted.create_analysis("done-job")
    persisted.update_analysis("done-job", "DONE", result=_result("done-job"))
    persisted.create_analysis("failed-job", status="FAILED")
    monkeypatch.setattr(routes, "storage", persisted)

    response = TestClient(app).get("/analyses?status=FAILED")

    assert response.status_code == 200
    assert "failed-job" in response.text
    assert "done-job" not in response.text


def test_analysis_history_pagination_shows_second_page(monkeypatch, tmp_path):
    persisted = Storage(tmp_path / "app.db")
    for index in range(30):
        job_id = f"job-{index:02d}"
        persisted.create_analysis(job_id)
        persisted.update_analysis(job_id, "DONE", result=_result(job_id))
    monkeypatch.setattr(routes, "storage", persisted)
    client = TestClient(app)

    first_page = client.get("/analyses")
    second_page = client.get("/analyses?page=2")

    assert "1 / 2 페이지" in first_page.text
    assert "2 / 2 페이지" in second_page.text
    assert first_page.text != second_page.text


def test_analysis_detail_renders_documents_and_exact_prompt_audit(monkeypatch, tmp_path):
    persisted = Storage(tmp_path / "app.db")
    persisted.create_analysis(
        "detail-job",
        request={
            "product": "VXvue",
            "user_notes": "Viewer 로그인 회귀 확인",
            "change_files": ["release-note.pdf"],
            "knowledge_documents": [{"kind": "specification", "name": "VXvue SRS.pdf", "product": "VXvue", "version": "1.0", "revision": "R1", "created_at": "2026-09-01"}],
        },
    )
    result = _result("detail-job")
    result["ai_audit"] = {
        "model": "gemini-test",
        "prompt_name": "impact_analysis",
        "prompt_version": 1,
        "cache_hit": False,
        "generation": {"temperature": 0.1, "max_output_tokens": 100, "thinking_budget": 0},
        "system_instruction": "QA system instruction",
        "user_prompt": '{"change":"로그인"}',
        "response": {"decisions": [{"tc_id": "TC-LOGIN"}]},
    }
    persisted.update_analysis("detail-job", "DONE", result=result)
    monkeypatch.setattr(routes, "storage", persisted)

    response = TestClient(app).get("/analyses/detail-job/view")

    assert response.status_code == 200
    assert "Viewer 로그인 회귀 확인" in response.text
    assert "release-note.pdf" in response.text
    assert "VXvue SRS.pdf" in response.text
    assert "QA system instruction" in response.text
    assert "TC-LOGIN" in response.text


def test_missing_analysis_detail_returns_404(monkeypatch, tmp_path):
    monkeypatch.setattr(routes, "storage", Storage(tmp_path / "app.db"))
    assert TestClient(app).get("/analyses/missing/view").status_code == 404


def test_retry_analysis_creates_linked_job(monkeypatch, tmp_path):
    persisted = Storage(tmp_path / "app.db")
    persisted.create_analysis("failed", request={"product": "VXvue", "user_notes": "로그인 확인", "change_files": [], "change_paths": []})
    persisted.update_analysis("failed", "FAILED", error="temporary")
    monkeypatch.setattr(routes, "storage", persisted)
    monkeypatch.setattr(routes, "_run_job", lambda *args, **kwargs: None)

    response = TestClient(app).post("/analyses/failed/retry")

    assert response.status_code == 200
    retried = persisted.get_analysis(response.json()["job_id"])
    assert retried["request"]["retry_of"] == "failed"
    assert retried["status"] == "QUEUED"


def test_analysis_history_is_separated_by_module(tmp_path):
    """여러 기능이 같은 analyses 테이블을 쓴다. 필터가 없으면 한 기능의 이력 화면에
    다른 기능의 분석이 섞인다."""
    from app.core.storage import Storage

    storage = Storage(db_path=tmp_path / "modules.db")
    storage.create_analysis("a1", module="impact_analyzer")
    storage.create_analysis("a2", module="qa_agent")
    storage.create_analysis("a3", module="qa_agent")

    impact, impact_total = storage.list_analyses(module="impact_analyzer")
    agent, agent_total = storage.list_analyses(module="qa_agent")
    everything, all_total = storage.list_analyses()

    assert [row["id"] for row in impact] == ["a1"] and impact_total == 1
    assert {row["id"] for row in agent} == {"a2", "a3"} and agent_total == 2
    assert all_total == 3


def test_legacy_rows_without_module_belong_to_impact_analyzer(tmp_path):
    """module 컬럼이 없던 시절의 행은 그때 유일했던 기능의 것이다."""
    from app.core.storage import Storage

    storage = Storage(db_path=tmp_path / "legacy.db")
    storage.create_analysis("old", module="impact_analyzer")
    with storage.connect() as db:
        db.execute("UPDATE analyses SET module=NULL WHERE id='old'")

    impact, total = storage.list_analyses(module="impact_analyzer")

    assert [row["id"] for row in impact] == ["old"] and total == 1
    assert storage.list_analyses(module="qa_agent")[1] == 0
