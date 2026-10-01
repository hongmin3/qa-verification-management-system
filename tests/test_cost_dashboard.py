from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.core.storage import Storage
from app.main import app
from app.modules.cost_dashboard import router as cost_dashboard_router


def _impact_result(total_tokens: int, cache_hit: bool | None) -> dict:
    result = {
        "analysis_id": "x",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "change_file": "change.docx",
        "specification_file": "spec.pdf",
        "testcase_file": "tc.xlsx",
        "change": {"changed_features": []},
        "total_tc": 1,
        "candidate_tc": 1,
        "decisions": [],
        "token_usage": {"total_tokens": total_tokens},
    }
    if cache_hit is not None:
        result["ai_audit"] = {"cache_hit": cache_hit}
    return result


def _manual_review_result(total_tokens: int, revision_id: int = 1, ai_audit: dict | None = None) -> dict:
    result = {
        "revision_id": revision_id,
        "round_number": 1,
        "total_changes": 3,
        "functional_changes": 2,
        "decision_counts": {},
        "prior_open_comments": [],
        "release_scope_total": 0,
        "release_scope_missing_suspected": 0,
        "cross_manual_review_required": 0,
        "token_usage": {"total_tokens": total_tokens},
    }
    if ai_audit is not None:
        result["ai_audit"] = ai_audit
    return result


def test_cost_dashboard_stats_aggregates_by_module_and_day(tmp_path):
    storage = Storage(tmp_path / "app.db")
    storage.create_analysis("impact-1", module="impact_analyzer", request={"product": "VXvue"})
    storage.update_analysis("impact-1", "DONE", result=_impact_result(100, cache_hit=True))
    storage.create_analysis("manual-1", module="manual_review")
    storage.update_analysis("manual-1", "DONE", result=_manual_review_result(50))

    stats = storage.cost_dashboard_stats(days=30)

    assert stats["modules"]["impact_analyzer"] == {"tokens": 100, "count": 1}
    assert stats["modules"]["manual_review"] == {"tokens": 50, "count": 1}
    assert len(stats["daily"]) == 1
    assert stats["daily"][0]["tokens"] == 150
    recent_products = {item["id"]: item["product"] for item in stats["recent"]}
    assert recent_products["impact-1"] == "VXvue"


def test_cost_dashboard_infers_module_for_legacy_rows_without_module_column(tmp_path):
    storage = Storage(tmp_path / "app.db")
    storage.create_analysis("legacy", module=None)
    storage.update_analysis("legacy", "DONE", result=_manual_review_result(20))

    stats = storage.cost_dashboard_stats(days=30)

    assert stats["modules"]["manual_review"]["tokens"] == 20


def test_cost_dashboard_cache_hit_rate_ignores_analyses_without_cache_data(tmp_path):
    storage = Storage(tmp_path / "app.db")
    storage.create_analysis("hit", module="impact_analyzer")
    storage.update_analysis("hit", "DONE", result=_impact_result(10, cache_hit=True))
    storage.create_analysis("miss", module="impact_analyzer")
    storage.update_analysis("miss", "DONE", result=_impact_result(10, cache_hit=False))
    storage.create_analysis("manual", module="manual_review")
    storage.update_analysis("manual", "DONE", result=_manual_review_result(10))

    stats = storage.cost_dashboard_stats(days=30)

    assert stats["cache_sample_size"] == 2
    assert stats["cache_hit_rate"] == 0.5


def test_cost_dashboard_cache_hit_rate_includes_manual_review_call_counts(tmp_path):
    storage = Storage(tmp_path / "app.db")
    storage.create_analysis("impact-hit", module="impact_analyzer")
    storage.update_analysis("impact-hit", "DONE", result=_impact_result(10, cache_hit=True))
    storage.create_analysis("manual-1", module="manual_review")
    storage.update_analysis(
        "manual-1", "DONE",
        result=_manual_review_result(10, ai_audit={"request_count": 1, "cache_hit_count": 1}),
    )

    stats = storage.cost_dashboard_stats(days=30)

    # impact: 1 hit / 1 call. manual: 1 hit / (1 hit + 1 실제 요청) = 1/2 calls. 합계 2 hits / 3 calls.
    assert stats["cache_sample_size"] == 3
    assert stats["cache_hit_rate"] == 2 / 3
    recent_by_id = {item["id"]: item for item in stats["recent"]}
    assert recent_by_id["manual-1"]["cache_hits"] == 1
    assert recent_by_id["manual-1"]["cache_calls"] == 2


