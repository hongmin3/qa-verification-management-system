"""같은 날 다시 수집한 뒤의 자동 변경 리포트 메일 (TEST-ALMCHG-006). 가짜 Polarion·발송기만 쓴다.

Validates: REQ-ALMCHG-004
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.modules.daily_qa import holidays
from app.modules.daily_qa.change_report import build_change_report
from app.modules.daily_qa.settings import ChangeReportSettings
from app.modules.daily_qa.snapshots import KIND_ISSUES, KIND_SRS
from tests.daily_qa_fixtures import REPO_ROOT, issue_item
from tests.test_alm_change_mail import FRI, MON, change_mails, make
from tests.test_alm_change_report import SCHEDULE, issue, save, setup

TUE = MON.replace(day=20)
RECOLLECTED = "2026-10-16 기록은 지난 자동 메일 뒤 다시 수집되어"


@pytest.fixture(autouse=True)
def holiday_table(monkeypatch):
    monkeypatch.setattr(holidays, "table_path", lambda root, calendar="kr": REPO_ROOT / "config/holidays/kr.yaml")


def vp100(status="open", assignee=("u1", "김철수")):
    return [issue_item("VP-100", "2026-10-15T00:00:00Z", ["VP-10"], review="", status=status, title="저장 오류",
                       description="DESC-SECRET 설명 문장", repro="REPRO-SECRET 재현 절차", assignees=(assignee,))]


def savings(tmp_path):
    h = make(tmp_path)
    h.cfg = replace(h.cfg, savings_enabled=True)
    return h


def test_prepare_after_morning_mail_keeps_changes_for_next_mail(tmp_path):
    h = savings(tmp_path)
    h.run(FRI, trigger="collect")                       # 04:00 수집 → 첫 기록 메일
    h.issues = vp100(status="closed")
    h.run(FRI.replace(hour=10), trigger="prepare")      # 같은 날 [변경사항 점검하기] → 금요일 기록 덮어씀
    h.issues = vp100(status="closed", assignee=("u2", "이영희"))
    h.run(MON, trigger="collect")

    subject, text, html, _ = change_mails(h)[-1]
    assert "10-16(금)" in text and "상태 open → closed" in text      # 04:00 ~ 10:00 사이 변경
    assert "10-19(월)" in text and "담당자 김철수 → 이영희" in text
    assert RECOLLECTED in text and RECOLLECTED in html
    assert "이슈 1건" in subject


def test_same_day_rerun_of_collect_does_not_replace_mail_base(tmp_path):
    h = savings(tmp_path)
    h.run(FRI, trigger="collect")
    h.issues = vp100(status="closed")
    h.run(FRI.replace(hour=5), trigger="scheduled")    # 이미 보낸 날: 메일 기준 기록을 바꾸지 않는다
    h.run(MON, trigger="collect")

    _, text, _, _ = change_mails(h)[-1]
    assert "상태 open → closed" in text and RECOLLECTED in text


def test_ai_basis_and_dated_snapshots_unchanged(tmp_path):
    h = savings(tmp_path)
    h.run(FRI, trigger="collect")
    h.issues = vp100(status="closed")
    h.run(FRI.replace(hour=10), trigger="prepare")
    h.issues = vp100(status="closed", assignee=("u2", "이영희"))
    outcome = h.run(MON, trigger="collect")

    snaps = h.cfg.snapshot_dir
    for kind in (KIND_ISSUES, KIND_SRS):
        assert sorted(p.name for p in (snaps / kind).iterdir()) == ["2026-10-16.json", "2026-10-19.json"]
    assert not list(snaps.glob("*.json")) and not list(snaps.glob("*/*/**/*.json"))
    # AI 분석의 비교 기준은 금요일 날짜 기록(재수집 내용)이다. 상태 변경은 금요일 재수집이 이미 찾았다.
    assert outcome["summary"]["basis"] == {KIND_SRS: "2026-10-16", KIND_ISSUES: "2026-10-16"}
    assert outcome["summary"]["coverage"][KIND_ISSUES]["start"] == "2026-10-16"
    monday = h.events(run_id=outcome["run_id"])
    assert monday and not any(event["event_type"] == "ISSUE_STATUS_ONLY_CHANGED" for event in monday)
    from app.modules.daily_qa.snapshots import for_settings
    store = for_settings(h.cfg)
    friday = store.load(store.previous(KIND_ISSUES, "2026-10-19"))
    assert friday[0]["status"] == "closed"
    # 화면의 변경 목록은 날짜별 기록만 쓴다: 금요일(재수집) → 월요일 담당자 변경만 보인다.
    report = build_change_report(h.cfg, store, "2026-10-16", "2026-10-19", schedule=SCHEDULE)
    kinds = [line.kind for entity in report.issues for line in entity.lines]
    assert kinds == ["assignee"] and not any(RECOLLECTED in note for note in report.notices)


def test_no_rerun_uses_no_mail_base_notice(tmp_path):
    h = savings(tmp_path)
    h.run(FRI, trigger="collect")
    h.issues = vp100(status="closed")
    h.run(MON, trigger="collect")

    _, text, _, _ = change_mails(h)[-1]
    assert "상태 open → closed" in text and RECOLLECTED not in text


def test_mail_base_from_another_day_is_ignored(tmp_path):
    h = savings(tmp_path)
    h.run(FRI, trigger="collect")                        # 메일 기준 기록 = 금요일
    h.issues = vp100(status="closed")
    enabled = h.cfg.change_report
    h.cfg = replace(h.cfg, change_report=ChangeReportSettings(enabled=False))
    h.run(MON, trigger="collect")                        # 메일 없음 → 기준 기록 그대로 금요일
    h.cfg = replace(h.cfg, change_report=enabled)
    h.issues = vp100(status="closed", assignee=("u2", "이영희"))
    h.run(TUE, trigger="collect")

    _, text, _, _ = change_mails(h)[-1]
    assert "담당자 김철수 → 이영희" in text
    assert "상태 open → closed" not in text and "다시 수집되어" not in text


def test_collect_failure_does_not_replace_mail_base(tmp_path):
    h = savings(tmp_path)
    h.run(FRI, trigger="collect")
    base = h.cfg.change_mail_base_dir / "issues.json"
    before = base.read_bytes()

    class Down:
        def iter_workitems(self, *args, **kwargs):
            from app.modules.daily_qa.polarion import PolarionError
            raise PolarionError("Polarion 연결 실패")

    from app.modules.daily_qa.pipeline import run_daily
    run_daily(h.cfg, today=MON, polarion_factory=lambda: Down(), inputs=h.inputs, store=h.store, trigger="collect",
              send_email=lambda *args: h.sent.append(args) or {"status": "sent"})
    assert "수집 실패" in change_mails(h)[-1][0]
    assert base.read_bytes() == before


def test_report_prepends_mail_base_only_when_content_differs(tmp_path):
    cfg, store = setup(tmp_path)
    save(store, KIND_ISSUES, "2026-10-16", [issue(status="B", assignees=["u1"])])
    save(store, KIND_ISSUES, "2026-10-19", [issue(status="B", assignees=["u2"])])
    base = tmp_path / "base" / "issues.json"
    base.parent.mkdir()
    same = build_change_report(cfg, store, "2026-10-16", "2026-10-19", schedule=SCHEDULE,
                               mail_base={"issues": store.directory(KIND_ISSUES) / "2026-10-16.json"})
    assert [line.kind for e in same.issues for line in e.lines] == ["assignee"]
    assert not any("다시 수집되어" in note for note in same.notices)

    store.save(KIND_ISSUES, "2026-10-16", [issue(status="A", assignees=["u1"])], collected_at="2026-10-15T19:00:00+00:00")
    base.write_bytes((store.directory(KIND_ISSUES) / "2026-10-16.json").read_bytes())
    store.save(KIND_ISSUES, "2026-10-16", [issue(status="B", assignees=["u1"])], collected_at="2026-10-16T01:00:00+00:00")
    report = build_change_report(cfg, store, "2026-10-16", "2026-10-19", schedule=SCHEDULE, mail_base={"issues": base})
    lines = [(line.day, line.kind) for e in report.issues for line in e.lines]
    assert lines == [("2026-10-16", "status"), ("2026-10-19", "assignee")]
    assert report.compared_days["issues"] == ["2026-10-16", "2026-10-16", "2026-10-19"]
    assert any(note.startswith(RECOLLECTED) and "(10:00)" in note and "(04:00)" in note for note in report.notices)


def test_changes_guide_explains_mail_continues_from_mail_base():
    from jinja2 import Environment, FileSystemLoader

    templates = REPO_ROOT / "app/modules/qa_agent/templates"
    page = Environment(loader=FileSystemLoader(str(templates))).get_template("_guide_changes.html").render()
    assert "지난 자동 메일이 비교한 기록부터 이어서 비교" in page
