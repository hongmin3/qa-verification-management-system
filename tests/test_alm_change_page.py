"""변경 리포트 화면 `/qa-agent/changes` (TEST-ALMCHG-003).

저장된 스냅샷만 읽는다. Polarion·Claude·메일은 부르지 않는다.

Validates: REQ-ALMCHG-003, REQ-QAINTEL-027
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.daily_qa import snapshots as snapshot_module
from app.modules.daily_qa.snapshots import KIND_ISSUES, KIND_SRS
from app.modules.daily_qa.store import DailyQaStore
from app.modules.qa_agent import dashboard as dash
from app.modules.qa_agent import router as router_module
from tests.daily_qa_fixtures import make_settings
from tests.test_alm_change_report import issue, srs

D1, D2, D3 = "2026-10-14", "2026-10-15", "2026-10-16"


def _save(store, kind, day, items):
    store.save(kind, day, items, collected_at=f"{day}T04:00:00+09:00")


@pytest.fixture
def world(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "data").mkdir(parents=True)
    cfg = make_settings(root, tmp_path / "ws")
    snaps = snapshot_module.for_settings(cfg)
    names = {"u1": "김철수", "u2": "이영희"}
    comment = {"id": "c1", "author": "u2", "created": "2026-10-15T23:30:00Z", "text": "원인은 캐시 문제입니다 BODY-SECRET"}
    _save(snaps, KIND_ISSUES, D1, [issue("VP-1", status="open", assignees=["u1"], user_names=names), issue("VP-2")])
    _save(snaps, KIND_ISSUES, D2, [issue("VP-1", status="closed", assignees=["u2"], user_names=names), issue("VP-2")])
    _save(snaps, KIND_ISSUES, D3, [issue("VP-1", status="closed", assignees=["u2"], user_names=names,
                                         comment_ids=["c1"], comments=[comment]), issue("VP-2"), issue("VP-3")])
    _save(snaps, KIND_SRS, D1, [srs("VP-9")])
    _save(snaps, KIND_SRS, D2, [srs("VP-9"), srs("VP-8")])
    _save(snaps, KIND_SRS, D3, [srs("VP-9", text="본문 하나. 새 문장."), srs("VP-8")])
    store = DailyQaStore(cfg.db_path)
    choice = dash.ProductChoice(cfg, store, [cfg.product])
    monkeypatch.setattr(dash, "choose", lambda product="": choice if product in ("", cfg.product, cfg.slug) else None)
    app = FastAPI()
    app.include_router(router_module.router, prefix="/qa-agent")
    return {"cfg": cfg, "store": store, "client": TestClient(app)}


# Validates: REQ-ALMCHG-003
def test_default_period_is_last_two_records(world):
    page = world["client"].get("/qa-agent/changes").text
    assert f'name="since" value="{D2}"' in page and f'name="until" value="{D3}"' in page


# Validates: REQ-ALMCHG-003
def test_issue_and_srs_tabs_render_lines(world):
    client = world["client"]
    issues = client.get(f"/qa-agent/changes?since={D1}&until={D3}").text
    assert "상태 변경" in issues and "open" in issues and "closed" in issues
    assert "https://alm.example/polarion/redirect/project/VXvue/workitem?id=VP-1" in issues
    srs_page = client.get(f"/qa-agent/changes?since={D1}&until={D3}&tab=srs").text
    assert "SRS 신규" in srs_page and "VP-8" in srs_page and "SRS 수정" in srs_page


# Validates: REQ-ALMCHG-003
def test_comment_text_only_on_page(world):
    page = world["client"].get(f"/qa-agent/changes?since={D2}&until={D3}").text
    start = page.index("<details")
    assert "BODY-SECRET" in page and page.index("BODY-SECRET") > start
    assert "이영희" in page


# Validates: REQ-ALMCHG-003
def test_kind_filter_and_search(world):
    client = world["client"]
    only = client.get(f"/qa-agent/changes?since={D1}&until={D3}&kind=assignee").text
    assert "담당자 변경" in only and "김철수" in only and "상태 변경</span>" not in only
    one = client.get(f"/qa-agent/changes?since={D1}&until={D3}&q=VP-3").text
    assert "VP-3" in one and "VP-1</a>" not in one


# Validates: REQ-ALMCHG-003
@pytest.mark.parametrize("params,message", [
    ("since=2026/10/01&until=2026-10-16", "날짜는 YYYY-MM-DD 로 입력하세요."),
    ("since=2026-10-16&until=2026-10-01", "시작일이 종료일보다 늦습니다."),
    ("since=2025-01-01&until=2026-10-16", "조회 기간은 366일까지입니다."),
])
def test_bad_dates_return_400_with_message(world, params, message):
    response = world["client"].get(f"/qa-agent/changes?{params}")
    assert response.status_code == 400 and message in response.text


# Validates: REQ-ALMCHG-003
def test_empty_reason_and_notices_shown(world):
    page = world["client"].get("/qa-agent/changes?since=2020-01-01&until=2020-01-02").text
    assert "이 기간에 저장된 기록이 없습니다." in page
    page = world["client"].get(f"/qa-agent/changes?since=2026-10-10&until={D3}").text
    assert "2026-10-14 전의 변경은 확인할 기록이 없습니다." in page


# Validates: REQ-ALMCHG-003, REQ-QAINTEL-027
def test_dashboard_and_period_run_link(world):
    client, store, cfg = world["client"], world["store"], world["cfg"]
    assert "/qa-agent/changes?product=" in client.get("/qa-agent").text
    store.create_run("20261016-040000-vxvue", False, cfg.slug)
    store.finish_run("20261016-040000-vxvue", "SUCCESS", {}, {"period": {"since": D1, "until": D3}})
    store.create_run("20261016-050000-vxvue", False, cfg.slug)
    store.finish_run("20261016-050000-vxvue", "SUCCESS", {}, {})
    page = client.get("/qa-agent/runs/20261016-040000-vxvue").text
    assert f"/qa-agent/changes?product={cfg.slug}&amp;since={D1}&amp;until={D3}" in page
    assert "/qa-agent/changes" not in client.get("/qa-agent/runs/20261016-050000-vxvue").text


def _recipients(monkeypatch, value):
    from app.modules.daily_qa import settings as settings_module

    monkeypatch.setattr(settings_module, "report_recipients", lambda settings=None: value)


# Validates: REQ-ALMCHG-004
def test_mail_button_sends_one_mail(world, monkeypatch):
    from app.core import notifier

    _recipients(monkeypatch, ("a@example.com", "b@example.com"))
    sent = []
    monkeypatch.setattr(notifier, "send_report", lambda subject, text, html="", recipients=(): sent.append((subject, text, html, recipients)) or {"status": "sent"})
    client, store, cfg = world["client"], world["store"], world["cfg"]
    page = client.get(f"/qa-agent/changes?since={D2}&until={D3}").text
    assert "이 기간 리포트 메일 보내기" in page and "disabled" not in page.split("change-mail-button")[1].split(">")[0]
    assert "수신자 2명" in page
    response = client.post("/qa-agent/changes/mail", data={"product": cfg.product, "since": D2, "until": D3})
    assert response.status_code == 200 and response.json()["status"] == "sent"
    assert len(sent) == 1
    subject, text, html, recipients = sent[0]
    assert "변경 리포트 · 10-15(목)~10-16(금)" in subject and recipients == ("a@example.com", "b@example.com")
    assert "BODY-SECRET" not in text and "BODY-SECRET" not in html and "이영희" in text
    assert store.get_state(cfg.state_key("change_mail_date"), "") == ""        # 자동 메일의 하루 한 통 기록은 그대로


# Validates: REQ-ALMCHG-004
def test_mail_button_without_recipients(world, monkeypatch):
    from app.core import notifier

    _recipients(monkeypatch, ())
    monkeypatch.setattr(notifier, "send_report", lambda *args, **kwargs: pytest.fail("메일을 보내면 안 된다"))
    client, cfg = world["client"], world["cfg"]
    page = client.get(f"/qa-agent/changes?since={D2}&until={D3}").text
    assert "메일 수신자가 설정되지 않았습니다." in page
    assert "disabled" in page.split("change-mail-button")[1].split(">")[0]
    response = client.post("/qa-agent/changes/mail", data={"product": cfg.product, "since": D2, "until": D3})
    assert response.status_code == 409 and "메일 수신자가 설정되지 않았습니다." in response.text


# Validates: REQ-ALMCHG-004
def test_mail_button_rejects_bad_period(world, monkeypatch):
    _recipients(monkeypatch, ("a@example.com",))
    response = world["client"].post("/qa-agent/changes/mail", data={"product": world["cfg"].product, "since": D3, "until": D1})
    assert response.status_code == 400 and "시작일이 종료일보다 늦습니다." in response.text


# Validates: REQ-ALMCHG-004
def test_mail_button_returns_json_when_sending_raises(world, monkeypatch):
    from app.core import notifier

    _recipients(monkeypatch, ("a@example.com",))

    def boom(*args, **kwargs):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(notifier, "send_report", boom)
    client, cfg = world["client"], world["cfg"]
    response = client.post("/qa-agent/changes/mail", data={"product": cfg.product, "since": D2, "until": D3})
    assert response.status_code == 500
    body = response.json()
    assert body["status"] == "failed" and "RuntimeError" in body["detail"]
