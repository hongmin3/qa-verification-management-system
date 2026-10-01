"""일일 QA 점검 사양 불일치 수정의 회귀 테스트 (docs/SPEC_CODE_MISMATCH.md 4절, OPEN_QUESTIONS 8-7·8-11).

QA Intelligence 개편(specs/qa-intelligence.md) 뒤에도 유지되는 것만 남겼다. 이슈 기준 시각·미룬 SRS
변경 이월(MISMATCH 4-3·4-4)은 변경 이벤트의 대기·재시도(REQ-QAINTEL-006)로 바뀌어
`tests/test_qa_intel_pipeline.py` 가 본다. 실제 Polarion·Claude·메일을 부르지 않는다.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.modules.daily_qa.agent_runner import FakeRunner, RunnerOutcome, build_command
from app.modules.daily_qa.pipeline import Inputs, run_daily
from app.modules.daily_qa.polarion import PolarionError
from app.modules.daily_qa.schema import SKILL_C, SKILL_COVERAGE, SKILL_E, Evidence, Finding, rejection_reason
from app.modules.daily_qa.srs_snapshot import save_snapshot
from app.modules.daily_qa.store import DailyQaStore
from app.modules.daily_qa.workspace import ALLOWED_TOOLS, prepare
from tests.daily_qa_fixtures import FakePolarion, issue_item, make_settings, make_tc_workbook, rules_ok, srs_item, write_result

MONDAY = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)
TUESDAY = datetime(2026, 9, 29, 9, 0, tzinfo=timezone.utc)
YESTERDAY_SRS = [
    {"id": "VP-10", "old_id": "01-10-10", "title": "Viewer", "status": "draft", "text": "옛 본문", "is_category": False},
    {"id": "VP-11", "old_id": "01-10-11", "title": "Setting", "status": "draft", "text": "본문", "is_category": False},
]


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    tc = make_tc_workbook(tmp_path / "tc.xlsx", [("TC_1", "VP-10", "A"), ("TC_2", "VP-11", "B")])
    cfg = make_settings(root, tmp_path / "ws")
    save_snapshot(cfg.srs_snapshot_dir, "2026-09-27", YESTERDAY_SRS)
    sent: list = []
    return {
        "root": root, "tmp": tmp_path, "tc": tc, "sent": sent, "cfg": cfg,
        "inputs": Inputs(tc_paths=[tc], manuals={}),
        "store": DailyQaStore(cfg.db_path),
        "polarion": FakePolarion(
            srs=[srs_item("VP-10", "01-10-10", "Viewer", text="새 본문"), srs_item("VP-11", "01-10-11", "Setting"),
                 srs_item("VP-12", "01-10-12", "Report")],
            issues=[issue_item("VP-6669", "2026-09-28T01:00:00Z", ["VP-10"])],
        ),
        "sender": lambda subject, text, body, to: sent.append((subject, text, to)) or {"status": "sent"},
    }


def _cfg(env, **overrides):
    return make_settings(env["root"], env["tmp"] / "ws", **overrides)


def _run(env, cfg=None, **kwargs):
    kwargs.setdefault("today", TUESDAY)
    kwargs.setdefault("runner", FakeRunner(lambda task, run: write_result(run, task, [])))
    kwargs.setdefault("rules_state", rules_ok(env["tmp"]))
    kwargs.setdefault("polarion_factory", lambda: env["polarion"])
    return run_daily(cfg or env["cfg"], inputs=env["inputs"], send_email=env["sender"], store=env["store"],
                     spec_chunks_loader=lambda: ([], {}, []), **kwargs)


# --- MISMATCH 4-1 --------------------------------------------------------------------------------

# Validates: REQ-DAILY-001, REQ-DAILY-022, REQ-QAINTEL-009
def test_polarion_failure_marks_collection_failed_not_skipped(env):
    class Broken:
        def iter_workitems(self, query, **_):
            raise PolarionError("HTTP 401")
            yield

    outcome = _run(env, polarion_factory=lambda: Broken())
    stages = outcome["stages"]
    assert stages["collect_srs"]["status"] == "failed" and "HTTP 401" in stages["collect_srs"]["note"]
    assert stages["collect_issues"]["status"] == "failed"
    assert outcome["status"] == "PARTIAL"                   # '변경사항 없음' 으로 보이지 않는다


# Validates: REQ-DAILY-001, REQ-DAILY-022
def test_issue_collection_failure_only_fails_issue_collection(env):
    polarion = env["polarion"]
    original = polarion.iter_workitems

    def iter_workitems(query, **kwargs):
        if "issue" in query:
            raise PolarionError("HTTP 503")
        yield from original(query, **kwargs)

    polarion.iter_workitems = iter_workitems
    outcome = _run(env)
    assert outcome["stages"]["collect_issues"]["status"] == "failed"
    assert outcome["stages"]["collect_srs"]["status"] == "ok"
    assert env["store"].list_events(product="vxvue", event_types=("SRS_UPDATED",))   # SRS 변경은 그대로 남는다


# --- MISMATCH 4-2 --------------------------------------------------------------------------------

# Validates: REQ-DAILY-001
def test_scheduler_launches_without_claude_token_when_polarion_is_configured(env, monkeypatch):
    from app.modules.daily_qa import scheduled_jobs

    launched = []

    class FakePopen:
        def __init__(self, args, **kwargs):
            launched.append(args)
            self.pid = 7

    monkeypatch.setattr(scheduled_jobs, "load", lambda product=None: _cfg(env, claude_token=""))
    assert scheduled_jobs.launch_detached(trigger="manual", popen=FakePopen)["status"] == "launched" and launched


# Validates: REQ-DAILY-018, REQ-DAILY-003
def test_removed_srs_finding_is_kept_for_every_tc_row(env):
    env["polarion"].issues = []
    env["inputs"] = Inputs(tc_paths=[make_tc_workbook(env["tmp"] / "tc3.xlsx", [
        ("TC_1", "VP-10", "A"), ("TC_2", "VP-11", "B"), ("TC_3", "VP-13", "C"), ("TC_4", "VP-13", "D"), ("TC_5", "VP-13", "E"),
    ])], manuals={})
    save_snapshot(env["cfg"].srs_snapshot_dir, "2026-09-28", [*YESTERDAY_SRS, {"id": "VP-13", "old_id": "", "title": "Gone",
                                                                              "status": "draft", "text": "", "is_category": False}])
    outcome = _run(env)
    removed = [item for item in env["store"].list_findings(run_id=outcome["run_id"], skill=SKILL_COVERAGE)
               if item["task_id"] == "COV-removed"]
    assert sorted(item["tc_ref"]["tc_id"] for item in removed) == ["TC_3", "TC_4", "TC_5"]
    assert {item["verdict"] for item in removed} == {"UPDATE_EXISTING"}
    assert {item["analysis_type"] for item in removed} == {"SRS_REMOVED"}
    events = env["store"].list_events(product="vxvue", event_types=("SRS_REMOVED",))
    assert [event["entity_id"] for event in events] == ["VP-13"] and not events[0]["analysis_required"]


# Validates: REQ-DAILY-018
def test_same_tc_same_verdict_is_still_deduplicated(env):
    from app.modules.daily_qa.pipeline import _OpenFindings

    store = env["store"]
    store.create_run("r1", False)
    store.create_run("r2", False)
    keys = _OpenFindings(store)
    finding = {"subject": "VP-13", "verdict": "UPDATE_EXISTING", "summary": "s", "evidence": [],
               "tc_ref": {"workbook": "tc.xlsx", "sheet": "Viewer", "row": 4, "tc_id": "TC_3"}}
    assert keys.save("r1", SKILL_COVERAGE, "COV-removed", finding)
    assert not keys.save("r1", SKILL_COVERAGE, "COV-removed", dict(finding))
    other = {**finding, "tc_ref": {**finding["tc_ref"], "row": 5, "tc_id": "TC_4"}}
    assert keys.save("r1", SKILL_COVERAGE, "COV-removed", other)
    assert not _OpenFindings(store).save("r2", SKILL_COVERAGE, "COV-removed", dict(other))   # 다음 실행에서도 막는다


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
    runner = FakeRunner(lambda task, run: RunnerOutcome(False, 1, 0.0, "exit=1 결과 파일을 쓰지 못했습니다 (timeout)"))
    outcome = _run(env, runner=runner)
    note = outcome["stages"]["SPEC_COVERAGE"]["note"]
    assert note.startswith("1개 작업 모두 실패") and "결과 파일을 쓰지 못했습니다" in note
    assert "결과 파일을 쓰지 못했습니다" in env["sent"][0][1]


# --- OPEN_QUESTIONS 8-7 --------------------------------------------------------------------------

# Validates: REQ-DAILY-013
def test_dry_run_saves_no_snapshot_and_no_findings(env):
    cfg = env["cfg"]
    outcome = _run(env, cfg, runner=None, dry_run=True, today=MONDAY, force_weekly=True)
    assert outcome["stages"]["E"]["counts"]["found"] > 0
    assert not (cfg.srs_snapshot_dir / "2026-09-28.json").exists()
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
