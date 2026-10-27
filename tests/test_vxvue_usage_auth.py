"""Validates: REQ-VXVUEUSAGE-003/004/007. No real credentials or AI calls."""
import importlib,re,uuid
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.core.storage import Storage
from app.modules.vxvue_usage import auth

@pytest.fixture
def owner_web(tmp_path,monkeypatch):
    router=importlib.import_module('app.modules.vxvue_usage.router')
    settings=SimpleNamespace(root=tmp_path,secrets=SimpleNamespace(vxvue_usage_owner_username='creator',vxvue_usage_owner_password_hash=auth.password_hash('synthetic-pass'),vxvue_usage_owner_device_ids='["abc123"]',vxvue_usage_signing_key='s'*48))
    settings.get=lambda key,default=None:default
    store=Storage(tmp_path/'data/app.db')
    monkeypatch.setattr(router,'get_settings',lambda:settings);monkeypatch.setattr(router,'get_storage',lambda:store)
    auth.reset_rate_limits()
    chat=importlib.import_module('app.modules.vxvue_chatbot.router')
    monkeypatch.setattr(chat,'get_storage',lambda:store);monkeypatch.setattr(chat,'get_settings',lambda:settings);monkeypatch.setattr(chat.service,'wake',lambda:None)
    app=FastAPI();app.include_router(router.router,prefix='/vxvue-usage');app.include_router(chat.router,prefix='/vxvue-chatbot')
    client=TestClient(app,base_url='http://127.0.0.1',client=('127.0.0.1',50000))
    return client,store,settings,router

def login(client,password='synthetic-pass'):
    page=client.get('/vxvue-usage')
    csrf=re.search(r'name="csrf" value="([^"]+)"',page.text)[1]
    return client.post('/vxvue-usage/login',data={'username':'creator','password':password,'csrf':csrf},follow_redirects=False)

def token(client):return client.get('/vxvue-usage/api/session').json()['csrf']

def test_unconfigured_and_logged_out_cannot_read_or_forge(owner_web):
    client,store,settings,_=owner_web
    assert client.get('/vxvue-usage/api/dashboard').status_code==403
    assert client.post('/vxvue-usage/api/activities',json={'question':'private'}).status_code==403
    settings.secrets.vxvue_usage_owner_password_hash=''
    assert '설정' in client.get('/vxvue-usage').text
    assert client.get('/vxvue-usage/api/dashboard').status_code==403

def test_login_csrf_expiry_tamper_and_no_plain_password(owner_web):
    client,store,settings,_=owner_web
    assert not auth.verify_password('wrong',settings.secrets.vxvue_usage_owner_password_hash)
    assert 'synthetic-pass' not in settings.secrets.vxvue_usage_owner_password_hash
    assert client.post('/vxvue-usage/login',data={'username':'creator','password':'synthetic-pass','csrf':'bad'}).status_code==403
    response=login(client);assert response.status_code==303
    assert 'HttpOnly' in response.headers['set-cookie'] and 'SameSite=strict' in response.headers['set-cookie']
    assert client.get('/vxvue-usage/api/dashboard').status_code==200
    assert client.post('/vxvue-usage/api/activities',json={'question':'bad'}).status_code==403
    assert client.post('/vxvue-usage/api/activities',headers={'X-CSRF-Token':token(client),'Origin':'https://evil.test'},json={}).status_code==403
    client.cookies.set(auth.COOKIE,'tampered')
    assert client.get('/vxvue-usage/api/dashboard').status_code==403

def test_owner_save_feedback_and_export_excludes_raw(owner_web):
    client,store,_,_=owner_web;login(client);headers={'X-CSRF-Token':token(client)}
    payload={'question':'<script>PRIVATE_SENTINEL</script>','save_original':True,'feature':'issue'}
    response=client.post('/vxvue-usage/api/activities',headers=headers,json=payload);assert response.status_code==200,response.text
    aid=response.json()['activity_id']
    assert client.post(f'/vxvue-usage/api/activities/{aid}/finish',headers=headers,json={'answer':'my final PRIVATE_SENTINEL','summary':'used PRIVATE_SENTINEL','status':'ANSWER_RECORDED'}).status_code==200
    assert client.post(f'/vxvue-usage/api/activities/{aid}/feedback',headers=headers,json={'bookmark':True,'evaluation':'used','baseline_minutes':20,'actual_minutes':8,'portfolio_note':'=email person@example.test'}).status_code==200
    assert client.get('/vxvue-usage/api/activities/'+aid).json()['question']==payload['question']
    preview=client.get('/vxvue-usage/api/export-preview').json()
    csv=client.get('/vxvue-usage/export.csv?preview_id='+preview['preview_id']).text
    assert 'PRIVATE_SENTINEL' not in str(preview)+csv and 'person@example.test' not in csv
    assert 'binding' not in csv and aid not in csv
    public=client.get('/vxvue-usage/api/export-preview?include_notes=true').json()
    assert 'person@example.test' not in str(public) and '[EMAIL]' in str(public)
    assert client.get('/vxvue-usage/export.csv?preview_id='+public['preview_id']).text.find("'=email")>=0

