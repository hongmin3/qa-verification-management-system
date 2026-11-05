"""Validates: REQ-ALMSKILL-004. Checks the complete published navigation graph."""
from urllib.parse import urlsplit
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from app.main import app

ROOT='/static/skills/'

def test_skill_management_home_and_all_document_links():
 client=TestClient(app)
 home=BeautifulSoup(client.get('/').text,'html.parser')
 card=home.select_one('a[href="/static/skills/index.html"]')
 assert card and card.h3.text=='스킬 관리'
 seen=set();pending=[ROOT+'index.html']
 while pending:
  path=pending.pop()
  if path in seen:continue
  seen.add(path);response=client.get(path);assert response.status_code==200,path
  if not path.endswith('.html'):continue
  soup=BeautifulSoup(response.text,'html.parser')
  for a in soup.select('a[href],link[href]'):
   href=a['href']
   if urlsplit(href).netloc:
    assert href=='https://learn.chatgpt.com/docs/build-skills'
    continue
   assert href.startswith('/') or href.startswith('#'),href
   # Ticket downloads enroll a device and are tested with isolated enrollment
   # fixtures; a static navigation crawl must not mint production tickets.
   if href=='/vxvue-desktop/download':continue
   if href.startswith(ROOT):pending.append(href)
   elif href.startswith('/'):
    assert client.get(href).status_code==200,href
 for key in ['vxvue','bellalun','xipl','alm','plm']:
  assert ROOT+key+'/updates.html' in seen
 assert '자동 배포' in client.get(ROOT+'alm/updates.html').text

def test_cards_open_actual_guides_directly():
 client=TestClient(app)
 soup=BeautifulSoup(client.get(ROOT+'index.html').text,'html.parser')
 assert {a.h2.text:a['href'] for a in soup.select('a.card')}=={
  'VXvue':'/vxvue-desktop','ALM':'/static/alm-ai/index.html', 'PLM':'/static/plm-ai/index.html',
  'XIPL':ROOT+'xipl/index.html','Bellalun':ROOT+'bellalun/index.html'}
 for key,target in [('vxvue','/vxvue-desktop'),('alm','/static/alm-ai/index.html'),('plm','/static/plm-ai/index.html')]:
  old=BeautifulSoup(client.get(ROOT+key+'/index.html').text,'html.parser')
  assert old.select_one('meta[http-equiv="refresh"]')['content']=='0;url='+target
  assert old.select_one('a')['href']==target

def test_xipl_explains_usage_not_only_connection_link():
 soup=BeautifulSoup(TestClient(app).get(ROOT+'xipl/index.html').text,'html.parser')
 for anchor in ['install','first','tools','examples','limits','trouble']:
  assert soup.find(id=anchor),anchor
 for term in ['xipl_get_status','xipl_search_documents','xipl_get_document','xipl_get_document_page','snapshot_id']:
  assert term in soup.get_text(),term
