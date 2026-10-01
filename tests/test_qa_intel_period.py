"""기간을 정한 수동 실행과 Claude 사용량 기록 (SPEC specs/qa-intelligence.md). 가짜 Polarion·가짜 Claude 만 쓴다.

TEST-QAINTEL-012 기간 실행과 토큰 기록.

Validates: REQ-QAINTEL-026, REQ-QAINTEL-027
(REQ-QAINTEL-024 는 `parse_run_period`·`run_period_defaults` 의 날짜 검사만 본다.)

스냅샷은 한 Harness 로 실제 매일 실행을 돌려 만든 파일을 다른 Harness 로 복사해 쓴다. 그래서 복사받은
쪽 DB 에는 이벤트가 없고, 기간 실행이 찾은 변경만 이벤트가 된다. 가짜 Polarion 은 조회식에 "srs" 가
있으면 SRS 로 답한다(첫 제품 설정 `type:srs`/`type:issue`). 이 테스트는 그 구분에 기대는 단언을 하지 않고
엔진이 만든 이벤트·스냅샷·호출 수만 본다.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.storage import Storage
from app.modules.daily_qa.agent_runner import RunnerOutcome
from app.modules.qa_agent.dashboard import MAX_PERIOD_DAYS, PeriodError, parse_run_period, run_period_defaults
from tests.daily_qa_fixtures import issue_item, make_tc_workbook, srs_item
from tests.qa_intel_harness import DAY1, DAY2, DAY3, Harness

DAY4 = DAY1 + timedelta(days=3)
D1, D2, D3 = DAY1.date(), DAY2.date(), DAY3.date()
EVIDENCE = [{"source_type": "srs", "location": "VP-10 본문", "validity": "Current"}]
USAGE = {"input_tokens": 100, "output_tokens": 20, "cache_creation_input_tokens": 5, "cache_read_input_tokens": 3}
USAGE_TOTAL = 128


# -- 도구 ------------------------------------------------------------------------


def _harness(tmp: Path, name: str) -> Harness:
    base = tmp / name
    base.mkdir()
    workbook = make_tc_workbook(base / "(TC) VXvue_TestCase.xlsx", [("TC_1", "VP-10", "목록 표시"), ("TC_2", "VP-11", "검색")])
    h = Harness(base, tc_paths=[workbook])
    h.srs = [srs_item("VP-10", "01-01", "목록"), srs_item("VP-11", "01-02", "검색")]
    h.issues = [issue_item("VP-100", "2026-09-28T00:00:00Z", ["VP-10"], review="", status="open", description="처음 설명")]
    for target in ("VP-100", "VP-200", "VP-300"):
        h.script[target] = {"verdict": "SPEC_VIOLATION", "evidence": EVIDENCE,
                            "sections": {"duplicate": {"verdict": "NO_DUPLICATE_FOUND", "candidates": []}}}
    return h


def _to_day2(h: Harness) -> None:
    """DAY2 의 변경: 새 이슈 VP-200, VP-100 설명 수정."""
    h.issues[0] = issue_item("VP-100", "2026-09-29T09:00:00Z", ["VP-10"], review="", status="open", description="두번째 설명")
    h.issues.append(issue_item("VP-200", "2026-09-29T09:00:00Z", ["VP-11"], review="", status="open"))


def _to_day3(h: Harness) -> None:
    """DAY3 의 변경: 새 이슈 VP-300, VP-100 설명을 한 번 더 수정."""
    h.issues[0] = issue_item("VP-100", "2026-09-30T09:00:00Z", ["VP-10"], review="", status="open", description="세번째 설명")
    h.issues.append(issue_item("VP-300", "2026-09-30T09:00:00Z", ["VP-10"], review="", status="open"))


def _daily_three_days(h: Harness) -> list[dict]:
    outcomes = [h.run(DAY1)]
    _to_day2(h)
    outcomes.append(h.run(DAY2))
    _to_day3(h)
    outcomes.append(h.run(DAY3))
    return outcomes


def _copy_snapshots(src: Harness, dst: Harness, days: list[date]) -> None:
    for attr in ("srs_snapshot_dir", "issue_snapshot_dir"):
        source, target = getattr(src.cfg, attr), getattr(dst.cfg, attr)
        target.mkdir(parents=True, exist_ok=True)
        for day in days:
            shutil.copy2(source / f"{day.isoformat()}.json", target / f"{day.isoformat()}.json")


def _snapshot_files(h: Harness) -> dict[str, list[tuple[str, int]]]:
    files = {}
    for attr in ("srs_snapshot_dir", "issue_snapshot_dir"):
        directory = getattr(h.cfg, attr)
        files[attr] = sorted((path.name, path.stat().st_mtime_ns) for path in directory.glob("*.json")) if directory.is_dir() else []
    return files


def _pairs(events: list[dict]) -> set[tuple[str, str]]:
    return {(event["entity_id"], event["event_type"]) for event in events}


def _targets_called(h: Harness) -> list[str]:
    return sorted(target for _, targets in h.calls for target in targets)


@pytest.fixture
def source(tmp_path):
    """사흘 매일 실행으로 DAY1·DAY2·DAY3 스냅샷을 만든 원본."""
    h = _harness(tmp_path, "source")
    _daily_three_days(h)
    for attr in ("srs_snapshot_dir", "issue_snapshot_dir"):
        assert sorted(path.stem for path in getattr(h.cfg, attr).glob("*.json")) == [D1.isoformat(), D2.isoformat(), D3.isoformat()]
    return h


# -- REQ-QAINTEL-027 기간 실행 -------------------------------------------------------


def test_period_run_turns_changes_across_the_whole_period_into_events(tmp_path, source):
    """시작일 스냅샷과 비교하므로 어제 스냅샷만 보는 매일 실행이 놓치는 DAY2 변경도 이벤트가 된다."""
    h = _harness(tmp_path, "period")
    _copy_snapshots(source, h, [D1, D2])
    _to_day2(h)
    _to_day3(h)

    outcome = h.run(DAY3, since=D1)

    events = h.events(outcome["run_id"])
    assert _pairs(events) == {("VP-100", "ISSUE_CONTENT_UPDATED"), ("VP-200", "ISSUE_CREATED"), ("VP-300", "ISSUE_CREATED")}
    content = next(event for event in events if event["entity_id"] == "VP-100")
    assert "처음 설명" in json.dumps(content["before"], ensure_ascii=False)     # 기준은 시작일(DAY1) 스냅샷
    assert "세번째 설명" in json.dumps(content["after"], ensure_ascii=False)
    assert _targets_called(h) == ["VP-100", "VP-200", "VP-300"]
    assert all(event["analysis_status"] == "done" for event in events)
    # 종료일이 오늘이면 Polarion 을 새로 읽고 오늘 스냅샷을 저장한다 (순서 2).
    assert h.polarion.queries
    assert (h.cfg.issue_snapshot_dir / f"{D3.isoformat()}.json").is_file()
    assert outcome["status"] == "SUCCESS"


def test_normal_run_on_same_state_sees_only_the_last_day(tmp_path, source):
    """대조군: 기간 없이 돌리면 기준은 어제(DAY2) 스냅샷이라 DAY2 에 생긴 VP-200 은 이벤트가 되지 않는다."""
    h = _harness(tmp_path, "normal")
    _copy_snapshots(source, h, [D1, D2])
    _to_day2(h)
    _to_day3(h)

    outcome = h.run(DAY3)

    assert _pairs(h.events(outcome["run_id"])) == {("VP-100", "ISSUE_CONTENT_UPDATED"), ("VP-300", "ISSUE_CREATED")}


def test_period_rerun_does_not_recreate_end_states_already_analyzed(tmp_path):
    """매일 실행이 이미 분석한 끝 상태(바뀐 뒤 값이 같은 이벤트)는 기간 실행에서 다시 이벤트가 되지 않는다 (순서 4).

    VP-100 은 DAY1→DAY3 비교에서 '처음→세번째' 이벤트가 된다. 이 이벤트의 지문(fingerprint)은 매일 실행이 남긴
    '두번째→세번째' 와 달라 지문 중복 검사로는 걸러지지 않는다. 바뀐 뒤 값 지문(after_fingerprint) 검사만 거른다.
    """
    h = _harness(tmp_path, "rerun")
    _daily_three_days(h)
    before_events = h.events()
    assert before_events and all(event["analysis_status"] == "done" for event in before_events if event["analysis_required"])
    calls_before = list(h.calls)

    outcome = h.run(DAY3 + timedelta(hours=1), since=D1)

    # 비교 자체는 했다: 실행 폴더의 이벤트 목록에 '처음→세번째' 가 있다.
    detected = json.loads((h.cfg.output_dir / outcome["run_id"] / "change_events.json").read_text(encoding="utf-8"))
    period_event = next(event for event in detected if event["entity_id"] == "VP-100" and event["event_type"] == "ISSUE_CONTENT_UPDATED")
    assert "처음 설명" in json.dumps(period_event["before"], ensure_ascii=False)
    stored_fingerprints = {event["fingerprint"] for event in before_events}
    assert period_event["fingerprint"] not in stored_fingerprints                   # 지문 검사로는 못 거른다
    assert period_event["after_fingerprint"] in {event["after_fingerprint"] for event in before_events}

    # 그래도 새 이벤트도, 새 Claude 호출도 없다.
    assert h.events(outcome["run_id"]) == []
    assert len(h.events()) == len(before_events)
    assert h.calls == calls_before
    assert outcome["summary"]["claude_calls"] == 0
    assert outcome["stages"]["events"]["counts"]["stored"] == 0


def test_period_rerun_twice_is_stable(tmp_path, source):
    """같은 기간을 두 번 돌려도 두 번째 실행은 이벤트와 Claude 호출을 더하지 않는다."""
    h = _harness(tmp_path, "twice")
    _copy_snapshots(source, h, [D1, D2])
    _to_day2(h)
    _to_day3(h)
    first = h.run(DAY3, since=D1)
    calls = len(h.calls)

    second = h.run(DAY3 + timedelta(hours=1), since=D1)

    assert len(h.events(first["run_id"])) == 3
    assert h.events(second["run_id"]) == []
    assert len(h.calls) == calls and second["summary"]["claude_calls"] == 0


def test_period_rerun_does_not_recreate_srs_text_end_state(tmp_path):
    h = _harness(tmp_path, "srs-text")
    h.srs[0] = srs_item("VP-10", "01-01", "목록", text="목록을 처음 방식으로 표시한다.")
    h.run(DAY1)
    h.srs[0] = srs_item("VP-10", "01-01", "목록", text="목록을 두번째 방식으로 표시한다.")
    h.run(DAY2)
    h.srs[0] = srs_item("VP-10", "01-01", "목록", text="목록을 세번째 방식으로 표시한다.")
    h.run(DAY3)
    assert _pairs(h.events()) == {("VP-10", "SRS_UPDATED")}
    calls_before = list(h.calls)

    outcome = h.run(DAY3 + timedelta(hours=1), since=D1)

    assert h.events(outcome["run_id"]) == []
    assert h.calls == calls_before


def test_srs_title_end_state_is_not_recreated(tmp_path):
    """본문이 아닌 SRS 필드(제목)는 바뀐 뒤 값만 지문에 들어가 기간 실행이 다시 만들지 않는다."""
    h = _harness(tmp_path, "srs-title")
    h.run(DAY1)
    h.srs[0] = srs_item("VP-10", "01-01", "목록 두번째")
    h.run(DAY2)
    h.srs[0] = srs_item("VP-10", "01-01", "목록 세번째")
    h.run(DAY3)
    calls_before = list(h.calls)

    outcome = h.run(DAY3 + timedelta(hours=1), since=D1)

    assert h.events(outcome["run_id"]) == []
    assert h.calls == calls_before


def test_past_until_compares_stored_snapshots_and_saves_nothing(tmp_path, source):
    """종료일이 지난 날이면 Polarion 을 읽지 않고, 그날 저장 스냅샷과 비교하며, 스냅샷을 저장하지 않는다 (순서 3)."""
    h = _harness(tmp_path, "past")
    _copy_snapshots(source, h, [D1, D2, D3])
    h.issues.append(issue_item("VP-900", "2026-10-02T00:00:00Z", ["VP-10"], review="", status="open"))  # 읽으면 안 되는 값
    files_before = _snapshot_files(h)

    outcome = h.run(DAY4, since=D1, until=D2)

    assert _snapshot_files(h) == files_before
    assert not (h.cfg.issue_snapshot_dir / f"{DAY4.date().isoformat()}.json").exists()
    assert h.polarion.queries == []
    events = h.events(outcome["run_id"])
    # DAY1 과 DAY2 저장본의 차이만: VP-300(DAY3)·VP-900(오늘 Polarion)은 없다.
    assert _pairs(events) == {("VP-100", "ISSUE_CONTENT_UPDATED"), ("VP-200", "ISSUE_CREATED")}
    content = next(event for event in events if event["entity_id"] == "VP-100")
    assert "두번째 설명" in json.dumps(content["after"], ensure_ascii=False)
    assert "기간 분석" in outcome["stages"]["collect_issues"]["note"]


def test_past_until_without_exact_snapshot_uses_nearest_earlier(tmp_path, source):
    """종료일 당일 스냅샷이 없으면 그 전 가장 가까운 저장본(DAY2)을 쓴다."""
    h = _harness(tmp_path, "nearest")
    _copy_snapshots(source, h, [D1, D2])
    files_before = _snapshot_files(h)

    outcome = h.run(DAY4, since=D1, until=D3)

    assert _pairs(h.events(outcome["run_id"])) == {("VP-100", "ISSUE_CONTENT_UPDATED"), ("VP-200", "ISSUE_CREATED")}
    assert _snapshot_files(h) == files_before


def test_until_equal_to_today_collects_fresh_and_saves_snapshot(tmp_path, source):
    """경계: 종료일 = 오늘이면 지난 날 처리가 아니라 새로 수집하고 오늘 스냅샷을 저장한다."""
    h = _harness(tmp_path, "today")
    _copy_snapshots(source, h, [D1, D2])
    _to_day2(h)
    _to_day3(h)

    outcome = h.run(DAY3, since=D1, until=D3)

    assert h.polarion.queries
    assert (h.cfg.issue_snapshot_dir / f"{D3.isoformat()}.json").is_file()
    assert ("VP-300", "ISSUE_CREATED") in _pairs(h.events(outcome["run_id"]))


def test_since_before_oldest_snapshot_uses_oldest_as_baseline(tmp_path, source):
    """시작일 전에 스냅샷이 하나도 없으면 가장 오래된 스냅샷(DAY2)을 기준으로 삼는다 (순서 1)."""
    h = _harness(tmp_path, "oldest")
    _copy_snapshots(source, h, [D2, D3])

    outcome = h.run(DAY4, since=D1, until=D3)

    # DAY2→DAY3 차이만. DAY2 이전 변경(VP-200 생성)은 알 수 없다.
    assert _pairs(h.events(outcome["run_id"])) == {("VP-100", "ISSUE_CONTENT_UPDATED"), ("VP-300", "ISSUE_CREATED")}


def test_no_stored_snapshot_up_to_until_fails_collection(tmp_path, source):
    """종료일 이전 저장 스냅샷이 없으면 수집 단계가 '실패' 이고 이벤트·스냅샷·Claude 호출이 없다 (안 될 때 표)."""
    h = _harness(tmp_path, "missing")
    _copy_snapshots(source, h, [D2, D3])
    files_before = _snapshot_files(h)

    outcome = h.run(DAY4, since=D1, until=D1)

    message = f"{D1.isoformat()} 이전 저장 스냅샷이 없습니다."
    for key in ("collect_srs", "collect_issues"):
        assert outcome["stages"][key]["status"] == "failed"
        assert outcome["stages"][key]["note"].startswith(message)
    assert outcome["status"] == "PARTIAL"
    assert h.events() == []
    assert h.calls == [] and outcome["summary"]["claude_calls"] == 0
    assert _snapshot_files(h) == files_before
    assert h.polarion.queries == []


def test_period_is_recorded_in_run_audit(tmp_path, source):
    h = _harness(tmp_path, "audit")
    _copy_snapshots(source, h, [D1, D2, D3])

    outcome = h.run(DAY4, since=D1, until=D2)

    audit = json.loads((h.cfg.output_dir / outcome["run_id"] / "audit.json").read_text(encoding="utf-8"))
    assert audit["period"] == {"since": D1.isoformat(), "until": D2.isoformat()}


# -- REQ-QAINTEL-026 토큰 사용량 ------------------------------------------------------


def _token_run(tmp_path, name="tokens") -> tuple[Harness, dict]:
    h = _harness(tmp_path, name)
    h.usage = dict(USAGE)
    h.run(DAY1)
    _to_day2(h)
    return h, h.run(DAY2)


def test_token_usage_goes_into_run_summary(tmp_path):
    h, outcome = _token_run(tmp_path)
    calls = outcome["summary"]["claude_calls"]
    assert calls == len(h.calls) >= 1
    usage = outcome["summary"]["token_usage"]
    assert usage["input_tokens"] == 100 * calls and usage["output_tokens"] == 20 * calls
    assert usage["cache_creation_input_tokens"] == 5 * calls and usage["cache_read_input_tokens"] == 3 * calls
    assert usage["total_tokens"] == USAGE_TOTAL * calls
    assert usage["cost_usd"] == pytest.approx(0.01 * calls)
    stored = h.store.get_run(outcome["run_id"])
    stored_summary = stored.get("summary") or json.loads(stored.get("summary_json") or "{}")
    assert stored_summary["token_usage"]["total_tokens"] == USAGE_TOTAL * calls


def test_token_usage_appears_in_cost_dashboard_and_daily_usage(tmp_path):
    h, outcome = _token_run(tmp_path)
    total = USAGE_TOTAL * outcome["summary"]["claude_calls"]
    storage = Storage(db_path=h.cfg.db_path)          # 임시 DB. 운영 data/ 를 쓰지 않는다.
    assert Path(h.cfg.db_path).is_relative_to(tmp_path)

    stats = storage.cost_dashboard_stats()
    assert stats["modules"]["qa_agent_run"] == {"tokens": total, "count": 1}  # 기준만 만든 첫 실행(0 토큰)은 없다
    recent = [item for item in stats["recent"] if item["module"] == "qa_agent_run"]
    assert [(item["id"], item["tokens"], item["product"]) for item in recent] == [(outcome["run_id"], total, h.cfg.slug)]
    assert recent[0]["cache_hits"] is None and recent[0]["cache_calls"] is None   # Claude CLI 는 캐시 Hit율에서 뺀다
    assert stats["cache_sample_size"] == 0
    assert sum(day["tokens"] for day in stats["daily"]) == total

    assert storage.tokens_used_since("2000-01-01T00:00:00+00:00") == total
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    assert storage.tokens_used_since(future) == 0


def test_run_without_usage_adds_nothing_to_cost_dashboard(tmp_path):
    h = _harness(tmp_path, "nousage")
    h.run(DAY1)
    _to_day2(h)
    outcome = h.run(DAY2)
    assert outcome["summary"]["claude_calls"] >= 1
    assert outcome["summary"]["token_usage"]["total_tokens"] == 0
    storage = Storage(db_path=h.cfg.db_path)
    assert "qa_agent_run" not in storage.cost_dashboard_stats()["modules"]
    assert storage.tokens_used_since("2000-01-01T00:00:00+00:00") == 0


def test_retried_format_error_attempt_tokens_are_counted(tmp_path):
    """형식 오류로 다시 돈 시도도 이미 쓴 토큰이라 센다 (순서 1)."""
    h = _harness(tmp_path, "retry")
    h.usage = dict(USAGE)
    h.run(DAY1)
    h.issues.append(issue_item("VP-200", "2026-09-29T09:00:00Z", ["VP-11"], review="", status="open"))
    attempts = []

    def first_bad(task, run):
        attempts.append(task.task_id)
        if len(attempts) == 1:   # 결과 파일 없이 끝남 → 형식 오류로 한 번 더 돈다
            return RunnerOutcome(True, 0, 0.0, meta={"usage": dict(USAGE), "total_cost_usd": 0.01})
        h.outcome_override = None
        try:
            return h._produce(task, run)
        finally:
            h.outcome_override = first_bad

    h.outcome_override = first_bad
    outcome = h.run(DAY2)

    assert outcome["summary"]["claude_calls"] == 2
    assert outcome["summary"]["token_usage"]["total_tokens"] == 2 * USAGE_TOTAL
    assert Storage(db_path=h.cfg.db_path).cost_dashboard_stats()["modules"]["qa_agent_run"]["tokens"] == 2 * USAGE_TOTAL


def test_period_run_tokens_are_recorded_too(tmp_path, source):
    h = _harness(tmp_path, "period-tokens")
    h.usage = dict(USAGE)
    _copy_snapshots(source, h, [D1, D2, D3])

    outcome = h.run(DAY4, since=D1, until=D2)

    total = USAGE_TOTAL * outcome["summary"]["claude_calls"]
    assert total > 0
    assert Storage(db_path=h.cfg.db_path).cost_dashboard_stats()["modules"]["qa_agent_run"]["tokens"] == total


# -- 실행 기간 입력 검사 (REQ-QAINTEL-027 안 될 때, REQ-QAINTEL-024) ----------------------

TODAY = date(2026, 9, 30)


def test_parse_run_period_both_empty_means_plain_run():
    assert parse_run_period("", "", today=TODAY) == ("", "")


def test_parse_run_period_since_only_defaults_until_to_today():
    assert parse_run_period("2026-09-25", "", today=TODAY) == ("2026-09-25", "")
    assert parse_run_period("2026-09-30", "", today=TODAY) == ("2026-09-30", "")   # 경계: 시작일 = 오늘


def test_parse_run_period_until_today_is_returned_empty_and_past_until_as_iso():
    assert parse_run_period("2026-09-25", "2026-09-30", today=TODAY) == ("2026-09-25", "")
    assert parse_run_period("2026-09-25", "2026-09-29", today=TODAY) == ("2026-09-25", "2026-09-29")
    assert parse_run_period("2026-09-29", "2026-09-29", today=TODAY) == ("2026-09-29", "2026-09-29")   # 경계: 하루


def test_parse_run_period_until_only_defaults_since_to_day_before_until():
    """종료일만 주면 시작일은 종료일 전날이다 (사용자 결정 2026-10-01, REQ-QAINTEL-027)."""
    assert parse_run_period("", "2026-09-29", today=TODAY) == ("2026-09-28", "2026-09-29")
    assert parse_run_period("", "2026-09-28", today=TODAY) == ("2026-09-27", "2026-09-28")


def test_parse_run_period_rejects_future_until():
    with pytest.raises(PeriodError, match="종료일은 오늘보다 늦을 수 없습니다."):
        parse_run_period("2026-09-25", "2026-10-01", today=TODAY)


def test_parse_run_period_rejects_since_after_until():
    with pytest.raises(PeriodError, match="시작일이 종료일보다 늦습니다."):
        parse_run_period("2026-09-29", "2026-09-28", today=TODAY)


@pytest.mark.parametrize("since, until", [("2026/09/25", ""), ("2026-13-01", ""), ("", "yesterday"), ("2026-9-5", "2026-09-30")])
def test_parse_run_period_rejects_bad_format(since, until):
    with pytest.raises(PeriodError, match="날짜는 YYYY-MM-DD 로 입력하세요."):
        parse_run_period(since, until, today=TODAY)


def test_parse_run_period_max_span_boundary():
    assert MAX_PERIOD_DAYS == 366
    exactly = (TODAY - timedelta(days=MAX_PERIOD_DAYS - 1)).isoformat()
    assert parse_run_period(exactly, "", today=TODAY) == (exactly, "")
    too_long = (TODAY - timedelta(days=MAX_PERIOD_DAYS)).isoformat()
    with pytest.raises(PeriodError, match="조회 기간은 366일까지입니다."):
        parse_run_period(too_long, "", today=TODAY)


def test_run_period_defaults():
    assert run_period_defaults(TODAY) == {"since": "2026-09-29", "until": "2026-09-30", "max": "2026-09-30", "min": ""}


def test_period_error_is_value_error():
    assert issubclass(PeriodError, ValueError)


# -- CLI --since/--until ---------------------------------------------------------------


def test_cli_passes_since_and_until_to_pipeline(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "scripts" / "run_daily_qa.py"
    spec = importlib.util.spec_from_file_location("run_daily_qa_for_period_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    captured = {}

    def fake_run_daily(cfg, **kwargs):
        captured.update(kwargs)
        return {"run_id": "r1", "status": "SUCCESS", "email": {"status": "disabled"}, "stages": {}, "summary": {}}

    monkeypatch.setattr(module, "load", lambda product=None: SimpleNamespace(enabled=True))
    monkeypatch.setattr(module, "run_daily", fake_run_daily)
    monkeypatch.setattr(sys, "argv", ["run_daily_qa.py", "--since", "2026-09-25", "--until", "2026-09-29", "--no-email"])

    assert module.main() == 0
    assert captured["since"] == date(2026, 9, 25) and captured["until"] == date(2026, 9, 29)
