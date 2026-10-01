from fastapi.testclient import TestClient

from app.main import app
from app.core.storage import Storage
from app.modules.knowledge import router as knowledge_routes


def _result(analysis_id: str) -> dict:
    """저장소 시험용 분석 결과. 기능마다 결과 모양이 달라 저장소는 내용을 해석하지 않는다."""
    return {"analysis_id": analysis_id, "change_file": "change.docx", "decisions": []}

def test_completed_analysis_survives_storage_recreation(tmp_path):
    db_path = tmp_path / "app.db"
    first = Storage(db_path)
    first.create_analysis("job-1", module="qa_agent")
    first.update_analysis("job-1", "DONE", result=_result("job-1"))

    restored = Storage(db_path).get_analysis("job-1")

    assert restored is not None
    assert restored["status"] == "DONE"
    assert restored["result"]["analysis_id"] == "job-1"
    assert restored["result"]["change_file"] == "change.docx"


def test_incomplete_analyses_are_failed_after_restart(tmp_path):
    storage = Storage(tmp_path / "app.db")
    storage.create_analysis("queued", module="qa_agent")
    storage.create_analysis("running", status="RUNNING", module="qa_agent")
    storage.create_analysis("done", module="qa_agent")
    storage.update_analysis("done", "DONE", result=_result("done"))

    assert storage.fail_incomplete_analyses() == 2
    assert storage.get_analysis("queued")["status"] == "FAILED"
    assert storage.get_analysis("running")["error"] == "서버 재시작으로 분석이 중단되었습니다."
    assert storage.get_analysis("done")["status"] == "DONE"


def test_restart_failure_can_target_only_running_jobs(tmp_path):
    storage = Storage(tmp_path / "app.db")
    storage.create_analysis("queued", module="qa_agent")
    storage.create_analysis("running", status="RUNNING", module="qa_agent")
    assert storage.fail_running_analyses() == 1
    assert storage.get_analysis("queued")["status"] == "QUEUED"
    assert storage.get_analysis("running")["status"] == "FAILED"


def test_startup_releases_retired_queue_and_preserves_other_jobs(monkeypatch, tmp_path):
    """Validates: REQ-STORE-003 — 없앤 기능의 대기가 현재 기능의 실행 자리를 막지 않는다."""
    import asyncio
    import app.main as main_module

    storage = Storage(tmp_path / "app.db")
    request = {"product": "VXvue", "change_files": ["old.pdf"]}
    storage.create_analysis("retired", module="impact_analyzer", request=request)
    storage.create_analysis("legacy", module="impact_analyzer", request=request)
    with storage.connect() as db:
        db.execute("UPDATE analyses SET module=NULL WHERE id='legacy'")
    storage.create_analysis("old-done", module="impact_analyzer")
    result = {"token_usage": {"total_tokens": 12}}
    storage.update_analysis("old-done", "DONE", result=result)
    storage.create_analysis("agent-queued", module="qa_agent")
    storage.create_analysis("manual-queued", module="manual_review")
    monkeypatch.setattr(main_module, "storage", storage)
    # 다른 기능의 실제 대기 실행과 예약 작업은 이 임시 DB 시험에서 시작하지 않는다.
    for name in ("_ensure_configured_products", "resume_queued_manual_jobs", "resume_queued_qa_agent_jobs", "stop_scheduler"):
        monkeypatch.setattr(main_module, name, lambda: None)
    monkeypatch.setattr(main_module, "start_scheduler", lambda callbacks: None)

    async def restart():
        async with main_module.lifespan(None):
            assert storage.active_analysis_count() == 2
            for job_id in ("retired", "legacy"):
                job = storage.get_analysis(job_id)
                assert job["status"] == "FAILED"
                assert "기능이 제거" in job["error"]
                assert job["request"] == request
            assert storage.get_analysis("old-done")["result"] == result
            assert storage.get_analysis("old-done")["status"] == "DONE"
            assert storage.get_analysis("agent-queued")["status"] == "QUEUED"
            assert storage.get_analysis("manual-queued")["status"] == "QUEUED"

    asyncio.run(restart())
    asyncio.run(restart())  # 다시 시작해도 보관 내용과 현재 기능의 대기는 그대로다.


def test_create_analysis_initializes_stage_tracking(tmp_path):
    storage = Storage(tmp_path / "app.db")
    storage.create_analysis("job-stage", stage_total=8, module="qa_agent")

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
        request={"product": "VXvue", "user_notes": "로그인 변경 확인", "change_files": ["change.pdf"]}, module="qa_agent",
    )

    restored = Storage(db_path).get_analysis("audited")

    assert restored["request"]["product"] == "VXvue"
    assert restored["request"]["change_files"] == ["change.pdf"]


def test_update_stage_advances_progress(tmp_path):
    storage = Storage(tmp_path / "app.db")
    storage.create_analysis("job-stage-2", stage_total=8, module="qa_agent")

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
    storage.create_analysis("queued", module="qa_agent")
    storage.create_analysis("running", status="RUNNING", module="qa_agent")
    storage.create_analysis("done", module="qa_agent")
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
    import app.modules.knowledge.vxvue_spec_sync as vxvue_spec
    monkeypatch.setattr(vxvue_spec, "is_available_on_this_host", lambda *a, **k: False)

    response = TestClient(app).post("/knowledge/sync/specification")

    assert response.status_code == 400


