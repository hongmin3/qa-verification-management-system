"""`상세 분석` 화면과 점검 화면 안내를 실제 브라우저로 확인한다. Validates: REQ-QAINTEL-042, NFR-OPS-003.

운영 호출은 없다. 임시 HTTP 서버가 실제 템플릿·JS를 내어 주고, 준비 목록은 임시 SQLite에서 읽는다.
"""
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest

from tests.test_qa_intel_dashboard import _save_plan, world  # noqa: F401
from tests.test_qa_run_progress_browser import browser_headless

playwright = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[1]
IDLE = {"running": False, "current_run_id": None, "cancel_requested": False, "last_run": None, "progress": None,
        "next_run": "예약 꺼짐", "claude_limit": None}


@pytest.fixture
def select_site(world):
    """실제 라우터가 만든 화면·목록 API를 그대로 내보낸다. 실행 요청은 받지 않고 기록만 한다."""
    posts = []

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
                self.send(IDLE)
            elif path in ("/qa-agent", "/qa-agent/select"):
                self.send(world["client"].get(self.path).content, content_type="text/html; charset=utf-8")
            elif path == "/qa-agent/plans/latest":
                self.send(world["client"].get(self.path).content)
            elif path.startswith("/static/"):
                asset = ROOT / "app/web" / path.lstrip("/")
                if asset.is_file():
                    self.send(asset.read_bytes(), content_type="text/javascript" if asset.suffix == ".js" else "text/css")
                else:
                    self.send({}, 404)
            else:
                self.send({})

        def do_POST(self):
            posts.append(self.path)
            self.send({"detail": "시험 서버는 실행하지 않습니다."}, 409)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield {"base": f"http://127.0.0.1:{server.server_port}", "posts": posts, "world": world}
    server.shutdown()
    server.server_close()


@pytest.mark.parametrize("width", [1366, 1920])
def test_select_page_row_is_one_click_area_with_fields(select_site, width):
    _save_plan(select_site["world"], created=datetime.now(timezone.utc))
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=browser_headless())
        page = browser.new_page(viewport={"width": width, "height": 900})
        page.goto(select_site["base"] + "/qa-agent/select?product=VXvue")
        playwright.expect(page.get_by_role("heading", name="상세 분석")).to_be_visible()
        boxes = page.locator("#qa-selection-targets input[type=checkbox]")
        playwright.expect(boxes).to_have_count(1)
        row = page.locator("#qa-selection-targets .qa-selection-row")
        for text in ("VP-100", "목록 오류", "수정 완료 이슈", "홍길동", "2026-09-29 18:00 (한국 시간)", "연구소 결과 변경"):
            playwright.expect(row).to_contain_text(text)
        # 체크박스는 행의 왼쪽 끝에 있고, 행의 글자를 눌러도 선택된다.
        box, title = boxes.bounding_box(), row.locator("b").bounding_box()
        assert box["x"] < title["x"]
        run, review = page.locator("#qa-selected-run"), page.locator("#qa-selected-review")
        playwright.expect(run).to_be_disabled()
        row.locator("b").click()
        playwright.expect(boxes).to_be_checked()
        playwright.expect(run).to_be_enabled()
        playwright.expect(review).to_be_enabled()
        boxes.uncheck()
        playwright.expect(run).to_be_disabled()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert not select_site["posts"]
        browser.close()


@pytest.mark.parametrize("width", [1366, 1920])
def test_select_page_expired_plan_has_no_checkbox_and_disabled_run(select_site, width):
    _save_plan(select_site["world"], created=datetime.now(timezone.utc) - timedelta(minutes=31))
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=browser_headless())
        page = browser.new_page(viewport={"width": width, "height": 900})
        page.goto(select_site["base"] + "/qa-agent/select?product=VXvue")
        playwright.expect(page.locator("#qa-selection-status")).to_contain_text("준비 목록 만료 (준비 ")
        playwright.expect(page.locator("#qa-selection-status")).to_contain_text("다시 준비하세요")
        playwright.expect(page.locator("#qa-selection-targets input[type=checkbox]")).to_have_count(0)
        playwright.expect(page.locator("#qa-selected-run")).to_be_disabled()
        playwright.expect(page.locator("#qa-selected-review")).to_be_disabled()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        browser.close()


@pytest.mark.parametrize("width", [1366, 1920])
def test_dashboard_shows_only_notice_and_link(select_site, width):
    _save_plan(select_site["world"], created=datetime.now(timezone.utc))
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=browser_headless())
        page = browser.new_page(viewport={"width": width, "height": 900})
        page.goto(select_site["base"] + "/qa-agent?product=VXvue")
        notice = page.locator("#qa-prepared-notice")
        playwright.expect(notice).to_have_text("준비된 대상 1건 · 상세 분석 열기")
        playwright.expect(page.locator("#qa-selection-targets")).to_have_count(0)
        playwright.expect(notice.locator("a")).to_have_attribute("href", "/qa-agent/select?product=VXvue")
        notice.locator("a").click()
        playwright.expect(page.get_by_role("heading", name="상세 분석")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        browser.close()
