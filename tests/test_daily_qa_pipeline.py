"""Validates: REQ-DAILY-001, REQ-DAILY-006 (TEST-DAILY-001)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.modules.daily_qa.agent_runner import FakeRunner
from app.modules.daily_qa.pipeline import Inputs, RunLocked, run_daily
from app.modules.daily_qa.rules import RulesState
from app.modules.daily_qa.schema import SKILL_B, SKILL_C, SKILL_E, SKILL_F
from app.modules.daily_qa.settings import PolarionSettings
from app.modules.daily_qa.srs_snapshot import save_snapshot
from app.modules.daily_qa.store import DailyQaStore
from tests.daily_qa_fixtures import FakePolarion, issue_item, make_settings, make_tc_workbook, rules_ok, srs_item, write_result

MONDAY = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)
TUESDAY = datetime(2026, 9, 29, 9, 0, tzinfo=timezone.utc)
EVIDENCE = [{"source_type": "srs", "location": "VP-10 / 본문", "validity": "Current"}]


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    cfg = make_settings(root, tmp_path / "ws")
    tc = make_tc_workbook(tmp_path / "tc.xlsx", [("TC_1", "VP-10", "A"), ("TC_2", "VP-11", "B")])
    inputs = Inputs(tc_paths=[tc], manuals={"Operation Manual.txt": "1. 설치\n2. Viewer"})
    store = DailyQaStore(tmp_path / "app.db")
    # 어제 스냅샷: VP-10 본문이 오늘과 다르다 → B 작업이 생긴다
    save_snapshot(cfg.snapshot_dir, "2026-09-27", [
        {"id": "VP-10", "old_id": "01-10-10", "title": "Viewer", "status": "draft", "text": "옛 본문", "is_category": False},
        {"id": "VP-11", "old_id": "01-10-11", "title": "Setting", "status": "draft", "text": "본문", "is_category": False},
    ])
    polarion = FakePolarion(
        srs=[srs_item("VP-10", "01-10-10", "Viewer", text="새 본문"), srs_item("VP-11", "01-10-11", "Setting"), srs_item("VP-12", "01-10-12", "Report")],
        issues=[issue_item("VP-6669", "2026-09-28T01:00:00Z", ["VP-10"]), issue_item("VP-1", "2020-01-01T00:00:00Z", [])],
    )
    sent = []
    return {
        "cfg": cfg, "inputs": inputs, "store": store, "polarion": polarion, "sent": sent, "tmp": tmp_path,
        "sender": lambda subject, text, body, to: sent.append((subject, text, to)) or {"status": "sent"},
    }


def _producer(task, run):
    if task.skill == SKILL_B:
        write_result(run, task, [{"subject": "VP-10", "verdict": "수정 필수", "summary": "Expected 가 옛 본문 기준", "evidence": EVIDENCE}],
                     questions=[{"subject": "VP-10", "question": "Admin 만 보이나?", "why": "권한"}])
    elif task.skill == SKILL_C:
        write_result(run, task, [{
            "subject": "VP-6669", "subject_title": "제목", "verdict": "신규 TC 필요", "summary": "수정확인 초안", "issue_type": "Program Fixed",
            "evidence": EVIDENCE,
            "draft_tcs": [{"kind": "수정확인", "srs_no": "VP-10", "title": "수정확인", "test_step": "1. 실행한다.", "expected_result": "1. 표시된다."}],
        }])
    elif task.skill == SKILL_F:
        write_result(run, task, [])


def _run(env, **kwargs):
    kwargs.setdefault("today", MONDAY)
    kwargs.setdefault("runner", FakeRunner(_producer))
    kwargs.setdefault("rules_state", rules_ok(env["tmp"]))
    return run_daily(env["cfg"], polarion_factory=lambda: env["polarion"], inputs=env["inputs"], send_email=env["sender"],
                     store=env["store"], **kwargs)


def test_full_run_creates_findings_questions_draft_and_email(env):
    outcome = _run(env)
    stages = outcome["stages"]
    assert outcome["status"] == "SUCCESS", stages
    assert stages["collect_srs"]["counts"] == {"total": 3, "added": 1, "removed": 0, "modified": 1}
    assert stages["collect_issues"]["counts"] == {"new": 1}           # 2020년 이슈는 첫 실행 기준 시각 이전
    assert stages["E"]["status"] == "ok" and stages["F"]["status"] == "ok"
    store = env["store"]
    skills = {item["skill"] for item in store.list_findings(run_id=outcome["run_id"])}
    assert {SKILL_B, SKILL_C, SKILL_E} <= skills
    assert len(store.list_questions(unanswered_only=True)) == 1
    out_dir = env["cfg"].output_dir / outcome["run_id"]
    assert (out_dir / "impact_checklist_draft.xlsx").is_file()
    audit = json.loads((out_dir / "audit.json").read_text(encoding="utf-8"))
    assert {task["task_id"] for task in audit["tasks"]} >= {"B-001", "C-001", "F-001"}
    assert (out_dir / "sent" / "B-001.json").is_file()               # 보낸 입력이 남는다
    assert env["sent"] and "Finding" in env["sent"][0][0]
    assert store.get_run(outcome["run_id"])["email_status"] == "sent"
    assert store.get_state("issues_last_success_at") == "2026-09-28T01:00:00Z"


def test_rules_mismatch_skips_ai_but_keeps_deterministic_stages(env):
    runner = FakeRunner(_producer)
    outcome = _run(env, runner=runner, rules_state=RulesState(False, "1.12", "판이 다릅니다", None, None))
    assert runner.calls == []
    assert outcome["stages"]["B"]["status"] == "rules" and outcome["stages"]["C"]["status"] == "rules"
    assert outcome["stages"]["E"]["status"] == "ok"
    assert outcome["status"] == "PARTIAL" and "판이 다릅니다" in env["sent"][0][1]


def test_polarion_failure_does_not_stop_trace_gap(env):
    class Broken:
        def iter_workitems(self, query, **_):
            from app.modules.daily_qa.polarion import PolarionError
            raise PolarionError("HTTP 401")
            yield

    outcome = run_daily(env["cfg"], today=MONDAY, polarion_factory=lambda: Broken(), runner=FakeRunner(_producer),
                        inputs=env["inputs"], rules_state=rules_ok(env["tmp"]), send_email=env["sender"], store=env["store"])
    stages = outcome["stages"]
    assert stages["collect_srs"]["status"] == "failed" and "HTTP 401" in stages["collect_srs"]["note"]
    assert stages["E"]["status"] == "ok"                              # 저장된 어제 스냅샷으로 돈다
    assert outcome["status"] == "PARTIAL"


def test_empty_srs_result_is_not_saved_as_snapshot(env):
    env["polarion"].srs = []
    outcome = _run(env)
    assert outcome["stages"]["collect_srs"]["status"] == "failed"
    assert not (env["cfg"].snapshot_dir / "2026-09-28.json").exists()


def test_broken_result_is_retried_once_then_failed(env):
    calls = []

    def bad(task, run):
        calls.append(task.task_id)
        (run.out_dir / f"{task.task_id}.json").write_text("not json", encoding="utf-8")

    outcome = _run(env, runner=FakeRunner(bad), today=TUESDAY)
    assert calls.count("B-001") == 2
    assert outcome["stages"]["B"]["status"] == "failed"
    assert outcome["stages"]["E"]["status"] == "not_due"


def test_rejected_findings_are_not_stored(env):
    def producer(task, run):
        write_result(run, task, [{"subject": "VP-10", "verdict": "수정 필수", "summary": "근거 없음", "evidence": []}])

    outcome = _run(env, runner=FakeRunner(producer), today=TUESDAY)
    assert not env["store"].list_findings(run_id=outcome["run_id"], skill=SKILL_B)
    assert "규칙 위반으로 버린 Finding" in outcome["stages"]["B"]["note"]


def test_dry_run_calls_no_ai_and_keeps_issue_cursor(env):
    outcome = _run(env, runner=None, dry_run=True, today=TUESDAY)
    assert outcome["stages"]["B"]["status"] == "skipped" and "dry-run" in outcome["stages"]["B"]["note"]
    assert env["store"].get_state("issues_last_success_at") == ""


def test_second_run_does_not_duplicate_trace_gap_findings(env):
    first = _run(env)
    second = _run(env, today=datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc))
    assert env["store"].list_findings(run_id=first["run_id"], skill=SKILL_E)
    assert not env["store"].list_findings(run_id=second["run_id"], skill=SKILL_E)


def test_concurrent_run_is_refused(env):
    lock = env["cfg"].data_dir / "run.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("123", encoding="utf-8")
    with pytest.raises(RunLocked):
        _run(env)


def test_missing_token_skips_ai_stages(env):
    cfg = make_settings(env["cfg"].root, env["tmp"] / "ws", claude_token="")
    outcome = run_daily(cfg, today=TUESDAY, polarion_factory=lambda: env["polarion"], inputs=env["inputs"],
                        rules_state=rules_ok(env["tmp"]), send_email=env["sender"], store=env["store"])
    assert outcome["stages"]["B"]["status"] == "skipped"
    assert "CLAUDE_CODE_OAUTH_TOKEN" in outcome["stages"]["preflight"]["note"]


def test_scheduler_launches_detached_process_only_with_credentials(env, monkeypatch):
    from app.modules.daily_qa import scheduled_jobs

    launched = []

    class FakePopen:
        def __init__(self, args, **kwargs):
            launched.append((args, kwargs))
            self.pid = 4242

    monkeypatch.setattr(scheduled_jobs.subprocess, "Popen", FakePopen)
    # Polarion 설정이 없으면 띄우지 않는다. Claude 토큰만 없는 경우는 test_daily_qa_fixes 가 본다.
    no_polarion = PolarionSettings(host="", token="", project_id="VXvue", srs_query="type:srs", issue_query="type:issue")
    monkeypatch.setattr(scheduled_jobs, "load", lambda: make_settings(env["cfg"].root, env["tmp"] / "ws", polarion=no_polarion))
    assert scheduled_jobs.launch_detached()["status"] == "not_configured" and not launched

    monkeypatch.setattr(scheduled_jobs, "load", lambda: env["cfg"])
    result = scheduled_jobs.launch_detached()
    assert result == {"status": "launched", "pid": 4242}
    args, kwargs = launched[0]
    assert args[-1].endswith("run_daily_qa.py")
    assert kwargs.get("start_new_session") or kwargs.get("creationflags")   # 웹 프로세스와 분리된다


def test_scheduler_registers_weekday_job():
    from apscheduler.schedulers.background import BackgroundScheduler

    from app.modules.daily_qa.scheduled_jobs import JOB_ID, register_scheduled_jobs

    scheduler = BackgroundScheduler()
    register_scheduled_jobs(scheduler)
    trigger = str(scheduler.get_job(JOB_ID).trigger)
    assert "day_of_week='mon-fri'" in trigger and "hour='7'" in trigger and "minute='30'" in trigger


def test_claude_tool_logs_are_collected_into_the_run_folder(env):
    """Validates: NFR-SEC-001 — 보낸 입력과 Claude 가 읽은 기록이 같은 실행 폴더에 모인다."""

    def producer(task, run):
        _producer(task, run)
        log_dir = run.run_dir / "logs"
        log_dir.mkdir(exist_ok=True)
        (log_dir / f"{task.task_id}.claude.json").write_text(
            json.dumps({"tool_calls": 1, "tool_call_log": [{"tool": "Read", "file_path": "runs/x/context/tc_index.jsonl"}]}),
            encoding="utf-8",
        )

    outcome = _run(env, runner=FakeRunner(producer), today=TUESDAY)
    logs = env["cfg"].output_dir / outcome["run_id"] / "claude_logs"
    saved = json.loads((logs / "B-001.attempt1.claude.json").read_text(encoding="utf-8"))
    assert saved["tool_call_log"][0]["file_path"].endswith("tc_index.jsonl")
    assert (env["cfg"].output_dir / outcome["run_id"] / "sent" / "B-001.json").is_file()
