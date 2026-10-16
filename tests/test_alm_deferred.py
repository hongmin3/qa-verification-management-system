"""자동 분석이 꺼진 동안의 변경을 밀린 변경으로 저장하고 [밀린 변경 분석]으로 분석. Validates: REQ-ALMCHG-005."""

from __future__ import annotations

import json
from datetime import date, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.storage import Storage
from app.modules.daily_qa import claude_limits, pipeline, scheduled_jobs
from app.modules.daily_qa.agent_runner import FakeRunner, RunnerOutcome
from app.modules.daily_qa.pipeline import STATE_CATCHUP_AFTER
from app.modules.qa_agent import dashboard as dash
from app.modules.qa_agent import router as router_module
from tests.daily_qa_fixtures import REPO_ROOT, issue_item, make_settings, make_tc_workbook, srs_item
from tests.qa_intel_harness import DAY1, DAY2, DAY3, Harness
from tests.test_qa_intel_pipeline import analysis_producer


def set_flags(store, cfg, enabled=True, validation_period=True):
    store.set_state(cfg.state_key("automation_policy"), json.dumps({"enabled": enabled, "validation_period": validation_period}))


@pytest.fixture
def env(tmp_path):
    workbook = make_tc_workbook(tmp_path / "(TC) VXvue_TestCase.xlsx", [("TC_1", "VP-10", "목록 표시")])
    h = Harness(tmp_path, tc_paths=[workbook])
    h.srs = [srs_item("VP-10", "01-01", "목록")]
    h.issues = [issue_item("VP-100", "2026-09-28T00:00:00Z", ["VP-10"], review="", status="open")]
    return h


def status_of(h, run_id, entity_id):
    return {e["entity_id"]: e["analysis_status"] for e in h.events(run_id)}.get(entity_id)


def new_issue_after_baseline(h, mode, trigger, extra=None, **run_kwargs):
    h.run(DAY1)                                         # 기준 기록
    set_flags(h.store, h.cfg, enabled=mode != "off", validation_period=mode != "weekly")
    h.issues.append(issue_item("VP-200", "2026-09-29T09:00:00Z", ["VP-10"], review="", status="open"))
    if extra:
        extra(h)
    return h.run(DAY2, trigger=trigger, **run_kwargs)


# Validates: REQ-ALMCHG-005
def test_collect_while_off_stores_deferred(env):
    outcome = new_issue_after_baseline(env, "off", "collect")
    assert status_of(env, outcome["run_id"], "VP-200") == "deferred"
    assert outcome["summary"]["deferred_events"] >= 1
    assert env.calls == []


# Validates: REQ-ALMCHG-005
def test_scheduled_run_while_off_stores_deferred(env):
    outcome = new_issue_after_baseline(env, "off", "scheduled")
    assert status_of(env, outcome["run_id"], "VP-200") == "deferred"
    assert outcome["summary"]["deferred_events"] >= 1
    assert env.calls == []


# Validates: REQ-ALMCHG-005
def test_not_required_events_stay_not_required(env):
    def change_status(h):
        h.issues[0] = issue_item("VP-100", "2026-09-28T00:00:00Z", ["VP-10"], review="", status="closed")
    outcome = new_issue_after_baseline(env, "off", "collect", change_status)
    statuses = {e["entity_id"]: e["analysis_status"] for e in env.events(outcome["run_id"])}
    not_required = [e for e in env.events(outcome["run_id"]) if not e["analysis_required"]]
    assert all(e["analysis_status"] == "not_required" for e in not_required)
    assert statuses["VP-200"] == "deferred"


# Validates: REQ-ALMCHG-005
def test_weekly_collect_keeps_pending(env):
    outcome = new_issue_after_baseline(env, "weekly", "collect")
    assert status_of(env, outcome["run_id"], "VP-200") == "pending"
    assert outcome["summary"]["deferred_events"] == 0


