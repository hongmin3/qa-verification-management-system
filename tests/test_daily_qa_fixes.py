"""일일 QA 점검 사양 불일치 수정의 회귀 테스트 (docs/SPEC_CODE_MISMATCH.md 4절, OPEN_QUESTIONS 8-7·8-11).

실제 Polarion·Claude·메일을 부르지 않는다.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.modules.daily_qa.agent_runner import FakeRunner, RunnerOutcome, build_command
from app.modules.daily_qa.pipeline import Inputs, run_daily
from app.modules.daily_qa.polarion import PolarionError
from app.modules.daily_qa.schema import SKILL_B, SKILL_C, SKILL_E, Evidence, Finding, rejection_reason
from app.modules.daily_qa.srs_snapshot import save_snapshot
from app.modules.daily_qa.store import DailyQaStore
from app.modules.daily_qa.workspace import ALLOWED_TOOLS, prepare
from tests.daily_qa_fixtures import FakePolarion, issue_item, make_settings, make_tc_workbook, rules_ok, srs_item, write_result

MONDAY = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)
TUESDAY = datetime(2026, 9, 29, 9, 0, tzinfo=timezone.utc)
WEDNESDAY = datetime(2026, 9, 30, 9, 0, tzinfo=timezone.utc)
EVIDENCE = [{"source_type": "srs", "location": "VP-10 / 본문", "validity": "Current"}]
YESTERDAY_SRS = [
    {"id": "VP-10", "old_id": "01-10-10", "title": "Viewer", "status": "draft", "text": "옛 본문", "is_category": False},
    {"id": "VP-11", "old_id": "01-10-11", "title": "Setting", "status": "draft", "text": "본문", "is_category": False},
]
UNCHANGED_SRS = [srs_item("VP-10", "01-10-10", "Viewer", text="옛 본문"), srs_item("VP-11", "01-10-11", "Setting")]


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    tc = make_tc_workbook(tmp_path / "tc.xlsx", [("TC_1", "VP-10", "A"), ("TC_2", "VP-11", "B")])
    save_snapshot(tmp_path / "repo" / "data" / "daily_qa" / "snapshots", "2026-09-27", YESTERDAY_SRS)
    sent: list = []
    return {
        "root": root, "tmp": tmp_path, "tc": tc, "sent": sent,
        "inputs": Inputs(tc_paths=[tc], manuals={}),
        "store": DailyQaStore(tmp_path / "app.db"),
        "polarion": FakePolarion(
            srs=[srs_item("VP-10", "01-10-10", "Viewer", text="새 본문"), srs_item("VP-11", "01-10-11", "Setting"),
                 srs_item("VP-12", "01-10-12", "Report")],
            issues=[issue_item("VP-6669", "2026-09-28T01:00:00Z", ["VP-10"])],
        ),
        "sender": lambda subject, text, body, to: sent.append((subject, text, to)) or {"status": "sent"},
    }


def _cfg(env, **overrides):
    cfg = make_settings(env["root"], env["tmp"] / "ws", **overrides)
    assert cfg.snapshot_dir == env["root"] / "data" / "daily_qa" / "snapshots"
    return cfg


def _ok_producer(task, run):
    if task.skill == SKILL_B:
        subjects = [change["srs"]["id"] for change in task.payload["changes"]]
        write_result(run, task, [{"subject": subjects[0], "verdict": "수정 필수", "summary": "검토", "evidence": EVIDENCE}])
    elif task.skill == SKILL_C:
        write_result(run, task, [{
            "subject": task.payload["issues"][0]["issue"]["id"], "verdict": "판정만", "summary": "확인",
            "issue_type": "Spec/Not Bug", "evidence": EVIDENCE,
        }])
    else:
        write_result(run, task, [])


class RecordingRunner:
    """작업 입력(마스킹 전 payload)을 기록하고, `fail` 에 든 작업은 실패시킨다."""

    def __init__(self, producer=_ok_producer, fail=(), error="exit=1 결과 파일을 쓰지 못했습니다"):
        self.producer = producer
        self.fail = set(fail)
        self.error = error
        self.seen: list[tuple[str, dict]] = []

    def run(self, task, run):
        self.seen.append((task.task_id, task.payload))
        if task.task_id in self.fail:
            return RunnerOutcome(False, 1, 0.0, self.error)
        self.producer(task, run)
        return RunnerOutcome(True, 0, 0.0)


def _run(env, cfg=None, **kwargs):
    kwargs.setdefault("today", TUESDAY)
    kwargs.setdefault("runner", RecordingRunner())
    kwargs.setdefault("rules_state", rules_ok(env["tmp"]))
    kwargs.setdefault("polarion_factory", lambda: env["polarion"])
    return run_daily(cfg or _cfg(env), inputs=env["inputs"], send_email=env["sender"], store=env["store"], **kwargs)


def _b_subjects(runner) -> list[str]:
    return [change["srs"]["id"] for task_id, payload in runner.seen if task_id.startswith("B-") for change in payload["changes"]]


def _c_subjects(runner) -> list[str]:
    return [item["issue"]["id"] for task_id, payload in runner.seen if task_id.startswith("C-") for item in payload["issues"]]


# --- MISMATCH 4-1 --------------------------------------------------------------------------------

# Validates: REQ-DAILY-001, REQ-DAILY-022
def test_polarion_failure_marks_srs_and_issue_reviews_failed_not_skipped(env):
    class Broken:
        def iter_workitems(self, query, **_):
            raise PolarionError("HTTP 401")
            yield

    outcome = _run(env, polarion_factory=lambda: Broken())
    stages = outcome["stages"]
    assert stages["B"]["status"] == "failed" and "SRS 수집" in stages["B"]["note"]
    assert stages["C"]["status"] == "failed" and "이슈 수집" in stages["C"]["note"]
    assert "변경·신규 항목 없음" not in stages["B"]["note"]


# Validates: REQ-DAILY-001, REQ-DAILY-022
def test_issue_collection_failure_only_fails_issue_review(env):
    polarion = env["polarion"]
    original = polarion.iter_workitems

    def iter_workitems(query, **kwargs):
        if "issue" in query:
            raise PolarionError("HTTP 503")
        yield from original(query, **kwargs)

    polarion.iter_workitems = iter_workitems
    outcome = _run(env)
    assert outcome["stages"]["C"]["status"] == "failed" and "이슈 수집" in outcome["stages"]["C"]["note"]
    assert outcome["stages"]["B"]["status"] == "ok"


# --- MISMATCH 4-2 --------------------------------------------------------------------------------

# Validates: REQ-DAILY-001
def test_scheduler_launches_without_claude_token_when_polarion_is_configured(env, monkeypatch):
    from app.modules.daily_qa import scheduled_jobs

    launched = []

    class FakePopen:
        def __init__(self, args, **kwargs):
            launched.append(args)
            self.pid = 7

    monkeypatch.setattr(scheduled_jobs.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(scheduled_jobs, "load", lambda: _cfg(env, claude_token=""))
    assert scheduled_jobs.launch_detached()["status"] == "launched" and launched


# --- MISMATCH 4-3 --------------------------------------------------------------------------------

# Validates: REQ-DAILY-003, NFR-DAILY-001
def test_srs_change_deferred_by_task_limit_is_reviewed_next_run(env):
    cfg = _cfg(env, max_tasks_per_run=1, batch_size=1)
    env["polarion"].issues = []
    first_runner = RecordingRunner()
    first = _run(env, cfg, runner=first_runner)
    assert "다음 실행으로 미룸" in first["stages"]["B"]["note"]
    assert _b_subjects(first_runner) == ["VP-12"]                     # 신규가 먼저, 변경 VP-10 은 미룸
    second_runner = RecordingRunner()
    _run(env, cfg, runner=second_runner, today=WEDNESDAY)             # SRS 는 화요일과 같다
    assert _b_subjects(second_runner) == ["VP-10"]
    third_runner = RecordingRunner()
    _run(env, cfg, runner=third_runner, today=datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc))
    assert _b_subjects(third_runner) == []                            # 검토를 마치면 다시 넣지 않는다


# Validates: REQ-DAILY-003, REQ-DAILY-016
def test_srs_change_in_failed_task_is_retried_next_run(env):
    cfg = _cfg(env, batch_size=1)
    env["polarion"].issues = []
    first = _run(env, cfg, runner=RecordingRunner(fail={"B-002"}))
    assert first["stages"]["B"]["status"] == "partial"
    second_runner = RecordingRunner()
    _run(env, cfg, runner=second_runner, today=WEDNESDAY)
    assert _b_subjects(second_runner) == ["VP-10"]


# Validates: REQ-DAILY-003
def test_carried_change_merges_with_new_change_of_the_same_srs(env):
    cfg = _cfg(env, batch_size=1)
    env["polarion"].issues = []
    _run(env, cfg, runner=RecordingRunner(fail={"B-002"}))            # VP-10 (옛 본문 -> 새 본문) 실패
    env["polarion"].srs[0] = srs_item("VP-10", "01-10-10", "Viewer 2", text="세 번째 본문")
    runner = RecordingRunner()
    _run(env, cfg, runner=runner, today=WEDNESDAY)
    changes = [change for task_id, payload in runner.seen if task_id.startswith("B-") for change in payload["changes"]]
    assert [change["srs"]["id"] for change in changes] == ["VP-10"]
    assert changes[0]["before"]["text"] == "옛 본문" and changes[0]["after"]["text"] == "세 번째 본문"
    assert set(changes[0]["fields"]) == {"text", "title"}


# --- MISMATCH 4-4 --------------------------------------------------------------------------------

def _issue_env(env):
    env["polarion"].srs = list(UNCHANGED_SRS)
    env["polarion"].issues = [
        issue_item("VP-6669", "2026-09-29T01:00:00Z", ["VP-10"]),
        issue_item("VP-6670", "2026-09-29T02:00:00Z", ["VP-11"]),
    ]


# Validates: REQ-DAILY-002, REQ-DAILY-004
def test_issue_in_failed_task_is_read_again_next_run(env):
    _issue_env(env)
    cfg = _cfg(env, batch_size=1)
    first = _run(env, cfg, runner=RecordingRunner(fail={"C-002"}))
    assert first["stages"]["C"]["status"] == "partial"
    runner = RecordingRunner()
    _run(env, cfg, runner=runner, today=WEDNESDAY)
    assert _c_subjects(runner) == ["VP-6670"]                         # 성공한 VP-6669 는 다시 보내지 않는다
    assert len(env["store"].list_findings(skill=SKILL_C)) == 2
    third = RecordingRunner()
    _run(env, cfg, runner=third, today=datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc))
    assert _c_subjects(third) == []
    assert env["store"].get_state("issues_last_success_at") == "2026-09-29T02:00:00Z"


# Validates: REQ-DAILY-002, REQ-DAILY-004, NFR-DAILY-001
def test_issue_deferred_by_task_limit_is_not_sent_twice(env):
    _issue_env(env)
    cfg = _cfg(env, batch_size=1, max_tasks_per_run=1)
    first_runner = RecordingRunner()
    first = _run(env, cfg, runner=first_runner)
    assert _c_subjects(first_runner) == ["VP-6669"] and "다음 실행으로 미룸" in first["stages"]["C"]["note"]
    second_runner = RecordingRunner()
    _run(env, cfg, runner=second_runner, today=WEDNESDAY)
    assert _c_subjects(second_runner) == ["VP-6670"]
    findings = env["store"].list_findings(skill=SKILL_C)
    assert sorted(item["subject"] for item in findings) == ["VP-6669", "VP-6670"]


# Validates: REQ-DAILY-002
def test_issue_updated_again_after_processing_is_read_again(env):
    _issue_env(env)
    cfg = _cfg(env, batch_size=1)
    _run(env, cfg, runner=RecordingRunner(fail={"C-002"}))
    env["polarion"].issues[0] = issue_item("VP-6669", "2026-09-29T05:00:00Z", ["VP-10"])
    runner = RecordingRunner()
    _run(env, cfg, runner=runner, today=WEDNESDAY)
    assert sorted(_c_subjects(runner)) == ["VP-6669", "VP-6670"]


# --- MISMATCH 4-5 --------------------------------------------------------------------------------

# Validates: REQ-DAILY-018, REQ-DAILY-003
def test_removed_srs_finding_is_kept_for_every_tc_row(env):
    env["polarion"].issues = []
    env["inputs"] = Inputs(tc_paths=[make_tc_workbook(env["tmp"] / "tc3.xlsx", [
        ("TC_1", "VP-10", "A"), ("TC_2", "VP-11", "B"), ("TC_3", "VP-13", "C"), ("TC_4", "VP-13", "D"), ("TC_5", "VP-13", "E"),
    ])], manuals={})
    save_snapshot(_cfg(env).snapshot_dir, "2026-09-28", [*YESTERDAY_SRS, {"id": "VP-13", "old_id": "", "title": "Gone",
                                                                         "status": "draft", "text": "", "is_category": False}])
    outcome = _run(env)
    removed = [item for item in env["store"].list_findings(run_id=outcome["run_id"], skill=SKILL_B) if item["task_id"] == "B-removed"]
    assert sorted(item["tc_ref"]["tc_id"] for item in removed) == ["TC_3", "TC_4", "TC_5"]


# Validates: REQ-DAILY-018
def test_same_tc_same_verdict_is_still_deduplicated(env):
    from app.modules.daily_qa.pipeline import _OpenFindings

    store = env["store"]
    keys = _OpenFindings(store)
    finding = {"subject": "VP-13", "verdict": "수정 필수", "summary": "s", "evidence": [],
               "tc_ref": {"workbook": "tc.xlsx", "sheet": "Viewer", "row": 4, "tc_id": "TC_3"}}
    assert keys.save("r1", SKILL_B, "B-removed", finding)
    assert not keys.save("r1", SKILL_B, "B-removed", dict(finding))
    other = {**finding, "tc_ref": {**finding["tc_ref"], "row": 5, "tc_id": "TC_4"}}
    assert keys.save("r1", SKILL_B, "B-removed", other)
    assert not _OpenFindings(store).save("r2", SKILL_B, "B-removed", dict(other))   # 다음 실행에서도 막는다


# --- MISMATCH 4-6 --------------------------------------------------------------------------------

def _finding(action: str) -> Finding:
    return Finding(subject="VP-6669", verdict="기존 TC 보강", summary="보강", action=action,
                   evidence=[Evidence(source_type="srs", location="VP-10", validity="Current")])


# Validates: REQ-DAILY-007
@pytest.mark.parametrize("action", [
    "닫기 버튼 동작을 확인하는 TC 보강 검토",
    "Closed 상태 이슈 목록 TC 보강",
    "앱 종료 시 설정 저장 TC 추가",
    "Close 버튼 클릭 시 창이 닫히는지 확인하는 TC 보강",
])
def test_normal_action_mentioning_close_button_is_kept(action):
    assert rejection_reason(SKILL_C, _finding(action)) is None


# Validates: REQ-DAILY-007
@pytest.mark.parametrize("action", [
    "이슈를 닫는다",
    "이슈 종료 처리",
    "VP-6669 이슈를 close 한다",
    "Close the issue",
    "기존 TC 를 덮어쓴다",
    "overwrite the existing TC",
    "TC 결과 삭제",
    "이력 삭제 후 재등록",
])
def test_forbidden_action_is_still_rejected(action):
    assert "금지된 조치" in (rejection_reason(SKILL_C, _finding(action)) or "")


# --- MISMATCH 4-7 --------------------------------------------------------------------------------

# Validates: REQ-DAILY-016, REQ-DAILY-008
def test_first_failure_reason_is_in_stage_note_and_email(env):
    env["polarion"].issues = []
    runner = RecordingRunner(fail={"B-001"}, error="exit=1 Claude AI usage limit reached|1759300000")
    outcome = _run(env, runner=runner)
    note = outcome["stages"]["B"]["note"]
    assert note.startswith("1개 작업 모두 실패") and "usage limit reached" in note
    assert "usage limit reached" in env["sent"][0][1]


# --- OPEN_QUESTIONS 8-7 --------------------------------------------------------------------------

# Validates: REQ-DAILY-013
def test_dry_run_saves_no_snapshot_and_no_findings(env):
    cfg = _cfg(env)
    outcome = _run(env, cfg, runner=None, dry_run=True, today=MONDAY, force_weekly=True)
    assert outcome["stages"]["E"]["counts"]["found"] > 0
    assert not (cfg.snapshot_dir / "2026-09-28.json").exists()
    assert "스냅샷" in outcome["stages"]["collect_srs"]["note"]
    assert env["store"].list_findings(run_id=outcome["run_id"]) == []
    assert (cfg.output_dir / outcome["run_id"] / "srs_diff.json").is_file()   # 비교 결과는 볼 수 있다
    # 다음 날 정식 실행은 시험 실행 날의 변경을 그대로 본다
    real = _run(env, cfg, today=TUESDAY)
    assert real["stages"]["collect_srs"]["counts"]["modified"] == 1
    assert env["store"].list_findings(run_id=real["run_id"], skill=SKILL_E) == []   # E 는 월요일 전용


# --- OPEN_QUESTIONS 8-11 -------------------------------------------------------------------------

READ_TOOLS = ("Read", "Glob", "Grep")


def _option_values(command: list[str], option: str) -> list[str]:
    start = command.index(option) + 1
    values = []
    for item in command[start:]:
        if item.startswith("--"):
            break
        values.append(item)
    return values


# Validates: NFR-SEC-001, REQ-DAILY-023
def test_read_tools_are_limited_to_the_workspace_in_cli_and_settings(tmp_path):
    command = build_command("claude")
    allowed = _option_values(command, "--allowedTools")
    for tool in READ_TOOLS:
        assert tool not in allowed                                     # 경로 제한 없는 허용은 없다
        assert f"{tool}(./**)" in allowed
    assert all(not rule.startswith(READ_TOOLS) or rule.endswith("(./**)") for rule in allowed)
    assert "--add-dir" not in command and "--dangerously-skip-permissions" not in command
    assert command[command.index("--permission-mode") + 1] == "dontAsk"   # 허용 목록 밖(작업 폴더 밖 읽기)은 거부

    run = prepare(tmp_path / "ws", tmp_path / "repo", "r1", None, None)
    settings = json.loads((run.root / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert settings["permissions"]["allow"] == list(ALLOWED_TOOLS)
    assert "additionalDirectories" not in settings["permissions"]
    assert settings["permissions"]["defaultMode"] == "dontAsk"


# Validates: NFR-SEC-001
def test_workspace_rules_tell_claude_reads_outside_are_refused(tmp_path):
    run = prepare(tmp_path / "ws", tmp_path / "repo", "r1", None, None)
    text = (run.root / "CLAUDE.md").read_text(encoding="utf-8")
    assert "작업 폴더 밖" in text
