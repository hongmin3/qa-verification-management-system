"""Validates: REQ-QAINTEL-021, REQ-DAILY-005, REQ-DAILY-008."""
from datetime import datetime, timezone

from app.modules.daily_qa.pipeline import _is_weekly
from app.modules.qa_agent import dashboard as dash
from tests.test_qa_intel_pipeline import DAY1, DAY2, env  # noqa: F401
from app.modules.daily_qa.agent_runner import FakeRunner
from tests.test_qa_intel_pipeline import analysis_producer
from tests.daily_qa_fixtures import issue_item


def test_weekly_day_uses_korea_date_even_on_american_server():
    assert not _is_weekly(datetime(2026, 10, 12, 22, 18, tzinfo=timezone.utc), "mon")
    assert _is_weekly(datetime(2026, 10, 11, 22, 18, tzinfo=timezone.utc), "mon")


def test_running_ai_progress_is_persisted_and_visible_to_another_reader(env):
    env["run"](DAY1)
    env["state"]["issues"].append(issue_item("VP-200", "2026-09-29T09:00:00Z", ["VP-10"]))
    seen = []
    producer = analysis_producer(env["calls"])

    def produce(task, workspace):
        row = env["store"].list_runs(limit=1)[0]
        seen.append(row["progress"])
        assert row["status"] == "RUNNING"
        assert row["progress"]["phase"] == "ai"
        assert row["progress"]["total"] == 1
        status = dash.status(dash.ProductChoice(env["cfg"], env["store"], [env["cfg"].product]))
        assert status["progress"]["run_id"] == row["id"]
        assert status["progress"]["phase"] == "ai"
        assert status["progress"]["elapsed_seconds"] >= 0
        producer(task, workspace)

    result = env["run"](DAY2, runner=FakeRunner(produce))
    row = env["store"].get_run(result["run_id"])
    assert seen and row["progress"]["done"] == row["progress"]["total"] == 1
    assert row["progress"]["phase"] == "finished"


def test_mail_is_running_until_smtp_result_and_error_is_stored(env, monkeypatch):
    from app.modules.daily_qa import pipeline
    seen = []

    def send(subject, *_):
        if "변경 리포트" in subject:      # 수집 직후 변경 리포트 메일은 AI 요약 메일 단계와 따로다
            return {"status": "sent"}
        row = env["store"].list_runs(limit=1)[0]
        seen.append(row["status"])
        assert row["progress"]["phase"] == "email"
        return {"status": "failed", "error": "SMTPSenderRefused: 530 Authentication Required"}

    # env supplies a sender; replace run_daily's call site to use the real mail boundary.
    original = pipeline.run_daily
    def run_with_sender(*args, **kwargs):
        kwargs["send_email"] = send
        return original(*args, **kwargs)
    monkeypatch.setattr("tests.test_qa_intel_pipeline.run_daily", run_with_sender)
    result = env["run"](DAY1)
    row = env["store"].get_run(result["run_id"])
    assert seen == ["RUNNING"]
    assert row["stages"]["email"]["status"] == "failed"
    assert "530 Authentication Required" in row["stages"]["email"]["note"]
    assert row["status"] == "PARTIAL"


def test_old_run_without_progress_is_readable(env):
    env["store"].create_run("old-run", False, "vxvue")
    env["store"].finish_run("old-run", "SUCCESS", {}, {})
    assert env["store"].get_run("old-run")["progress"] == {}


def test_auth_block_is_not_reported_as_usage_limit(env):
    env["store"].create_run("auth-run", False, "vxvue")
    env["store"].finish_run("auth-run", "PARTIAL", {}, {"claude_limit": {"kind": "AUTH", "label": "Claude 인증 실패"}})
    status = dash.status(dash.ProductChoice(env["cfg"], env["store"], [env["cfg"].product]))
    assert "사용량 한도" not in status["last_run"]["result"]
    assert "인증" in status["last_run"]["result"]


def test_abandoned_running_record_stops_without_losing_saved_results(env):
    store = env["store"]
    store.create_run("abandoned", False, env["cfg"].slug)
    store.update_progress("abandoned", {"phase": "ai", "done": 1, "total": 3},
                          {"srs": {"status": "ok", "counts": {"total": 4}}})
    with store.storage.connect() as db:
        db.execute("UPDATE daily_qa_runs SET started_at='2026-10-01T00:00:00+00:00' WHERE id='abandoned'")
    status = dash.status(dash.ProductChoice(env["cfg"], store, [env["cfg"].product]))
    row = store.get_run("abandoned")
    assert not status["running"]
    assert row["status"] == "FAILED"
    assert row["stages"]["srs"]["counts"]["total"] == 4
    assert row["stages"]["interrupted"]["status"] == "failed"
    assert row["progress"]["done"] == 1
    assert row["progress"]["phase"] == "finished"


def test_new_running_record_without_lock_has_startup_grace(env):
    env["store"].create_run("starting", False, env["cfg"].slug)
    dash.status(dash.ProductChoice(env["cfg"], env["store"], [env["cfg"].product]))
    assert env["store"].get_run("starting")["status"] == "RUNNING"


def test_email_render_failure_finishes_run(env, monkeypatch):
    def broken(*_, **__):
        raise ValueError("render failed")
    monkeypatch.setattr("app.modules.daily_qa.report.build_email", broken)
    result = env["run"](DAY1)
    row = env["store"].get_run(result["run_id"])
    assert row["status"] == "PARTIAL"
    assert row["progress"]["phase"] == "finished"
    assert row["stages"]["email"]["note"] == "ValueError"