# Validates: REQ-ALMCHG-005
def test_manual_run_while_off_keeps_pending(env):
    outcome = new_issue_after_baseline(env, "off", "manual", runner=FakeRunner(analysis_producer(env.calls)))
    assert status_of(env, outcome["run_id"], "VP-200") == "done"     # 사람이 누른 실행은 바로 분석한다
    assert env.calls and any("VP-200" in targets for _, targets in env.calls)
    assert outcome["summary"]["deferred_events"] == 0


# Validates: REQ-ALMCHG-005
def test_auto_analysis_skips_deferred(env):
    env.run(DAY1)                                       # 기준 기록
    env.issues.append(issue_item("VP-150", "2026-09-29T09:00:00Z", ["VP-10"], review="", status="open"))
    waiting = env.run(DAY2, trigger="collect")   # 켜 있을 때 저장하고 아직 분석하지 않은 대기 이벤트
    assert status_of(env, waiting["run_id"], "VP-150") == "pending"
    set_flags(env.store, env.cfg, enabled=False)
    env.issues.append(issue_item("VP-200", "2026-09-29T09:00:00Z", ["VP-10"], review="", status="open"))
    deferred_run = env.run(DAY3, trigger="collect")
    assert status_of(env, deferred_run["run_id"], "VP-200") == "deferred"
    assert env.calls == []
    set_flags(env.store, env.cfg, enabled=True)
    env.run(DAY3 + timedelta(days=1), trigger="scheduled", runner=FakeRunner(analysis_producer(env.calls)))     # 새 변경 없음
    assert status_of(env, waiting["run_id"], "VP-150") == "done"       # 꺼지기 전 대기 이벤트는 켜면 분석한다
    assert env.calls and all("VP-200" not in targets for _, targets in env.calls)
    assert [e["entity_id"] for e in env.store.list_events(product=env.cfg.slug, statuses=("deferred",))] == ["VP-200"]
    # 켠 뒤에 저장한 새 변경은 분석 대상이다
    env.calls.clear()
    env.issues.append(issue_item("VP-300", "2026-10-01T09:00:00Z", ["VP-10"], review="", status="open"))
    later = env.run(DAY3 + timedelta(days=2), trigger="scheduled", runner=FakeRunner(analysis_producer(env.calls)))
    assert status_of(env, later["run_id"], "VP-300") != "deferred"
    assert env.calls and all("VP-200" not in targets for _, targets in env.calls)
    assert status_of(env, deferred_run["run_id"], "VP-200") == "deferred"


# ---- [밀린 변경 분석] (REQ-ALMCHG-005 3~7번) ---------------------------------------------------------

DAY_AFTER = DAY3 + timedelta(days=2)


def kst_day(moment) -> date:
    return moment.astimezone(claude_limits.KST).date()


@pytest.fixture
def two_days(env):
    """자동 분석이 꺼진 채 이틀 동안 이슈가 하나씩 밀린 변경이 된다. DAY2 = VP-200, DAY3 = VP-300."""
    new_issue_after_baseline(env, "off", "collect")
    env.issues.append(issue_item("VP-300", "2026-09-30T09:00:00Z", ["VP-10"], review="", status="open"))
    env.run(DAY3, trigger="collect")
    assert env.calls == []
    # 이벤트의 저장 시각은 실제 시계라 같은 날로 찍힌다. 이틀치로 보이도록 날짜를 맞춘다.
    with env.store.storage.connect() as db:
        for entity, moment in (("VP-200", DAY2), ("VP-300", DAY3)):
            db.execute("UPDATE qa_change_events SET created_at=? WHERE entity_id=?",
                       (moment.astimezone(timezone.utc).isoformat(timespec="seconds"), entity))
    return env


def deferred_ids(h):
    return {e["entity_id"] for e in h.store.list_events(product=h.cfg.slug, statuses=("deferred",))}


def run_deferred(h, since=None, until=None, day=DAY_AFTER, **kwargs):
    kwargs.setdefault("runner", FakeRunner(analysis_producer(h.calls)))
    return h.run(day, trigger="deferred", deferred_since=since, deferred_until=until, **kwargs)


