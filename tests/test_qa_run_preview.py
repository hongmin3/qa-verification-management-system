"""Validates: REQ-QAINTEL-041. Read-only preflight through the HTTP entry point."""
from datetime import datetime, timedelta

import pytest

from app.modules.daily_qa.change_events import ChangeEvent, ISSUE_AUDIT_TARGET
from app.modules.qa_agent import dashboard as dash
from tests.test_qa_intel_dashboard import world  # noqa: F401


def test_preview_matches_backlog_cap_and_never_writes(world):
    today = datetime.now(dash.KST).date()
    yesterday = today - timedelta(days=1)
    store = world['store']
    for day, count in [(yesterday, 12), (today, 2)]:
        events = [ChangeEvent('issue', f'VP-{day.day}-{i}', 'ISSUE_CREATED',
                              analysis_required=True, analyses=['NEW_ISSUE']).as_dict() for i in range(count)]
        store.add_events(world['cfg'].slug, day.strftime('%Y%m%d') + '-100000-vxvue', events)
    before = (store.list_runs(), store.list_events())
    response = world['client'].get('/qa-agent/run-preview', params={'since': yesterday.isoformat(), 'until': today.isoformat()})
    assert response.status_code == 200
    result = response.json()
    assert result['changes']['waiting'] == 14
    assert result['changes']['selected'] == 12
    assert result['changes']['deferred'] == 2
    assert result['changes']['targets'] == {'NEW_ISSUE': 12}
    assert result['changes']['batches_by_count'] == 3
    assert result['new_changes'] is None
    past = world['client'].get('/qa-agent/run-preview', params={'until': yesterday.isoformat()}).json()
    assert past['changes']['selected'] == 0
    assert before == (store.list_runs(), store.list_events())


def test_preview_merges_comments_but_keeps_audit_separate(world):
    store, cfg = world['store'], world['cfg']
    day = datetime.now(dash.KST).strftime('%Y%m%d')
    events = [ChangeEvent('issue', 'VP-500', 'ISSUE_RD_RESULT_CHANGED', analysis_required=True, analyses=['FIXED_ISSUE']),
              ChangeEvent('issue', 'VP-500', 'ISSUE_COMMENT_ADDED', analysis_required=True, analyses=['COMMENT']),
              ChangeEvent('issue', 'VP-501', ISSUE_AUDIT_TARGET, analysis_required=True, analyses=['ISSUE_AUDIT'])]
    store.add_events(cfg.slug, day + '-100000-vxvue', [e.as_dict() for e in events])
    result = world['client'].get('/qa-agent/run-preview').json()
    assert result['changes']['targets'] == {'FIXED_ISSUE': 1, 'ISSUE_AUDIT': 1}
    assert result['audit']['selected'] == 1
    page = world['client'].get('/qa-agent').text
    assert 'id="qa-run-preview"' in page and '/static/qa-run-preview.js' in page


@pytest.mark.parametrize('params,code', [({'until': 'bad-date'}, 400), ({'product': 'no-product'}, 404),
                                       ({'since': '2026-10-14', 'until': '2026-10-13'}, 400)])
def test_preview_rejects_invalid_period_or_product(world, params, code):
    assert world['client'].get('/qa-agent/run-preview', params=params).status_code == code


@pytest.mark.parametrize('width', [390, 1920])
def test_preview_in_browser_updates_dates_and_reports_failure(cancel_site, width, tmp_path):
    from playwright.sync_api import sync_playwright, expect
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': width, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(cancel_site['base'] + '/qa-agent')
        panel = page.locator('#qa-run-preview')
        expect(panel).to_contain_text('새 변경·전체 점검의 새 대상은 실행 후 확정')
        page.screenshot(path=str(tmp_path / f'preview-{width}.png'), full_page=True)
        page.locator('#run-until').fill('2026-10-01')
        page.locator('#run-since').fill('2026-09-30')
        page.locator('#run-since').dispatch_event('change')
        expect(panel).to_contain_text('과거 종료일의 변경 점검은 현재 대기분을 이어받지 않습니다.')
        page.route('**/qa-agent/run-preview?**', lambda route: route.fulfill(status=503, json={'detail': '조회 실패 확인'}))
        page.locator('#run-since').dispatch_event('change')
        expect(panel).to_have_text('조회 실패 확인')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert errors == []
        browser.close()


from tests.test_qa_run_progress_browser import cancel_site  # noqa: E402,F401


def test_late_preview_response_cannot_replace_newer_period():
    from pathlib import Path
    from playwright.sync_api import sync_playwright, expect
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content('<input id="run-since"><input id="run-until"><div id="qa-run-preview" data-product="VXvue"></div>')
        # Deliberately ignore abort to exercise a response already in flight.
        page.evaluate('() => { window.responses = []; window.fetch = () => new Promise(resolve => responses.push(resolve)); }')
        source = Path(__file__).resolve().parents[1] / 'app/web/static/qa-run-preview.js'
        page.add_script_tag(content=source.read_text(encoding='utf-8'))
        page.locator('#run-until').dispatch_event('change')
        page.evaluate('''() => {
          const info = {waiting: 0, selected: 0, deferred: 0, targets: {}, batches_by_count: 0};
          window.newData = {changes: info, audit: info, labels: {}, max_tasks: 30, historical: true};
          responses[1]({ok: true, json: async () => newData});
        }''')
        expect(page.locator('#qa-run-preview')).to_contain_text('과거 종료일')
        page.evaluate('''async () => {
          responses[0]({ok: true, json: async () => ({...newData, historical: false})});
          await new Promise(resolve => setTimeout(resolve, 30));
        }''')
        expect(page.locator('#qa-run-preview')).to_contain_text('과거 종료일')
        browser.close()
