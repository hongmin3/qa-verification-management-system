"""제품 상세 화면에서 요청이 서버에 닿지 못했을 때 알림이 보이는지 실제 브라우저로 확인한다.

Validates: REQ-KNOW-020, REQ-KNOW-021 (TEST-KNOW-010)

화면은 실제 라우터가 임시 DB·임시 설정으로 그린다. 브라우저는 가짜 주소(`http://kn.test`)로 그 화면을 받고,
서버로 가는 POST 는 브라우저 안에서 끊는다. 운영 호출은 없다.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_knowledge_manual_spec import _upload, _xlsx, env  # noqa: F401
from tests.test_qa_run_progress_browser import browser_headless

playwright = pytest.importorskip("playwright.sync_api")
ORIGIN = "http://kn.test"
SENT_FAIL = "파일을 서버로 보내지 못했습니다"
COLLECT_FAIL = "수집 요청을 서버로 보내지 못했습니다"


@pytest.fixture
def browser_page():
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=browser_headless())
        except Exception as exc:  # 브라우저가 설치되지 않은 PC
            pytest.skip(f"Chromium 을 띄울 수 없습니다: {exc}")
        page = browser.new_page()
        yield page
        browser.close()


def _serve(page, env, path: str, on_post) -> dict:
    """GET 은 실제 라우터 화면을 돌려주고, POST 는 `on_post(route)` 가 처리한다. 알림 문구를 모은다."""
    seen = {"alerts": [], "posts": []}

    def handle(route):
        request = route.request
        if request.method == "POST":
            seen["posts"].append(request.url)
            on_post(route)
            return
        response = env.client.get(request.url.replace(ORIGIN, ""))
        route.fulfill(status=response.status_code, body=response.content,
                      headers={"content-type": response.headers.get("content-type", "text/html")})

    page.route(f"{ORIGIN}/**", handle)

    def on_dialog(dialog):
        if dialog.type == "alert":
            seen["alerts"].append(dialog.message)
        dialog.accept()

    page.on("dialog", on_dialog)
    page.goto(f"{ORIGIN}{path}")
    return seen


def _file(tmp_path: Path) -> str:
    path = tmp_path / "pick.xlsx"
    path.write_bytes(_xlsx("새 사양"))
    return str(path)


def _wait_alert(page, seen):
    page.wait_for_function("() => true")  # 이벤트 처리 한 바퀴
    for _ in range(50):
        if seen["alerts"]:
            return
        page.wait_for_timeout(100)


def test_replace_that_never_reaches_the_server_shows_an_alert(env, browser_page, tmp_path):
    _upload(env, "auto-spec", "(사양서) VSS Specification (261006).xlsx", _xlsx("옛 사양"))
    page = browser_page
    seen = _serve(page, env, "/knowledge/products/auto-spec", lambda route: route.abort("failed"))
    replace_input = page.locator("form.kn-upload-form:has(input[name=replace_id]) input[type=file]").first
    replace_input.set_input_files(_file(tmp_path))
    _wait_alert(page, seen)

    assert seen["posts"], "교체 요청을 보내려고 하지 않았다"
    assert any(SENT_FAIL in message and "기존 문서는 그대로" in message for message in seen["alerts"]), seen["alerts"]
    assert page.url.endswith("/knowledge/products/auto-spec")
    assert replace_input.evaluate("input => input.files.length") == 0


def test_new_document_that_never_reaches_the_server_shows_an_alert(env, browser_page, tmp_path):
    page = browser_page
    seen = _serve(page, env, "/knowledge/products/auto-spec", lambda route: route.abort("failed"))
    page.locator("form.kn-upload-form:not(:has(input[name=replace_id])) input[type=file]").first.set_input_files(_file(tmp_path))
    _wait_alert(page, seen)

    assert any(SENT_FAIL in message for message in seen["alerts"]), seen["alerts"]


def test_server_refusal_still_shows_the_server_reason(env, browser_page, tmp_path):
    page = browser_page
    body = json.dumps({"detail": "등록할 수 없는 종류입니다: x"}, ensure_ascii=False)
    seen = _serve(page, env, "/knowledge/products/auto-spec",
                  lambda route: route.fulfill(status=400, body=body, headers={"content-type": "application/json"}))
    page.locator("form.kn-upload-form:not(:has(input[name=replace_id])) input[type=file]").first.set_input_files(_file(tmp_path))
    _wait_alert(page, seen)

    assert seen["alerts"] == ["등록할 수 없는 종류입니다: x"]


def test_collect_request_that_never_reaches_the_server_shows_an_alert(env, browser_page):
    page = browser_page
    seen = _serve(page, env, "/knowledge/products/vxvue", lambda route: route.abort("failed"))
    button = page.locator("form.knowledge-sync-form button")
    button.click()
    _wait_alert(page, seen)

    assert any(COLLECT_FAIL in message for message in seen["alerts"]), seen["alerts"]
    assert button.text_content().strip() == "지금 수집"
    assert button.is_enabled()
