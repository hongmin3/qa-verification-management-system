"""변경 리포트 메일 (TEST-ALMCHG-004). 가짜 발송기만 쓴다. Validates: REQ-ALMCHG-004."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from app.core import notifier
from app.modules.daily_qa import change_mail, holidays, snapshots
from app.modules.daily_qa.change_report import build_change_report
from app.modules.daily_qa.settings import ChangeReportSettings
from app.modules.daily_qa.snapshots import KIND_ISSUES, KIND_SRS
from tests.daily_qa_fixtures import REPO_ROOT, issue_item, make_tc_workbook, srs_item
from tests.qa_intel_harness import Harness
from tests.test_alm_change_report import SCHEDULE, issue, save, setup, srs

KST = holidays.KST
FRI = datetime(2026, 10, 16, 4, 0, tzinfo=KST)
MON = datetime(2026, 10, 19, 4, 0, tzinfo=KST)
CLOSING = "이 메일은 수집 결과를 자동으로 정리한 것입니다. AI 판단은 들어 있지 않습니다."


@pytest.fixture(autouse=True)
def holiday_table(monkeypatch):
    monkeypatch.setattr(holidays, "table_path", lambda root, calendar="kr": REPO_ROOT / "config/holidays/kr.yaml")


def make(tmp_path, **settings):
    workbook = make_tc_workbook(tmp_path / "(TC) VXvue_TestCase.xlsx", [("TC_1", "VP-10", "목록 표시")])
    h = Harness(tmp_path, tc_paths=[workbook], **settings)
    h.srs = [srs_item("VP-10", "01-01", "목록")]
    h.issues = [issue_item("VP-100", "2026-10-15T00:00:00Z", ["VP-10"], review="", status="open", title="저장 오류",
                           description="DESC-SECRET 설명 문장", repro="REPRO-SECRET 재현 절차", assignees=(("u1", "김철수"),))]
    return h


def kst_minute(raw: str) -> str:
    return datetime.fromisoformat(raw).astimezone(KST).strftime("%Y-%m-%d %H:%M")


def change_mails(h):
    return [mail for mail in h.sent if "변경 리포트" in mail[0]]


@pytest.fixture
def h(tmp_path):
    h = make(tmp_path)
    h.run(FRI, trigger="collect")
    h.issues = [issue_item("VP-100", "2026-10-17T00:00:00Z", ["VP-10"], review="", status="closed", title="저장 오류",
                           description="DESC-SECRET 바뀐 설명 문장", repro="REPRO-SECRET 재현 절차", assignees=(("u2", "이영희"),))]
    h.srs = [srs_item("VP-10", "01-01", "목록", text="SRS-SECRET 새 본문 문장입니다.")]
    h.run(MON, trigger="collect")
    return h


def test_monday_mail_covers_friday_to_monday(h):
    mails = change_mails(h)
    assert len(mails) == 2                                   # 금요일 첫 기록 메일 + 월요일 변경 메일
    subject, text, html, recipients = mails[-1]
    assert subject.startswith("[QA Agent] VXvue 변경 리포트 · 10-16(금)~10-19(월) · 이슈 1건 · SRS 1건")
    assert recipients == ("qa@example.com",)
    # 기간은 두 기록을 실제로 수집한 시각이다. 수집 시각은 벽시계라 날짜를 고정하지 않고 기록에서 읽는다.
    store = snapshots.for_settings(h.cfg)
    first, last = (kst_minute(store.meta(store.between(KIND_ISSUES, day, day)[0])["collected_at"])
                   for day in ("2026-10-16", "2026-10-19"))
    assert f"기간: {first} ~ {last} · 비교한 기록: 10-16, 10-19" in text
    assert "AI 분석: 켜짐 · 밀린 변경 0건" in text


def test_mail_has_values_but_no_bodies(h):
    _, text, html, _ = change_mails(h)[-1]
    for body in (text, html):
        assert "VP-100" in body and "저장 오류" in body
        assert "closed" in body and "open" in body
        assert "김철수" in body and "이영희" in body
        for secret in ("DESC-SECRET", "REPRO-SECRET", "SRS-SECRET"):
            assert secret not in body
        assert CLOSING in body
    assert "상태 open → closed" in text
    assert "10-19(월)" in text and "바뀐 곳: 본문(더해진 문장" in text
    assert "https://alm.example/polarion/redirect/project/VXvue/workitem?id=VP-100" in text
    assert "전체 보기: http://qa.example/qa-agent/changes?product=VXvue&since=2026-10-16&until=2026-10-19" in text


def test_comment_author_and_time_without_comment_body(tmp_path):
    cfg, store = setup(tmp_path)
    names = {"u2": "이영희"}
    comment = {"id": "c1", "author": "u2", "created": "2026-10-16T23:30:00Z", "text": "원인은 캐시 BODY-SECRET"}
    save(store, KIND_ISSUES, "2026-10-16", [issue("VP-1", user_names=names)])
    save(store, KIND_ISSUES, "2026-10-19", [issue("VP-1", user_names=names, comment_ids=["c1"], comments=[comment])])
    report = build_change_report(cfg, store, "2026-10-16", "2026-10-19", schedule=SCHEDULE)
    assert report.issues[0].lines[0].comment_text                      # 화면은 본문을 갖고 있다
    subject, text, html = change_mail.build_change_email(
        report, product="VXvue", page_url="http://x/c", ai_state={"label": "켜짐", "deferred": 0}, max_items=50)
    for body in (text, html):
        assert "BODY-SECRET" not in body
        assert "이영희" in body
    assert "10-17(토) 08:30  댓글 추가 · 이영희" in text


def test_same_day_rerun_sends_one_mail(tmp_path):
    h = make(tmp_path)
    h.run(FRI, trigger="collect")
    h.run(FRI.replace(hour=5), trigger="scheduled")
    assert len(change_mails(h)) == 1
    h.run(MON, trigger="collect")
    h.run(MON.replace(hour=5), trigger="scheduled")
    assert len(change_mails(h)) == 2


def test_collect_failure_mail(tmp_path):
    h = make(tmp_path)
    h.run(FRI, trigger="collect")

    class Down:
        def iter_workitems(self, *args, **kwargs):
            from app.modules.daily_qa.polarion import PolarionError
            raise PolarionError("Polarion 연결 실패")

    from app.modules.daily_qa.pipeline import run_daily
    outcome = run_daily(h.cfg, today=MON, polarion_factory=lambda: Down(), inputs=h.inputs, store=h.store, trigger="collect",
                        send_email=lambda *args: h.sent.append(args) or {"status": "sent"})
    subject, text, html, _ = change_mails(h)[-1]
    assert "수집 실패" in subject
    assert "기준을 유지했고 다음 수집에서 다시 비교합니다." in text and "기준을 유지했고 다음 수집에서 다시 비교합니다." in html
    assert "[이슈 변경]" not in text and "VP-100" not in text
    assert outcome["summary"]["change_mail"]["status"] == "sent"


def test_baseline_mail(tmp_path):
    h = make(tmp_path)
    outcome = h.run(FRI, trigger="collect")
    subject, text, html, _ = change_mails(h)[0]
    assert "첫 기록을 저장했습니다. 다음 업무일부터 변경을 보냅니다." in text
    assert "첫 기록을 저장했습니다. 다음 업무일부터 변경을 보냅니다." in html
    assert CLOSING in text
    assert outcome["summary"]["change_mail"]["status"] == "sent"


@pytest.mark.parametrize("send_when_empty,expected", [(True, 2), (False, 1)])
def test_empty_day_respects_send_when_empty(tmp_path, send_when_empty, expected):
    h = make(tmp_path, change_report=ChangeReportSettings(send_when_empty=send_when_empty))
    h.run(FRI, trigger="collect")
    outcome = h.run(MON, trigger="collect")
    mails = change_mails(h)
    assert len(mails) == expected
    if send_when_empty:
        assert "이슈 0건 · SRS 0건" in mails[-1][0] and "변경 없음" in mails[-1][1]
    else:
        assert outcome["summary"]["change_mail"]["status"] == "skipped"


def test_max_items_cuts_with_link(tmp_path):
    cfg, store = setup(tmp_path)
    save(store, KIND_ISSUES, "2026-10-16", [])
    save(store, KIND_ISSUES, "2026-10-19", [issue(f"VP-{n}") for n in range(1, 52)])
    report = build_change_report(cfg, store, "2026-10-16", "2026-10-19", schedule=SCHEDULE)
    subject, text, html = change_mail.build_change_email(
        report, product="VXvue", page_url="http://x/changes", ai_state={"label": "켜짐", "deferred": 0}, max_items=50)
    assert "이슈 51건" in subject
    assert "나머지 1건은 화면에서 보기: http://x/changes" in text
    assert "나머지 1건은" in html and html.count("VP-") >= 50


def test_disabled_setting_sends_nothing(tmp_path):
    h = make(tmp_path, change_report=ChangeReportSettings(enabled=False))
    outcome = h.run(FRI, trigger="collect")
    assert change_mails(h) == [] and "change_mail" not in outcome["summary"]


@pytest.mark.parametrize("trigger", ["manual", "manual_cli", "catchup", "prepare"])
def test_manual_and_catchup_runs_send_no_change_mail(tmp_path, trigger):
    h = make(tmp_path)
    h.run(FRI, trigger=trigger)
    assert change_mails(h) == []


def test_send_failure_does_not_change_run_status(tmp_path):
    h = make(tmp_path)
    from app.modules.daily_qa.pipeline import run_daily

    def boom(*args):
        raise RuntimeError("smtp down")

    outcome = run_daily(h.cfg, today=FRI, polarion_factory=lambda: __import__("tests.daily_qa_fixtures", fromlist=["FakePolarion"]).FakePolarion(h.srs, h.issues),
                        inputs=h.inputs, store=h.store, trigger="collect", send_email=boom)
    assert outcome["summary"]["change_mail"]["status"] == "failed"
    assert outcome["status"] != "FAILED"


def test_test_mail_kind_change_report(monkeypatch):
    sent = []
    monkeypatch.setattr(notifier, "send_report", lambda subject, text, html="", recipients=(): sent.append((subject, text, html)) or {"status": "sent"})
    assert notifier.send_test("change_report", ("a@example.com",))["status"] == "sent"
    subject, text, html = sent[0]
    assert "[시험]" in subject and "변경 리포트" in subject
    assert "예시 자료" in text and "예시 자료" in html
    assert "VP-<번호>" in text and CLOSING in text


def test_settings_defaults_and_config():
    from app.modules.daily_qa.settings import load

    cfg = load()
    assert cfg.change_report == ChangeReportSettings(enabled=True, send_when_empty=True, max_items=50)


@pytest.mark.parametrize("trigger", ["scheduled", "collect"])
def test_missed_schedule_run_later_in_day_sends_one_mail(tmp_path, trigger):
    """04:00 을 놓친 날의 만회는 launch_scheduled 가 scheduled/collect 로 띄운다. 그날 메일이 한 통 나간다."""
    h = make(tmp_path)
    h.run(FRI, trigger="collect")
    h.run(MON.replace(hour=8, minute=2), trigger=trigger)
    mails = change_mails(h)
    assert len(mails) == 2 and "10-16(금)~10-19(월)" in mails[-1][0]


def test_usage_limit_catchup_sends_no_change_mail(tmp_path):
    h = make(tmp_path)
    h.run(FRI, trigger="catchup")
    assert change_mails(h) == []


def test_skipped_collection_sends_failure_mail_not_baseline(tmp_path):
    h = make(tmp_path)
    from app.modules.daily_qa.pipeline import run_daily

    cfg = replace(h.cfg, polarion=replace(h.cfg.polarion, host="", token=""))      # Polarion 연결 없음 -> 수집 단계 skipped
    outcome = run_daily(cfg, today=FRI, inputs=h.inputs, store=h.store, trigger="collect",
                        send_email=lambda *args: h.sent.append(args) or {"status": "sent"})
    assert outcome["stages"]["collect_issues"]["status"] == "skipped"
    subject, text, html, _ = change_mails(h)[-1]
    assert "수집 실패" in subject
    assert "첫 기록을 저장했습니다" not in text and "첫 기록을 저장했습니다" not in html


def test_schedule_disabled_sends_no_auto_change_mail(tmp_path, monkeypatch):
    """`daily_qa.schedule.enabled: false` 면 CLI로 `--trigger scheduled`를 돌려도 자동 메일은 나가지 않는다."""
    from app.modules.daily_qa import scheduled_jobs

    monkeypatch.setattr(scheduled_jobs, "schedule_settings", lambda settings=None: {"enabled": False})
    h = make(tmp_path)
    outcome = h.run(FRI, trigger="scheduled")
    assert change_mails(h) == []
    assert outcome["summary"]["change_mail"]["status"] == "skipped"
    assert "꺼져" in outcome["summary"]["change_mail"]["reason"]


def test_no_email_run_is_skipped_and_does_not_block_real_mail(tmp_path):
    """`--no-email`(발송 끔) 실행은 실패가 아니라 건너뜀이며, 그날의 진짜 메일을 막지 않는다."""
    from app.modules.daily_qa.pipeline import run_daily
    from tests.daily_qa_fixtures import FakePolarion

    h = make(tmp_path)
    kwargs = dict(today=FRI, polarion_factory=lambda: FakePolarion(h.srs, h.issues), inputs=h.inputs, store=h.store, trigger="collect")
    outcome = run_daily(h.cfg, send_email=lambda *args: {"status": "disabled"}, **kwargs)
    assert outcome["summary"]["change_mail"]["status"] == "skipped"
    assert outcome["summary"]["change_mail"]["reason"] == "메일 끔"
    assert h.store.get_state(h.cfg.state_key("change_mail_date"), "") == ""
    kwargs["today"] = FRI + timedelta(minutes=5)
    run_daily(h.cfg, send_email=lambda *args: h.sent.append(args) or {"status": "sent"}, **kwargs)
    assert len(change_mails(h)) == 1


def _grouped_report(tmp_path):
    cfg, store = setup(tmp_path)
    first = [issue("VP-1", reporter_id="u2", reporter_name="이영희"), issue("VP-2", reporter_id="u1", reporter_name="김철수"),
             issue("VP-3"), issue("VP-4", reporter_id="u2", reporter_name="이영희")]
    save(store, KIND_ISSUES, "2026-10-16", first)
    save(store, KIND_ISSUES, "2026-10-19", [dict(item, status="B") for item in first])
    later = [dict(item, status="B") for item in first]
    later[0] = dict(later[0], status="C")                      # VP-1 만 하루 더 늦게 바뀐다
    save(store, KIND_ISSUES, "2026-10-20", later)
    return build_change_report(cfg, store, "2026-10-16", "2026-10-20", schedule=SCHEDULE)


# Validates: REQ-ALMCHG-004
def test_issues_grouped_by_reporter_name_unknown_last(tmp_path):
    report = _grouped_report(tmp_path)
    subject, text, html = change_mail.build_change_email(
        report, product="VXvue", page_url="http://x/c", ai_state={"label": "꺼짐", "deferred": 0}, max_items=50)
    groups = [line for line in text.splitlines() if line.startswith("■ ")]
    assert groups == ["■ 김철수 (이슈 1건)", "■ 이영희 (이슈 2건)", "■ 작성자 미확인 (이슈 1건)"]
    assert text.index("VP-1 VP-1 제목") < text.index("VP-4 VP-4 제목")   # 같은 작성자 안에서는 최근 변경 순
    assert html.index("김철수 (이슈 1건)") < html.index("이영희 (이슈 2건)") < html.index("작성자 미확인 (이슈 1건)")


# Validates: REQ-ALMCHG-004
def test_reporter_groups_respect_max_items(tmp_path):
    report = _grouped_report(tmp_path)
    subject, text, html = change_mail.build_change_email(
        report, product="VXvue", page_url="http://x/c", ai_state={"label": "꺼짐", "deferred": 0}, max_items=2)
    groups = [line for line in text.splitlines() if line.startswith("■ ")]
    assert sum(int(g.split("이슈 ")[1].split("건")[0]) for g in groups) == 2
    assert "나머지 2건은 화면에서 보기" in text