def test_trigger_specification_sync_blocked_when_already_running(monkeypatch, tmp_path):
    persisted = Storage(tmp_path / "app.db")
    persisted.sync_start("VXvue", "specification", "alm_crawler")
    monkeypatch.setattr(knowledge_routes, "storage", persisted)

    response = TestClient(app).post("/knowledge/sync/specification")

    assert response.status_code == 409


def test_delete_document_removes_row_and_file(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "app.db")
    file_path = tmp_path / "tc.xlsx"
    file_path.write_bytes(b"PK")
    doc_id = storage.add_document("testcase", "VXvue", "1.0", "", "tc.xlsx", file_path)      # VXvue TC 출처는 manual
    monkeypatch.setattr(knowledge_routes, "storage", storage)
    monkeypatch.setattr(knowledge_routes.document_cache, "delete", lambda document_id: None)

    response = TestClient(app).post(f"/knowledge/delete/{doc_id}", data={"next": "/knowledge/products/vxvue"}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/knowledge/products/vxvue"
    assert storage.get_document(doc_id) is None
    assert not file_path.exists()


def test_delete_automatic_source_document_is_refused_without_its_source(tmp_path, monkeypatch):
    """Validates: REQ-KNOW-005 — ALM 자동 자료는 사람이 지울 수 없고, 동기화(source=alm_crawler)만 지운다."""
    storage = Storage(tmp_path / "app.db")
    file_path = tmp_path / "spec.pdf"
    file_path.write_bytes(b"%PDF-")
    doc_id = storage.add_document("specification", "VXvue", "1.0", "", "spec.pdf", file_path)
    monkeypatch.setattr(knowledge_routes, "storage", storage)
    monkeypatch.setattr(knowledge_routes.document_cache, "delete", lambda document_id: None)
    client = TestClient(app)

    refused = client.post(f"/knowledge/delete/{doc_id}", follow_redirects=False)
    assert refused.status_code == 409
    assert storage.get_document(doc_id) is not None and file_path.exists()

    allowed = client.post(f"/knowledge/delete/{doc_id}", data={"source": "alm_crawler", "next": "https://evil.example"}, follow_redirects=False)
    assert allowed.status_code == 303 and allowed.headers["location"] == "/knowledge"
    assert storage.get_document(doc_id) is None


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
    storage.create_analysis("old", module="qa_agent")
    storage.update_analysis("old", "DONE", result=old)
    storage.create_analysis("new", module="qa_agent")
    storage.update_analysis("new", "DONE", result=new)
    storage.create_analysis("running", status="RUNNING", module="qa_agent")

    assert storage.tokens_used_since("1970-01-01T00:00:00+00:00") == 350


def test_list_analyses_filters_by_status(tmp_path):
    storage = Storage(tmp_path / "app.db")
    storage.create_analysis("done-job", module="qa_agent")
    storage.update_analysis("done-job", "DONE", result=_result("done-job"))
    storage.create_analysis("failed-job", status="FAILED", module="qa_agent")

    rows, total = storage.list_analyses(status="DONE")

    assert total == 1
    assert [row["id"] for row in rows] == ["done-job"]


def test_list_analyses_filters_by_product(tmp_path):
    storage = Storage(tmp_path / "app.db")
    storage.create_analysis("vxvue-job", request={"product": "VXvue"}, module="qa_agent")
    storage.update_analysis("vxvue-job", "DONE", result=_result("vxvue-job"))
    storage.create_analysis("other-job", request={"product": "Bellalun Viewer"}, module="qa_agent")
    storage.update_analysis("other-job", "DONE", result=_result("other-job"))

    rows, total = storage.list_analyses(product="VXvue")

    assert total == 1
    assert rows[0]["id"] == "vxvue-job"


def test_list_analyses_search_matches_id_or_change_file(tmp_path):
    storage = Storage(tmp_path / "app.db")
    matching = _result("job-a")
    matching["change_file"] = "release-note.pdf"
    storage.create_analysis("job-a", module="qa_agent")
    storage.update_analysis("job-a", "DONE", result=matching)
    other = _result("job-b")
    other["change_file"] = "unrelated.pdf"
    storage.create_analysis("job-b", module="qa_agent")
    storage.update_analysis("job-b", "DONE", result=other)

    by_id, _ = storage.list_analyses(search="job-a")
    by_file, _ = storage.list_analyses(search="release-note")

    assert [row["id"] for row in by_id] == ["job-a"]
    assert [row["id"] for row in by_file] == ["job-a"]


def test_list_analyses_paginates_with_limit_and_offset(tmp_path):
    storage = Storage(tmp_path / "app.db")
    for index in range(5):
        job_id = f"job-{index}"
        storage.create_analysis(job_id, module="qa_agent")
        storage.update_analysis(job_id, "DONE", result=_result(job_id))

    first_page, total = storage.list_analyses(limit=2, offset=0)
    second_page, _ = storage.list_analyses(limit=2, offset=2)

    assert total == 5
    assert len(first_page) == 2
    assert len(second_page) == 2
    assert {row["id"] for row in first_page}.isdisjoint({row["id"] for row in second_page})


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
