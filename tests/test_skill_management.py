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
 seen=set();pending=[ROOT+'index.html'];titles=[]
 while pending:
  path=pending.pop()
  if path in seen:continue
  seen.add(path);response=client.get(path);assert response.status_code==200,path
  if not path.endswith('.html'):continue
  soup=BeautifulSoup(response.text,'html.parser');titles.append(soup.h1.text)
  for a in soup.select('a[href],link[href]'):
   href=a['href'];assert not urlsplit(href).netloc
   assert href.startswith('/') or href.startswith('#'),href
   if href.startswith(ROOT):pending.append(href)
   elif href.startswith('/'):
    assert client.get(href).status_code==200,href
 assert len([p for p in seen if p.endswith('.html')])==9
 for key in ['vxvue','bellalun','xipl','alm']:
  assert ROOT+key+'/index.html' in seen
  assert ROOT+key+'/updates.html' in seen
 assert '연결 준비 중' in client.get(ROOT+'bellalun/index.html').text
 assert '자동 배포' in client.get(ROOT+'alm/updates.html').text
