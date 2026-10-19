"""운영 호출 없는 임시 HTTP 화면 시험. Validates: REQ-QAINTEL-021, REQ-QAINTEL-036."""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from tests.test_qa_intel_dashboard import world  # noqa: F401
from app.modules.qa_agent import dashboard as dash

playwright = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "browser-cancel-fixture"


def browser_headless():
    """기본은 화면 없이 띄운다. 화면을 보려면 QA_BROWSER_HEADED=1. Validates: NFR-OPS-003.

    Windows 에서 화면 있는 Chromium 은 백그라운드 셸·deploy.ps1 의 pytest 에서 멈출 수 있다.
    """
    return os.environ.get("QA_BROWSER_HEADED", "").strip() not in ("1", "true", "yes")


@pytest.mark.parametrize("value, headless", [
    (None, True), ("", True), ("0", True), ("1", False), ("true", False), ("yes", False),
])
def test_browser_headless_unless_asked(monkeypatch, value, headless):
    """Validates: NFR-OPS-003. CI·DISPLAY 가 없어도 기본은 화면 없이 띄운다."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    if value is None:
        monkeypatch.delenv("QA_BROWSER_HEADED", raising=False)
    else:
        monkeypatch.setenv("QA_BROWSER_HEADED", value)
    assert browser_headless() is headless


@pytest.fixture
def cancel_site(world, monkeypatch):
    """실제 템플릿·JS를 읽고 취소 HTTP 응답만 대신한다. 분석 프로세스는 없다."""
    store = world["store"]
    store.create_run(RUN_ID, False, world["cfg"].slug)
    store.add_finding(RUN_ID, "qa-trace-gap", "saved-before-cancel",
                      {"subject": "SAVED-RESULT", "verdict": "TC 없음", "summary": "중단 전에 저장한 결과"}, product="vxvue")
    state = {"running": True, "current_run_id": RUN_ID, "cancel_requested": False,
             "last_run": {"id": RUN_ID, "status": "RUNNING", "status_label": "실행 중", "started": "시작"},
             "progress": {"phase": "ai", "label": "AI 분석", "done": 4, "total": 18, "elapsed_seconds": 61},
             "next_run": "예약 꺼짐", "claude_limit": None}
    requests = []
    monkeypatch.setattr(dash, "status", lambda *_: state)
    finding = store.list_findings(run_id=world["changed"]["run_id"], analysis_type="SPEC_COVERAGE")[0]
    draft_finding = store.list_findings(run_id=world["changed"]["run_id"], analysis_type="FIXED_ISSUE")[0]
    pages = {path: world["client"].get(path).content for path in
             ("/qa-agent", f"/qa-agent/findings/{finding['id']}", f"/qa-agent/findings/{draft_finding['id']}")}
    cached_run = "browser-cache-fixture"
    origin = {"run_id": finding["run_id"], "task_id": "SPEC-001", "finding_ids": [finding["id"]]}
    store.create_run(cached_run, False, "vxvue")
    cached_id = store.add_finding(cached_run, "qa-spec-change-analysis", "cached-result", {
        "subject": "CACHED-RESULT", "verdict": "검토 필요", "summary": "동일한 자료의 성공 결과 재사용",
        "sections": {"result_cache": origin}, "analysis_type": "SPEC_COVERAGE"}, product="vxvue")
    store.finish_run(cached_run, "SUCCESS", {"SPEC_COVERAGE": {"status": "ok", "counts": {"tasks": 1, "done": 1}}},
                     {"claude_calls": 0, "result_cache": {"hits": 1, "reused_findings": 1, "sources": [origin]}})
    for path in (f"/qa-agent/runs/{cached_run}", f"/qa-agent/findings/{cached_id}"):
        response = world["client"].get(path)
        assert response.status_code == 200
        pages[path] = response.content

    def finish():
        store.finish_run(RUN_ID, "CANCELLED", {}, {"findings": 1})
        pages[f"/qa-agent/runs/{RUN_ID}"] = world["client"].get(f"/qa-agent/runs/{RUN_ID}").content
        state.update(running=False, current_run_id=None, cancel_requested=False,
                     last_run={**state["last_run"], "status": "CANCELLED", "status_label": "사용자 중단",
                               "result": "사용자 중단 · 저장한 결과 1건"},
                     progress={**state["progress"], "phase": "cancelled", "label": "사용자 중단", "elapsed_seconds": 70})

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *_):
            pass

        def send(self, body, status=200, content_type="application/json"):
            content = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/qa-agent/status":
                self.send(state)
            elif path == "/qa-agent/run-preview":
                response = world["client"].get(self.path)
                self.send(response.content, response.status_code)
            elif path == "/qa-agent":
                self.send(world["client"].get("/qa-agent").content, content_type="text/html; charset=utf-8")
            elif path.startswith("/static/"):
                asset = ROOT / "app/web" / path.lstrip("/")
                if asset.is_file():
                    self.send(asset.read_bytes(), content_type="text/javascript" if asset.suffix == ".js" else "text/css")
                else:
                    self.send({}, 404)
            elif path in pages:
                self.send(pages[path], content_type="text/html; charset=utf-8")
            else:
                # 사용량 등 부가 조회를 실제 인증·외부 서비스에 연결하지 않는다.
                self.send({})

        def do_POST(self):
            url = urlparse(self.path)
            if url.path == "/qa-agent/automation-policy":
                content = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                response = world["client"].post(url.path, content=content,
                    headers={"Content-Type": self.headers.get("Content-Type", "")}, follow_redirects=False)
                self.send_response(response.status_code)
                self.send_header("Location", response.headers["location"])
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            requests.append(self.path)
            assert url.path == f"/qa-agent/runs/{RUN_ID}/cancel"
            assert parse_qs(url.query)["product"][0].lower() == "vxvue"
            state["cancel_requested"] = True
            state["progress"] = {**state["progress"], "cancel_requested": True, "label": "중단 요청 확인 중"}
            self.send({"status": "cancelling", "run_id": RUN_ID, "product": "vxvue"}, 202)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {"base": f"http://127.0.0.1:{server.server_port}", "requests": requests,
           "finding": finding["id"], "draft_finding": draft_finding["id"], "store": store, "finish": finish,
           "cached_run": cached_run, "cached_finding": cached_id, "origin": origin}
    server.shutdown()
    server.server_close()


@pytest.mark.parametrize("width", [1366, 1920])
@pytest.mark.parametrize("screen", ["dashboard", "tc_check", "tc_draft"])
def test_cancel_in_real_http_browser(cancel_site, width, screen, tmp_path):
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=browser_headless())
        context = browser.new_context(viewport={"width": width, "height": 1000})
        page = context.new_page()
        finding_id = cancel_site["draft_finding"] if screen == "tc_draft" else cancel_site["finding"]
        path = "/qa-agent" if screen == "dashboard" else f"/qa-agent/findings/{finding_id}"
        response = page.goto(cancel_site["base"] + path)
        assert response.status == 200
        stop = page.locator("#qa-cancel")
        playwright.expect(stop).to_be_visible()
        playwright.expect(stop).to_have_text("이번 점검 중단")
        playwright.expect(stop).to_be_enabled()
        playwright.expect(page.locator("#qa-progress-step")).to_contain_text("4/18")
        stop.click()
        playwright.expect(stop).to_be_disabled()
        playwright.expect(page.locator("#qa-progress-step")).to_contain_text("중단")
        other = context.new_page()
        other.goto(cancel_site["base"] + path)
        playwright.expect(other.locator("#qa-cancel")).to_be_disabled()
        playwright.expect(other.locator("#qa-progress-step")).to_contain_text("중단")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.bring_to_front()
        page.screenshot(path=str(tmp_path / f"cancelling-{screen}-{width}.png"), full_page=True)
        cancel_site["finish"]()
        for tab in (page, other):
            tab.bring_to_front()
            playwright.expect(tab.locator("#qa-spinner")).to_be_hidden(timeout=15000)
            playwright.expect(tab.locator("#qa-progress-step")).to_contain_text("사용자 중단")
            playwright.expect(tab.locator("#qa-progress-link")).to_have_attribute("href", f"/qa-agent/runs/{RUN_ID}")
            for button in tab.locator("#run-now,#audit-now,button.on-demand").all():
                playwright.expect(button).to_be_enabled()
            if screen == "dashboard":
                playwright.expect(tab.locator("#run-now")).to_have_text("변경사항 점검하기")
            assert tab.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert len(cancel_site["requests"]) == 1
        assert len(cancel_site["store"].list_findings(run_id=RUN_ID)) == 1
        page.bring_to_front()
        page.locator("#qa-progress-link").click()
        playwright.expect(page.get_by_text("사용자 요청으로 이번 점검을 중단했습니다.", exact=False)).to_be_visible()
        page.screenshot(path=str(tmp_path / f"cancel-{screen}-{width}.png"), full_page=True)
        context.close()
        browser.close()


@pytest.mark.parametrize("width", [1366, 1920])
def test_automation_checkboxes_in_http_browser(cancel_site, width):
    """Validates: REQ-QAINTEL-038. 두 체크박스의 세 모드 저장·공유·PC 폭(1366px·1920px) 확인."""
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=browser_headless())
        context = browser.new_context(viewport={"width": width, "height": 1000})
        page = context.new_page()
        page.goto(cancel_site["base"] + "/qa-agent")
        form = page.locator("#qa-automation-policy")
        enabled = page.locator("#qa-auto-enabled")
        period = page.locator("#qa-validation-period")
        playwright.expect(enabled).to_be_checked()
        playwright.expect(period).to_be_checked()
        period.uncheck()
        form.get_by_role("button", name="저장", exact=True).click()
        playwright.expect(form.locator(".pill")).to_have_text("차수 대기")
        other = context.new_page()
        other.goto(cancel_site["base"] + "/qa-agent")
        playwright.expect(other.locator("#qa-validation-period")).not_to_be_checked()
        enabled.uncheck()
        playwright.expect(period).to_be_disabled()
        form.get_by_role("button", name="저장", exact=True).click()
        playwright.expect(form.locator(".pill")).to_have_text("검증 종료")
        enabled.check()
        playwright.expect(period).to_be_enabled()
        period.check()
        form.get_by_role("button", name="저장", exact=True).click()
        playwright.expect(form.locator(".pill")).to_have_text("검증 기간")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert not cancel_site["requests"]
        context.close()
        browser.close()


@pytest.mark.parametrize("width", [1366, 1920])
def test_cache_in_real_http_browser(cancel_site, width):
    """Validates: REQ-QAINTEL-037. 실제 렌더링에서 새 분석 0·재사용 1·원본 링크를 구분한다."""
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=browser_headless())
        page = browser.new_page(viewport={"width": width, "height": 1000})
        page.goto(f"{cancel_site['base']}/qa-agent/runs/{cancel_site['cached_run']}")
        tile = page.locator("#run-key-counts .rv-tile").filter(has_text="AI가 분석한 것")
        playwright.expect(tile.locator("b")).to_have_text("0")
        playwright.expect(tile).to_contain_text("재사용 1묶음")
        cache = page.locator("#run-cache-summary")
        playwright.expect(cache).to_contain_text("결과 1건을 재사용")
        cache.locator("summary").click()
        origin_url = f"/qa-agent/runs/{cancel_site['origin']['run_id']}"
        playwright.expect(cache.locator("a")).to_have_attribute("href", origin_url)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.goto(f"{cancel_site['base']}/qa-agent/findings/{cancel_site['cached_finding']}")
        notice = page.locator(".qa-banner").filter(has_text="기존 분석 결과를 재사용")
        playwright.expect(notice.locator("a")).to_have_attribute("href", origin_url)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert not cancel_site["requests"]
        browser.close()
