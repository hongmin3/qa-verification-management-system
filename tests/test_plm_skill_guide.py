"""Validates: REQ-PLMSKILL-001, REQ-PLMSKILL-002. Synthetic metadata only."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from app.main import app
from tests.test_qa_run_progress_browser import browser_headless

ORIGIN='http://plm-guide.test'
PAGE='/static/plm-ai/index.html'
ASSET='/static/plm-guide'

def test_plm_guide_is_available_from_hub_and_serves_assets():
 client=TestClient(app)
 assert 'href="/static/plm-ai/index.html"' in client.get('/static/skills/plm/index.html').text
 response=client.get(PAGE)
 assert response.status_code==200
 for word in ['PLM 스킬 사용 안내','Codex','Claude Code','Teamcenter','ALM','화면 표본','음성','로그인된 Edge','저장 결과','전자서명']:
  assert word in response.text
 assert 'href="/vxvue-desktop"' in response.text
 assert client.get('/static/plm-ai/style.css').status_code==200
 assert client.get('/static/plm-ai/guide.js').status_code==200

@pytest.fixture
def page():
 playwright=pytest.importorskip('playwright.sync_api')
 with playwright.sync_playwright() as p:
  browser=p.chromium.launch(headless=browser_headless())
  page=browser.new_page(viewport={'width':1280,'height':900})
  yield page
  browser.close()

def serve(page,catalog=None,manifest=None,fail_catalog=False):
 catalog=catalog if catalog is not None else {'release_path':'training-002'}
 manifest=manifest if manifest is not None else {'release':'training-002','created':'2026-11-03','counts':{'chapters':7,'guide_pages':19,'slides':42,'videos':3,'sample_frames':21},'files':{name:{} for name in ['index.html','PLM_Guide.docx','PLM_Guide.pdf','plm-skill.zip']}}
 client=TestClient(app);seen=[];state={'fail':fail_catalog}
 def handle(route):
  path=route.request.url.removeprefix(ORIGIN);seen.append(path)
  if path==ASSET+'/current.json':
   if state['fail']:route.abort('failed')
   else:route.fulfill(json=catalog)
  elif path==ASSET+'/training-002/manifest.json':route.fulfill(json=manifest)
  else:
   r=client.get(path);route.fulfill(status=r.status_code,body=r.content,headers={'content-type':r.headers.get('content-type','text/html')})
 page.route(ORIGIN+'/**',handle)
 page.goto(ORIGIN+PAGE)
 return seen,state

def test_manifest_changes_links_and_counts_in_real_browser(page):
 serve(page)
 page.wait_for_function("document.querySelector('#release-status').textContent.includes('training-002')")
 assert page.locator('[data-count="slides"]').inner_text()=='42'
 assert page.locator('[data-count="guide_pages"]').inner_text()=='19'
 assert page.locator('#guide-link').get_attribute('href')==ASSET+'/training-002/index.html'
 assert page.locator('#skill-download').get_attribute('href')==ASSET+'/training-002/plm-skill.zip'
 assert page.locator('#pdf-download').get_attribute('aria-disabled')=='false'
 page.set_viewport_size({'width':390,'height':844})
 assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')

def test_network_failure_keeps_links_disabled_then_retry_recovers(page):
 _,state=serve(page,fail_catalog=True)
 page.wait_for_function("document.querySelector('#release-status').textContent.includes('불러오지 못')")
 assert page.locator('#skill-download').get_attribute('aria-disabled')=='true'
 assert page.locator('#skill-download').get_attribute('href') is None
 state['fail']=False
 page.get_by_role('button',name='다시 불러오기').click()
 page.wait_for_function("document.querySelector('#release-status').textContent.includes('training-002')")
 assert page.locator('#skill-download').get_attribute('aria-disabled')=='false'

@pytest.mark.parametrize('catalog,manifest',[
 ({'release_path':'../../other'},None),
 ({'release_path':'https://other.test'},None),
 ({'release_path':'training-002'},{'release':'different','counts':{},'files':{}}),
])
def test_invalid_or_mixed_release_does_not_enable_links(page,catalog,manifest):
 seen,_=serve(page,catalog=catalog,manifest=manifest)
 page.wait_for_function("document.querySelector('#release-status').textContent.includes('불러오지 못')")
 assert page.locator('#guide-link').get_attribute('href') is None
 assert not any('other' in path for path in seen)


def test_missing_file_stays_disabled_and_metadata_is_text(page):
 serve(page,manifest={'release':'training-002','created':'<img src=x onerror=alert(1)>','counts':{'slides':20},'files':{'index.html':{}}})
 page.wait_for_function("document.querySelector('#release-status').textContent.includes('training-002')")
 assert page.locator('#guide-link').get_attribute('aria-disabled')=='false'
 assert page.locator('#skill-download').get_attribute('href') is None
 assert page.locator('#pdf-download').get_attribute('aria-disabled')=='true'
 assert page.locator('#release-status img').count()==0
