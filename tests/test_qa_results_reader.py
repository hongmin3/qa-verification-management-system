"""Validates: REQ-QAINTEL-046..047. HTTP filters and processing history."""
from tests.test_qa_intel_dashboard import world


def test_search_creator_state_and_processing_http(world):
    store, client = world['store'], world['client']
    finding = store.list_findings(analysis_type='NEW_ISSUE')[0]
    store.storage.qa_save_reporters('vxvue', [{'id': finding['subject'], 'reporter_id': 'creator', 'reporter_name': '최초 작성자'}])
    response = client.get('/qa-agent/results', params={'category': 'issues', 'reporter': 'creator', 'subject': finding['subject']})
    assert response.status_code == 200 and '최초 작성자' in response.text
    response = client.post(f"/qa-agent/results/{finding['id']}/processing",
        data={'product': 'vxvue', 'state': 'done', 'actor': 'QA 담당', 'note': '추가 조치 없음'})
    assert response.status_code == 200 and '추가 조치 없음' in response.text
    response = client.get('/qa-agent/results', params={'state': 'done', 'reporter': 'creator'})
    assert f"/findings/{finding['id']}" in response.text
    assert store.get_finding(finding['id'])['summary'] == finding['summary']
    assert client.get('/qa-agent/results?category=invalid').status_code == 400


def test_known_review_verdict_is_in_review_filter(world):
    fid = world['store'].add_finding(world['changed']['run_id'], 'qa-spec-coverage-analysis', 'review',
        {'subject': 'VP-999', 'analysis_type': 'SPEC_COVERAGE', 'verdict': 'PARTIALLY_COVERED', 'summary': '검토 필터 대상'}, 'vxvue')
    response = world['client'].get('/qa-agent/results', params={'level': '검토 필요'})
    assert response.status_code == 200 and f'/findings/{fid}' in response.text


def test_dashboard_sorting_in_http_browser(world, monkeypatch):
    import pytest
    import threading
    from dataclasses import replace
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path
    from urllib.parse import urlparse
    from app.modules.qa_agent import dashboard as dash
    playwright = pytest.importorskip('playwright.sync_api')
    choice = dash.ProductChoice(replace(world['cfg'], savings_enabled=True), world['store'], ['VXvue'])
    monkeypatch.setattr(dash, 'choose', lambda product='': choice)
    world['store'].storage.qa_save_reporters('vxvue', [
        {'id':'VP-100','reporter_id':'a','reporter_name':'가 작성자','updated':'2026-10-13T01:00:00Z'},
        {'id':'VP-200','reporter_id':'b','reporter_name':'나 작성자','updated':'2026-10-14T01:00:00Z'}])
    static_root = Path(__file__).resolve().parents[1] / 'app/web'
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            path = urlparse(self.path).path
            if path.startswith('/static/'):
                file = static_root / path.lstrip('/')
                body = file.read_bytes() if file.is_file() else b''
                kind = 'text/javascript' if file.suffix == '.js' else 'text/css'
            else:
                response = world['client'].get(self.path)
                body, kind = response.content, response.headers.get('content-type', 'text/html')
            self.send_response(200)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                for width in (1366, 1920):
                    page = browser.new_page(viewport={'width':width, 'height':1080})
                    errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(f'http://127.0.0.1:{server.server_port}/qa-agent', wait_until='domcontentloaded')
                    assert page.locator('.qa-card h4').first.inner_text().startswith('[VP-200]')
                    assert '2026-10-14 10:00' in page.locator('.qa-card').first.inner_text()
                    page.locator('select[name=sort]').select_option('author')
                    page.get_by_role('button', name='적용', exact=True).click()
                    page.wait_for_url('**sort=author')
                    assert page.locator('.qa-card h4').first.inner_text().startswith('[VP-100]')
                    assert '가 작성자' in page.locator('.qa-card').first.inner_text()
                    assert not errors
                    page.close()
            finally:
                browser.close()
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)