# Validates: REQ-ALMCHG-005
def test_deferred_run_analyzes_only_chosen_range(two_days):
    h = two_days
    kept = {e["entity_id"]: e["remaining_analyses"] for e in h.store.list_events(product=h.cfg.slug, statuses=("deferred",))}
    assert kept["VP-200"] and kept["VP-300"]          # 저장할 때부터 남은 분석이 있어 deferred -> pending 으로 바꿔도 잃지 않는다
    outcome = run_deferred(h, since=kst_day(DAY2), until=kst_day(DAY2))
    events = {e["entity_id"]: e["analysis_status"] for e in h.store.list_events(product=h.cfg.slug)}
    assert events["VP-200"] == "done"
    assert events["VP-300"] == "deferred"
    assert h.calls and all("VP-300" not in targets for _, targets in h.calls)
    assert outcome["summary"]["trigger"] == "deferred" and outcome["summary"]["run_kind"] == "deferred"
    assert deferred_ids(h) == {"VP-300"}


# Validates: REQ-ALMCHG-005
def test_deferred_run_does_not_collect_again(two_days):
    h = two_days
    h.issues.append(issue_item("VP-400", "2026-10-01T09:00:00Z", ["VP-10"], review="", status="open"))
    run_deferred(h)
    assert all(e["entity_id"] != "VP-400" for e in h.store.list_events(product=h.cfg.slug))


# Validates: REQ-ALMCHG-005
def test_limit_returns_rest_to_deferred(two_days):
    h = two_days
    reset = DAY_AFTER + timedelta(hours=2)
    limit = claude_limits.LimitInfo(kind=claude_limits.KIND_SESSION, reset_at=reset.astimezone(timezone.utc).isoformat(timespec="seconds"),
                                    message="limit reached", detected_at=reset.isoformat())
    runner = FakeRunner(lambda task, run: RunnerOutcome(False, 1, 0.0, "limit reached", limit=limit))
    run_deferred(h, runner=runner)
    statuses = {e["entity_id"]: e["analysis_status"] for e in h.store.list_events(product=h.cfg.slug)}
    assert statuses["VP-200"] == statuses["VP-300"] == "deferred"       # pending 이 아니라 밀린 변경이다
    assert not h.store.get_state(h.cfg.state_key(STATE_CATCHUP_AFTER), "")   # 자동 재개를 걸지 않는다
    assert not [e for e in h.store.list_events(product=h.cfg.slug, statuses=("pending", "failed")) if e["analysis_required"]]


# Validates: REQ-ALMCHG-005
def test_cancel_returns_rest_to_deferred(two_days):
    h = two_days

    def produce(task, run):
        assert h.store.request_cancel(run.run_id)
        return RunnerOutcome(False, -1, .1, "사용자 중단", cancelled=True)
    outcome = run_deferred(h, runner=FakeRunner(produce))
    assert outcome["status"] == "CANCELLED"
    assert deferred_ids(h) == {"VP-200", "VP-300"}


# Validates: REQ-ALMCHG-005
def test_deferred_run_mail_follows_policy(two_days):
    h = two_days
    before = len(h.summary_sent)
    off = run_deferred(h, since=kst_day(DAY2), until=kst_day(DAY2))
    assert off["email"]["status"] == "skipped" and off["email"]["reason"]
    assert len(h.summary_sent) == before
    set_flags(h.store, h.cfg, enabled=True)
    on = run_deferred(h, since=kst_day(DAY3), until=kst_day(DAY3), day=DAY_AFTER + timedelta(days=1))
    assert on["email"]["status"] == "sent"
    assert len(h.summary_sent) == before + 1


# Validates: REQ-ALMCHG-005
def test_launch_detached_passes_deferred_range_and_keeps_mail(tmp_path, monkeypatch):
    cfg = make_settings(tmp_path / "root", tmp_path / "ws")
    launched = []

    class Popen:
        pid = 7

        def __init__(self, args, **kwargs):
            launched.append(args)
    monkeypatch.setattr(scheduled_jobs, "load", lambda product=None: cfg)
    result = scheduled_jobs.launch_detached(trigger="deferred", deferred_since="2026-09-30", deferred_until="2026-10-01", popen=Popen)
    assert result["status"] == "launched"
    args = launched[0]
    assert args[args.index("--trigger") + 1] == "deferred"
    assert args[args.index("--deferred-since") + 1] == "2026-09-30" and args[args.index("--deferred-until") + 1] == "2026-10-01"
    assert "--no-email" not in args


