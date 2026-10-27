"""Validates: TEST-VXVUEUSAGE-003. Synthetic HTTP UI, headless browser, zero AI calls."""
import json,socket,threading,time,uuid
from contextlib import contextmanager
from pathlib import Path
import pytest,uvicorn
from tests.test_vxvue_usage_auth import owner_web  # noqa: F401
from tests.test_vxvue_usage import BINDING,event

playwright=pytest.importorskip('playwright.sync_api')

@contextmanager
def running(app):
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(32)
    server=uvicorn.Server(uvicorn.Config(app,log_level='error',access_log=False))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
    deadline=time.monotonic()+10
    while not server.started:
        if time.monotonic()>deadline:raise RuntimeError('합성 UI 서버 시작 실패')
        time.sleep(.02)
    try:yield 'http://127.0.0.1:'+str(sock.getsockname()[1])
    finally:server.should_exit=True;thread.join(5);sock.close()


def test_creator_ui_selection_metrics_export_and_xss(owner_web,tmp_path,monkeypatch):
    client,store,_,_=owner_web
    assert '/vxvue-usage' not in client.get('/vxvue-chatbot').text
    aid=str(uuid.uuid4());store.usage_start(aid,BINDING,'desktop','issue')
    for i in range(5):store.usage_event(event(activity_id=aid,attempt=2 if i>2 else 1),binding=BINDING)
    with running(client.app) as base, playwright.sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1000},accept_downloads=True)
        page.goto(base+'/vxvue-usage')
        page.locator('[name=username]').fill('creator');page.locator('[name=password]').fill('synthetic-pass')
        page.get_by_role('button',name='로그인',exact=True).click()
        playwright.expect(page.locator('#usage-status')).to_contain_text('불러왔습니다')
        cards=page.locator('.usage-card')
        playwright.expect(cards.nth(0).locator('strong')).to_have_text('5')
        playwright.expect(cards.nth(1).locator('strong')).to_have_text('1')
        playwright.expect(cards.nth(3).locator('strong')).to_have_text('2')
        page.locator('#save-original').check()
        sentinel='<img src=x onerror="globalThis.__usageXss=1"> PRIVATE_SENTINEL'
        page.locator('#question').fill(sentinel);page.locator('#answer').fill('final PRIVATE_SENTINEL');page.locator('#summary').fill('used PRIVATE_SENTINEL')
        page.locator('#activity-form button').click()
        playwright.expect(page.locator('#usage-status')).to_contain_text('선택한 본인 기록을 저장')
        assert page.evaluate('globalThis.__usageXss') is None
        playwright.expect(page.locator('#question')).to_have_value(sentinel)
        page.locator('#bookmark').check();page.locator('#evaluation').select_option('used')
        page.locator('#baseline').fill('20');page.locator('#actual').fill('8');page.locator('#portfolio-note').fill('@public person@example.test')
        page.locator('#feedback-form button').click()
        playwright.expect(page.locator('#usage-status')).to_contain_text('활용 평가')
        page.locator('#bookmarks').check()
        page.locator('#search-form button').first.click()
        playwright.expect(page.locator('.usage-history-item')).to_have_count(1)
        page.locator('#include-notes').check();page.locator('#preview').click()
        playwright.expect(page.locator('#export-table')).to_contain_text('[EMAIL]')
        assert 'PRIVATE_SENTINEL' not in page.locator('#export-table').inner_text()
        with page.expect_download() as downloaded:page.locator('#csv-download').click()
        output=tmp_path/'usage.csv';downloaded.value.save_as(output)
        csv=output.read_text(encoding='utf-8-sig')
        assert 'PRIVATE_SENTINEL' not in csv and 'person@example.test' not in csv and "'@public" in csv
        assert page.evaluate('globalThis.__usageXss') is None
        with store.connect() as db:assert db.execute('select count(*) from vxusage_events').fetchone()[0]==5
        page.locator('#usage-dashboard').scroll_into_view_if_needed();page.screenshot(path=str(tmp_path/'owner-ui.png'),full_page=True)
        browser.close()