def test_cost_dashboard_stats_excludes_analyses_older_than_window(tmp_path):
    storage = Storage(tmp_path / "app.db")
    storage.create_analysis("old", module="impact_analyzer")
    storage.update_analysis("old", "DONE", result=_impact_result(999, cache_hit=None))
    with storage.connect() as db:
        db.execute("UPDATE analyses SET created_at=? WHERE id=?", ("2000-01-01T00:00:00+00:00", "old"))

    stats = storage.cost_dashboard_stats(days=30)

    assert stats["modules"] == {}
    assert stats["daily"] == []


def test_cost_dashboard_route_renders_module_and_cache_summary(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "app.db")
    storage.create_analysis("impact-1", module="impact_analyzer", request={"product": "VXvue"})
    storage.update_analysis("impact-1", "DONE", result=_impact_result(100, cache_hit=True))
    monkeypatch.setattr(cost_dashboard_router, "storage", storage)

    response = TestClient(app).get("/cost-dashboard")

    assert response.status_code == 200
    assert "Regression 영향 분석" in response.text
    assert "VXvue" in response.text
    assert "100" in response.text


def test_cost_dashboard_route_respects_days_query_param(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "app.db")
    monkeypatch.setattr(cost_dashboard_router, "storage", storage)

    response = TestClient(app).get("/cost-dashboard?days=7")

    assert response.status_code == 200
    assert "최근 7일" in response.text


def test_cost_dashboard_linked_from_other_modules():
    client = TestClient(app)
    for path in ("/qa-agent", "/manual-review", "/knowledge"):
        assert 'href="/cost-dashboard"' in client.get(path).text


# --- 하루 기준(한국 시간 0시)과 실패한 분석의 토큰 (OPEN_QUESTIONS 8-8, 8-10) ---------------


def _set_created_at(storage: Storage, analysis_id: str, iso: str) -> None:
    with storage.connect() as db:
        db.execute("UPDATE analyses SET created_at=? WHERE id=?", (iso, analysis_id))


def test_today_starts_at_midnight_korea_time():
    # Validates: REQ-USAGE-002
    from app.core.usage import today_start_iso

    # 한국 시간 2026-09-29 08:00 = UTC 2026-09-28 23:00. 오늘은 한국 시간 9월 29일 0시(UTC 9월 28일 15:00)부터다.
    now = datetime(2026, 9, 28, 23, 0, tzinfo=timezone.utc)
    assert today_start_iso(now) == "2026-09-28T15:00:00+00:00"
    # 한국 시간 2026-09-29 10:00 = UTC 01:00. 같은 날이다.
    assert today_start_iso(datetime(2026, 9, 29, 1, 0, tzinfo=timezone.utc)) == "2026-09-28T15:00:00+00:00"


def test_daily_token_status_counts_early_morning_korea_usage_as_today(tmp_path, monkeypatch):
    # Validates: REQ-USAGE-002
    from app.core import usage

    storage = Storage(tmp_path / "app.db")
    storage.create_analysis("early", module="impact_analyzer")
    storage.update_analysis("early", "DONE", result=_impact_result(300, cache_hit=None))
    _set_created_at(storage, "early", "2026-09-28T16:00:00+00:00")  # 한국 시간 9월 29일 01:00
    storage.create_analysis("yesterday", module="impact_analyzer")
    storage.update_analysis("yesterday", "DONE", result=_impact_result(900, cache_hit=None))
    _set_created_at(storage, "yesterday", "2026-09-28T14:00:00+00:00")  # 한국 시간 9월 28일 23:00

    now = datetime(2026, 9, 29, 1, 0, tzinfo=timezone.utc)
    status = usage.daily_token_status(storage, now=now)
    assert status["used"] == 300