@pytest.fixture
def world(two_days, monkeypatch):
    h = two_days
    target = h.cfg.root / "config" / "products"
    target.mkdir(parents=True, exist_ok=True)
    (target / "vxvue.yaml").write_text((REPO_ROOT / "config" / "products" / "vxvue.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    choice = dash.ProductChoice(h.cfg, h.store, [h.cfg.product])
    monkeypatch.setattr(dash, "choose", lambda product="": choice if product in ("", h.cfg.product, h.cfg.slug) else None)
    documents = Storage(db_path=h.cfg.db_path)
    monkeypatch.setattr(dash, "_documents_storage", lambda _cfg: documents)
    app = FastAPI()
    app.include_router(router_module.router, prefix="/qa-agent")
    return {"h": h, "client": TestClient(app)}


# Validates: REQ-ALMCHG-005
def test_deferred_summary_counts_by_analysis(world):
    data = world["client"].get("/qa-agent/deferred").json()
    assert data["count"] == 2
    assert data["first"] == kst_day(DAY2).isoformat() and data["last"] == kst_day(DAY3).isoformat()
    assert data["by_analysis"] and sum(data["by_analysis"].values()) >= 2
    day = kst_day(DAY3).isoformat()
    one = world["client"].get("/qa-agent/deferred", params={"since": day, "until": day}).json()
    assert one["count"] == 1 and one["first"] == one["last"] == day


# Validates: REQ-ALMCHG-005
def test_dashboard_shows_deferred_line_and_hides_button_when_none(world):
    client, h = world["client"], world["h"]
    page = client.get("/qa-agent").text
    assert "밀린 변경 분석" in page and "밀린 변경 <b>2</b>건" in page
    h.store.update_events([e["id"] for e in h.store.list_events(product=h.cfg.slug, statuses=("deferred",))], "abandoned")
    assert "deferred-open" not in client.get("/qa-agent").text and "밀린 변경 분석" not in client.get("/qa-agent").text


# Validates: REQ-ALMCHG-005
def test_empty_range_returns_400(world):
    client = world["client"]
    response = client.post("/qa-agent/deferred", data={"product": "", "since": "2020-01-01", "until": "2020-01-02"})
    assert response.status_code == 400 and response.json()["detail"] == "고른 기간에 밀린 변경이 없습니다."
    bad = client.post("/qa-agent/deferred", data={"since": "내일"})
    assert bad.status_code == 400 and bad.json()["detail"] == "날짜는 YYYY-MM-DD 로 입력하세요."
    backwards = client.post("/qa-agent/deferred", data={"since": "2026-10-02", "until": "2026-10-01"})
    assert backwards.json()["detail"] == "시작일이 종료일보다 늦습니다."


# Validates: REQ-ALMCHG-005
def test_running_returns_409(world, monkeypatch):
    monkeypatch.setattr(pipeline, "is_running", lambda cfg: True)
    response = world["client"].post("/qa-agent/deferred", data={"product": ""})
    assert response.status_code == 409
    assert response.json()["detail"] == "점검이 이미 실행 중입니다." and response.json()["status"] == "running"


# Validates: REQ-ALMCHG-005
def test_post_launches_deferred_run(world, monkeypatch):
    seen = {}

    def fake_launch(product=None, **kwargs):
        seen.update(kwargs)
        return {"status": "launched", "product": product}
    monkeypatch.setattr(scheduled_jobs, "launch_detached", fake_launch)
    day = kst_day(DAY2).isoformat()
    response = world["client"].post("/qa-agent/deferred", data={"product": "", "since": day, "until": day})
    assert response.status_code == 202 and response.json()["status"] == "launched"
    assert seen["trigger"] == "deferred" and seen["deferred_since"] == day