def test_csv_formula_prefix_and_public_note_masking():
    from app.modules.vxvue_usage.export import safe_cell,public_note
    assert safe_cell('=1+1').startswith("'") and safe_cell(' @cmd').startswith("'")
    assert '10.1.2.3' not in public_note(r'host 10.1.2.3 \\10.1.2.3\private\data token=secret-value-long')

def test_unicode_login_and_csrf_are_rejected_without_server_error(owner_web):
    client,_,_,_=owner_web
    page=client.get('/vxvue-usage');csrf=re.search(r'name="csrf" value="([^"]+)"',page.text)[1]
    assert client.post('/vxvue-usage/login',data={'username':'제작자','password':'wrong','csrf':csrf}).status_code==403

def test_web_binding_requires_both_owner_login_and_browser_cookie(owner_web):
    client,store,settings,_=owner_web
    client.get('/vxvue-chatbot');chat=client.post('/vxvue-chatbot/conversations').json()['id']
    job=client.post(f'/vxvue-chatbot/conversations/{chat}/messages',data={'text':'own web question'}).json()
    store.vxchat_start(job['id']);store.vxchat_finish(job['id'],'DONE','own web answer')
    assert client.post('/vxvue-chatbot/usage-bind').status_code==403
    login(client);headers={'X-CSRF-Token':token(client)}
    ticket=client.post('/vxvue-usage/api/web-ticket',headers=headers).json()['ticket']
    assert client.post('/vxvue-chatbot/usage-bind',headers={**headers,'X-VXvue-Owner-Ticket':ticket}).status_code==200
    assert client.post('/vxvue-chatbot/usage-bind',headers={**headers,'X-VXvue-Owner-Ticket':ticket}).status_code==403
    assert client.get('/vxvue-usage/api/web-jobs').json()[0]['id']==job['id']
    imported=client.post('/vxvue-usage/api/web-import/'+job['id'],headers=headers,json={'save_original':True,'summary':'used'})
    assert imported.status_code==200
    assert uuid.UUID(imported.json()['activity_id']).hex!=job['id']
    assert store.usage_get(imported.json()['activity_id'])['question']=='own web question'
    other=TestClient(client.app,base_url='http://127.0.0.1',client=('127.0.0.1',50001));login(other);other.get('/vxvue-chatbot')
    other_headers={'X-CSRF-Token':token(other)}
    ticket=other.post('/vxvue-usage/api/web-ticket',headers=other_headers).json()['ticket']
    other.post('/vxvue-chatbot/usage-bind',headers={**other_headers,'X-VXvue-Owner-Ticket':ticket})
    assert other.post('/vxvue-usage/api/web-import/'+job['id'],headers=other_headers,json={'save_original':True}).status_code==404
    assert other.get('/vxvue-usage/api/web-jobs').json()==[]

def test_export_download_uses_reviewed_snapshot(owner_web):
    client,store,_,_=owner_web;login(client)
    preview=client.get('/vxvue-usage/api/export-preview').json()
    assert preview.get('preview_id')
    assert client.get('/vxvue-usage/export.csv').status_code==400
    aid=str(uuid.uuid4());store.usage_start(aid,'a'*64,'owner_ui','issue');store.usage_feedback(aid,bookmark=True)
    downloaded=client.get('/vxvue-usage/export.csv?preview_id='+preview['preview_id'])
    assert downloaded.status_code==200 and 'case,' not in downloaded.text

@pytest.mark.parametrize('peer,host,headers,scheme,allowed',[
 ('10.1.2.3','127.0.0.1',{},'http',False),
 ('10.1.2.3','127.0.0.1',{'x-real-ip':'127.0.0.1','x-forwarded-proto':'https'},'http',False),
 ('127.0.0.1','qa.example.test',{'x-real-ip':'10.1.2.3'},'http',False),
 ('127.0.0.1','127.0.0.1:24359',{},'http',True),
 ('127.0.0.1','localhost.evil.test',{},'http',False),
 ('127.0.0.1','127.0.0.1',{'x-forwarded-for':'127.0.0.1'},'http',False),
 ('127.0.0.1','qa.example.test',{'x-real-ip':'10.1.2.3','x-forwarded-proto':'https'},'http',True),
 ('127.0.0.1','qa.example.test',{'x-forwarded-proto':'https,http'},'http',False),
 ('10.1.2.3','qa.example.test',{},'https',True),
])
def test_creator_transport_boundary(peer,host,headers,scheme,allowed):
    from starlette.requests import Request
    request=Request({'type':'http','method':'GET','scheme':scheme,'path':'/vxvue-usage','server':('qa.example.test',80),'client':(peer,50000),'headers':[(k.encode(),v.encode()) for k,v in {'host':host,**headers}.items()]})
    assert auth.secure_transport(request) is allowed