def test_failed_analysis_tokens_count_toward_the_daily_total(tmp_path):
    # Validates: REQ-USAGE-002, REQ-COST-002
    storage = Storage(tmp_path / "app.db")
    storage.create_analysis("done", module="impact_analyzer")
    storage.update_analysis("done", "DONE", result=_impact_result(100, cache_hit=None))
    storage.create_analysis("failed", module="manual_review")
    storage.update_analysis("failed", "FAILED", result={"token_usage": {"total_tokens": 40}}, error="AI 판정 도중 실패")
    storage.create_analysis("failed-empty", module="impact_analyzer")
    storage.update_analysis("failed-empty", "FAILED", error="시작 전 실패")

    assert storage.tokens_used_since("1970-01-01T00:00:00+00:00") == 140
    stats = storage.cost_dashboard_stats(days=30)
    assert stats["modules"]["manual_review"]["tokens"] == 40
    assert stats["modules"]["manual_review"]["failed"] == 1
    assert stats["daily"][0]["tokens"] == 140
    statuses = {item["id"]: item["status"] for item in stats["recent"]}
    assert statuses == {"done": "DONE", "failed": "FAILED"}


def test_dashboard_groups_days_by_korea_time(tmp_path):
    # Validates: REQ-COST-001
    storage = Storage(tmp_path / "app.db")
    storage.create_analysis("early", module="impact_analyzer")
    storage.update_analysis("early", "DONE", result=_impact_result(10, cache_hit=None))
    recent_iso = datetime.now(timezone.utc).replace(hour=16, minute=30, second=0, microsecond=0).isoformat()
    _set_created_at(storage, "early", recent_iso)

    stats = storage.cost_dashboard_stats(days=30)

    expected_day = (datetime.fromisoformat(recent_iso) + __import__("datetime").timedelta(hours=9)).strftime("%Y-%m-%d")
    assert stats["daily"][0]["date"] == expected_day
    assert stats["recent"][0]["created_at_kst"].startswith(expected_day)


def test_dashboard_labels_qa_agent_and_explains_the_features_in_use(monkeypatch, tmp_path):
    # Validates: REQ-COST-001
    storage = Storage(tmp_path / "app.db")
    storage.create_analysis("agent-1", module="qa_agent", request={"product": "VXvue"})
    storage.update_analysis("agent-1", "DONE", result={"token_usage": {"total_tokens": 70}})
    monkeypatch.setattr(cost_dashboard_router, "storage", storage)

    text = TestClient(app).get("/cost-dashboard").text

    assert "QA Agent" in text
    assert "<b>qa_agent</b>" not in text
    # Regression 영향 분석을 없앤 뒤(2026-10-01) 지금 쓰는 기능은 QA Agent 와 매뉴얼 개정 검증 둘이다.
    assert "세 기능의 사용량을 합산" not in text
    assert "두 기능의 사용량을 합산" in text
    assert "한국 시간" in text


def test_recent_table_says_start_time_because_it_shows_created_at(monkeypatch, tmp_path):
    # Validates: REQ-COST-001, REQ-COST-002
    # 최근 분석 표의 시각은 작업을 만든 시각(created_at)이다. "완료 시각"이라고 쓰면 오래 걸린 분석에서 틀린다.
    storage = Storage(tmp_path / "app.db")
    storage.create_analysis("a-1", module="impact_analyzer", request={"product": "VXvue"})
    storage.update_analysis("a-1", "DONE", result={"token_usage": {"total_tokens": 10}})
    monkeypatch.setattr(cost_dashboard_router, "storage", storage)

    text = TestClient(app).get("/cost-dashboard").text

    assert "완료 시각" not in text
    assert "시작 시각(한국 시간)" in text


def test_guide_does_not_claim_one_call_per_analysis_for_manual_review():
    # Validates: REQ-COST-004
    # 매뉴얼 개정 검증은 변경마다 1~2회 부른다. "분석 1건당 1회가 원칙"은 그 기능에 맞지 않는다.
    text = TestClient(app).get("/cost-dashboard/guide").text
    assert "이 시스템은 분석 1건당 1회가 원칙입니다" not in text
    assert "변경마다" in text
