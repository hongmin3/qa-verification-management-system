"""실제 SPA의 공유 URL·뒤로가기·표시 상한 검사. DB와 운영 API는 사용하지 않는다.

Validates: REQ-HUB-008.
Run: py -m pytest services/qa-manual-hub/frontend/tests --confcutdir=services/qa-manual-hub/frontend/tests
"""
import json
import os
import socket
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen

import pytest

pw = pytest.importorskip("playwright.sync_api")
FRONTEND = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def site():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen(
        ["node", "node_modules/vite/bin/vite.js", "--host", "127.0.0.1", "--port", str(port), "--strictPort"],
        cwd=FRONTEND, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            if process.poll() is not None:
                pytest.fail("Vite exited before serving the test SPA")
            try:
                with urlopen(url, timeout=1):
                    break
            except OSError:
                time.sleep(.1)
        else:
            pytest.fail("Vite did not start")
        yield url
    finally:
        process.terminate()
        process.wait(timeout=10)


@pytest.fixture
def screen(site):
    with pw.sync_playwright() as engine:
        cdp = os.environ.get("QA_MANUAL_HUB_BROWSER_CDP")
        browser = engine.chromium.connect_over_cdp(cdp, timeout=15000) if cdp else engine.chromium.launch(headless=True, timeout=15000)
        context = browser.new_context()
        page = context.new_page()
        requests = []
        hits = [dict(document_id=str(i), product_name="Product", document_name=f"Manual {i:03}",
                     category_name="Guide", document_status="active", version_status="active",
                     version_id=None) for i in range(200)]

        def respond(route):
            path = route.request.url.split("/api/", 1)[1].split("?", 1)[0]
            requests.append(route.request.url)
            payload = []
            if path == "auth/me":
                payload = dict(id="user", display_name="Tester", is_admin=False)
            elif path == "products":
                payload = [dict(id="p1", name="Product", is_active=True)]
            elif path == "categories":
                payload = [dict(id="c1", name="Guide")]
            elif path == "search":
                payload = hits
            elif path == "documents":
                payload = [dict(id=str(i), product_id="p1", product_name="Product", name=f"Manual {i}",
                                category_name="Guide", category_sort_order=0, status="active",
                                version_count=0) for i in range(2)]
            route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

        page.route("**/api/**", respond)
        yield page, requests, site
        context.close()
        browser.close()


def test_search_restores_all_filters_sort_and_view_and_discloses_limit(screen):
    page, requests, site = screen
    query = ("q=한글&product_id=p1&category_id=c1&document_name=Guide&document_number=DOC-1"
             "&revision=R2&version=V3&language=ko&file_name=manual.pdf&uploaded_by=Tester"
             "&revision_date_from=2026-01-01&revision_date_to=2026-02-01"
             "&upload_date_from=2026-03-01&upload_date_to=2026-04-01"
             "&document_status=archived&version_status=active&current_only=true"
             "&sort=document&dir=desc&view=advanced")
    page.goto(f"{site}/search?{query}")
    pw.expect(page.get_by_role("searchbox")).to_have_value("한글")
    pw.expect(page.get_by_label("Document Number", exact=True)).to_have_value("DOC-1")
    pw.expect(page.get_by_label("Current 버전만 검색")).to_be_checked()
    pw.expect(page.locator('th[aria-sort="descending"]')).to_contain_text("Document")
    pw.expect(page.locator("tbody tr").first).to_contain_text("Manual 199")
    pw.expect(page.get_by_text("최대 200건까지 표시합니다.", exact=False)).to_be_visible()
    search_url = next(url for url in requests if "/api/search?" in url)
    from urllib.parse import parse_qs, urlparse
    values = parse_qs(urlparse(search_url).query)
    expected = parse_qs(query)
    for key, value in expected.items():
        if key not in {"sort", "dir", "view"}:
            assert values[key] == value
    assert not {"sort", "dir", "view"}.intersection(values)
    page.reload()
    pw.expect(page.get_by_label("Document Number", exact=True)).to_have_value("DOC-1")
    page.get_by_role("button", name="상세 조건 접기").click()
    assert "view=basic" in page.url
    page.go_back()
    pw.expect(page.get_by_label("Document Number", exact=True)).to_be_visible()
    page.go_forward()
    pw.expect(page.get_by_label("Document Number", exact=True)).to_have_count(0)


def test_search_submit_reset_and_history_restore_applied_filters(screen):
    page, _, site = screen
    page.goto(f"{site}/search")
    pw.expect(page.locator("tbody tr")).to_have_count(200)
    page.get_by_role("searchbox").fill("첫 검색")
    page.get_by_role("button", name="검색", exact=True).click()
    pw.expect(page).to_have_url(__import__("re").compile("q="))
    first_url = page.url
    page.get_by_role("searchbox").fill("두 번째")
    page.get_by_role("button", name="검색", exact=True).click()
    pw.expect(page).not_to_have_url(first_url)
    page.go_back()
    pw.expect(page.get_by_role("searchbox")).to_have_value("첫 검색")
    page.go_forward()
    pw.expect(page.get_by_role("searchbox")).to_have_value("두 번째")
    page.get_by_role("button", name="초기화", exact=True).click()
    pw.expect(page.get_by_role("searchbox")).to_have_value("")
    assert "q=" not in page.url


def test_documents_restores_filters_and_sort_on_reload_and_history(screen):
    page, requests, site = screen
    page.goto(f"{site}/documents?product_id=p1&category_id=c1&status=archived&q=Guide&sort=name&dir=desc")
    pw.expect(page.get_by_label("Product", exact=True)).to_have_value("p1")
    pw.expect(page.get_by_label("Category", exact=True)).to_have_value("c1")
    pw.expect(page.get_by_label("Status", exact=True)).to_have_value("archived")
    pw.expect(page.get_by_role("searchbox")).to_have_value("Guide")
    pw.expect(page.locator('th[aria-sort="descending"]')).to_contain_text("Document")
    assert any("product_id=p1" in url and "/api/documents?" in url for url in requests)
    page.reload()
    pw.expect(page.get_by_role("searchbox")).to_have_value("Guide")
    page.get_by_label("Status", exact=True).select_option("all")
    pw.expect(page).to_have_url(__import__("re").compile("status=all"))
    page.go_back()
    pw.expect(page.get_by_label("Status", exact=True)).to_have_value("archived")
    page.go_forward()
    pw.expect(page.get_by_label("Status", exact=True)).to_have_value("all")
    page.get_by_role("button", name="Document", exact=True).click()
    assert "sort=name" in page.url
    assert "dir=desc" not in page.url
    page.get_by_role("button", name="초기화", exact=True).click()
    pw.expect(page.get_by_label("Product", exact=True)).to_have_value("")
    pw.expect(page.get_by_label("Status", exact=True)).to_have_value("active")
