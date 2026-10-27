"""Validates: REQ-VXVUEUSAGE-001/002/006/008/009; synthetic values only."""
from datetime import datetime,timedelta,timezone
import json,sqlite3,uuid
import pytest
from app.core.storage import Storage
from app.modules.vxvue_usage import telemetry

BINDING='a'*64

def uid():return str(uuid.uuid4())

@pytest.fixture
def store(tmp_path):return Storage(tmp_path/'app.db')

def event(**changes):
    return dict(event_id=uid(),occurred_at=datetime.now(timezone.utc).isoformat(),channel='desktop',feature='documents',tool='search_documents',evidence='document',status='success',duration_ms=12,actor_tag='b'*64,activity_id=None,attempt=1,source='live',diagnostic=False)|changes

def test_event_whitelist_never_persists_payload_or_identity(store):
    with pytest.raises(ValueError):store.usage_event(event(question='PRIVATE_SENTINEL',ip='10.1.2.3'))
    assert store.usage_event(event())
    with store.connect() as db:
        data=[dict(r) for r in db.execute('select * from vxusage_events')]
    assert len(data)==1 and 'question' not in data[0] and 'PRIVATE_SENTINEL' not in str(data)

def test_one_activity_five_calls_two_retries_dedupe_and_truth(store):
    activity=uid();store.usage_start(activity,BINDING,'desktop','issue',question='my question',save_original=True)
    for n in range(5):
        value=event(activity_id=activity,attempt=2 if n>2 else 1)
        assert store.usage_event(value,binding=BINDING)
        assert not store.usage_event(value,binding=BINDING)
    store.usage_event(event(tool='get_status',feature='diagnostic',evidence='none',diagnostic=True))
    store.usage_event(event(source='test'))
    dashboard=store.usage_dashboard()
    assert dashboard['calls']==5 and dashboard['retries']==2 and dashboard['diagnostics']==1 and dashboard['tasks']==1
    assert dashboard['cost_usd'] is None and dashboard['tokens'] is None and dashboard['quality_accuracy'] is None
    assert store.usage_get(activity)['status']=='IN_PROGRESS'
    assert not store.usage_finish(activity,'c'*64,status='ANSWER_RECORDED',answer='forged')
    assert store.usage_finish(activity,BINDING,status='INCOMPLETE',answer='draft')
    assert store.usage_get(activity)['status']=='INCOMPLETE'

def test_anonymous_or_unselected_raw_is_never_stored(store):
    activity=uid();store.usage_start(activity,BINDING,'web_chat','other',scope='anonymous',question='PRIVATE_SENTINEL',save_original=True)
    store.usage_finish(activity,BINDING,status='ANSWER_GENERATED',answer='PRIVATE_SENTINEL',summary='PRIVATE_SENTINEL')
    with store.connect() as db:row=dict(db.execute('select * from vxusage_activities where id=?',(activity,)).fetchone())
    assert row['question']==row['answer']==row['summary']==''
    assert store.usage_get(activity) is None

def test_retention_only_new_data_and_monthly_not_double_counted(store):
    old=(datetime.now(timezone.utc)-timedelta(days=400)).isoformat()
    chat=store.vxchat_create('original-owner')
    store.usage_event(event(occurred_at=old))
    assert store.usage_retention()['events']==1
    assert store.usage_retention()['events']==0
    with store.connect() as db:
        assert db.execute('select sum(calls) from vxusage_monthly').fetchone()[0]==1
    assert store.vxchat_get('original-owner',chat['id']) is not None

def test_recording_failure_never_changes_tool_response(monkeypatch):
    monkeypatch.setattr(telemetry,'persist',lambda *a,**k:(_ for _ in ()).throw(sqlite3.OperationalError('PRIVATE_SENTINEL')))
    result={'content':[{'type':'text','text':'unchanged'}],'isError':False}
    assert telemetry.execute(lambda:result,tool='search_documents',context={'enabled':True,'channel':'desktop','actor_tag':'b'*64,'binding':BINDING}) is result
    assert telemetry.health()['failures']>=1

def test_month_bucket_uses_korea_calendar_boundary(store):
    store.usage_event(event(occurred_at='2026-10-31T15:30:00+00:00'))
    with store.connect() as db:assert db.execute('select month from vxusage_events').fetchone()[0]=='2026-11'

def test_owner_tools_and_workflow_are_request_scoped(store,tmp_path,monkeypatch):
    from types import SimpleNamespace
    from app.modules.vxvue_chatbot import desktop
    from app.modules.vxvue_usage import service
    settings=SimpleNamespace(root=tmp_path,secrets=SimpleNamespace(vxvue_usage_signing_key='s'*40,vxvue_usage_owner_device_ids='["owner-device"]'))
    owner=telemetry.context(settings,{'kind':'device','device_id':'owner-device'},secure=True)
    team=telemetry.context(settings,{'kind':'device','device_id':'another-device'})
    monkeypatch.setattr(telemetry,'store',lambda *a,**k:store)
    monkeypatch.setattr(desktop,'get_settings',lambda:settings)
    snap={'id':'s','rules':'BASE RULES','skill_text':'BASE SKILL','skill_metadata':{}}
    monkeypatch.setattr(desktop,'snapshot',lambda sid:snap)
    names=lambda ctx:{t['name'] for t in desktop.dispatch({'method':'tools/list'},{'usage':ctx})['tools']}
    assert 'begin_vxvue_activity' not in names(team)
    assert 'begin_vxvue_activity' in names(owner)
    def workflow(ctx):
        result=desktop.dispatch({'method':'tools/call','params':{'name':'get_workflow','arguments':{'snapshot_id':'s'}}},{'usage':ctx})
        assert not result['isError'];return json.loads(result['content'][0]['text'])['text']
    original=workflow(team);assert 'begin_vxvue_activity' in workflow(owner)
    assert workflow(team)==original and snap['rules']=='BASE RULES' and snap['skill_text']=='BASE SKILL'
    denied=desktop.dispatch({'method':'tools/call','params':{'name':'begin_vxvue_activity','arguments':{'question':'private','save_original':True}}},{'usage':team})
    assert denied['isError']
    begun=desktop.dispatch({'method':'tools/call','params':{'name':'begin_vxvue_activity','arguments':{'question':'owner question','save_original':True}}},{'usage':owner})
    aid=json.loads(begun['content'][0]['text'])['activity_id']
    assert store.usage_get(aid)['question']=='owner question'
    forged=desktop.dispatch({'method':'tools/call','params':{'name':'finish_vxvue_activity','arguments':{'activity_id':aid,'answer':'forged'}}},{'usage':team})
    assert forged['isError'] and store.usage_get(aid)['answer']==''
    assert not telemetry.is_owner(settings,{'kind':'team','device_id':None})

def test_stdio_observer_keeps_original_reply_and_no_arguments(store,monkeypatch):
    import io
    from app.modules.vxvue_chatbot.mcp_server import serve
    monkeypatch.setattr(telemetry,'store',lambda *a,**k:store)
    class Session:
        usage_context={'enabled':True,'channel':'web_chat','actor_tag':'b'*64,'binding':BINDING}
        def call(self,name,args):return {'answer':'PRIVATE_SENTINEL'}
    request={'id':1,'method':'tools/call','params':{'name':'search_documents','arguments':{'query':'PRIVATE_SENTINEL'}}}
    target=io.StringIO();serve(Session(),io.StringIO(json.dumps(request)+'\n'),target)
    assert json.loads(json.loads(target.getvalue())['result']['content'][0]['text'])=={'answer':'PRIVATE_SENTINEL'}
    with store.connect() as db:
        rows=[dict(r) for r in db.execute('select * from vxusage_events')]
    assert len(rows)==1 and 'PRIVATE_SENTINEL' not in str(rows)

def test_final_record_requires_answer_when_original_selected(store):
    activity=uid();store.usage_start(activity,BINDING,'desktop','issue',save_original=True)
    with pytest.raises(ValueError):store.usage_finish(activity,BINDING)
    assert store.usage_get(activity)['status']=='IN_PROGRESS'


@pytest.mark.parametrize('status',['ANSWER_RECORDED','INCOMPLETE','FAILED','CANCELLED'])
def test_begin_retry_preserves_existing_terminal_status_and_originals(store,monkeypatch,status):
    from app.modules.vxvue_usage.service import owner_call
    monkeypatch.setattr(telemetry,'store',lambda *args,**kwargs:store)
    activity=uid();store.usage_start(activity,BINDING,'desktop','issue',question='original',save_original=True)
    store.usage_finish(activity,BINDING,status=status,answer='recorded answer')
    context={'owner':True,'secure':True,'binding':BINDING,'channel':'desktop'}
    result=owner_call('begin_vxvue_activity',{'activity_id':activity,'question':'retry input','save_original':True},context)
    assert result['status']==status
    row=store.usage_get(activity)
    assert row['status']==status and row['question']=='original' and row['answer']=='recorded answer'
    assert store.usage_dashboard()['tasks']==1

def test_attempt_three_is_one_retry_call_not_two_extra_requests(store):
    store.usage_event(event(attempt=3))
    assert store.usage_dashboard()['retries']==1

def test_http_diagnostic_purpose_requires_server_owner_identity(store,tmp_path,monkeypatch):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.modules.vxvue_chatbot import desktop
    settings=SimpleNamespace(root=tmp_path,secrets=SimpleNamespace(vxvue_usage_signing_key='s'*40,vxvue_usage_owner_device_ids='["owner-device"]'))
    monkeypatch.setattr(desktop,'get_settings',lambda:settings)
    monkeypatch.setattr(telemetry,'store',lambda *a,**k:store)
    monkeypatch.setattr(desktop,'_authorize',lambda req:{'kind':'device','device_id':'owner-device' if req.headers.get('authorization')=='Bearer synthetic-owner' else 'team-device'})
    monkeypatch.setattr(desktop,'call',lambda name,args:{'unchanged':'PRIVATE_SENTINEL'})
    app=FastAPI();app.include_router(desktop.router,prefix='/vxvue-desktop');client=TestClient(app)
    message={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'search_documents','arguments':{'query':'PRIVATE_SENTINEL'}}}
    for token in ('synthetic-owner','synthetic-team'):
        reply=client.post('/vxvue-desktop/mcp',json=message,headers={'Authorization':'Bearer '+token,'X-VXvue-Usage-Purpose':'diagnostic'})
        assert json.loads(reply.json()['result']['content'][0]['text'])=={'unchanged':'PRIVATE_SENTINEL'}
    stats=store.usage_dashboard();assert stats['calls']==1 and stats['diagnostics']==1
    with store.connect() as db:
        rows=[dict(r) for r in db.execute('select * from vxusage_events')]
    assert 'PRIVATE_SENTINEL' not in str(rows) and 'owner-device' not in str(rows)

@pytest.mark.parametrize('tool,feature',[('find_testcases','testcase'),('trace_requirements','specification'),('compare_packages','specification'),('get_document','documents')])
def test_tool_feature_is_fixed_without_reading_arguments(store,monkeypatch,tool,feature):
    monkeypatch.setattr(telemetry,'store',lambda *a,**k:store)
    telemetry.execute(lambda:{'isError':False},tool=tool,context={'enabled':True,'channel':'desktop','actor_tag':None,'binding':None})
    with store.connect() as db:assert db.execute('select feature from vxusage_events').fetchone()[0]==feature

def test_anonymous_web_tasks_do_not_share_binding_or_raw_job_uuid(store,tmp_path,monkeypatch):
    from types import SimpleNamespace
    settings=SimpleNamespace(root=tmp_path,secrets=SimpleNamespace(vxvue_usage_signing_key='s'*40,vxvue_usage_owner_device_ids='[]'))
    monkeypatch.setattr(telemetry,'store',lambda *a,**k:store)
    jobs=[{'id':uuid.uuid4().hex,'owner':'synthetic-browser-hash'} for _ in range(2)]
    contexts=[telemetry.start_web(settings,job) for job in jobs]
    with store.connect() as db:rows=[dict(r) for r in db.execute('select * from vxusage_activities')]
    assert len({r['binding'] for r in rows})==2
    assert all(uuid.UUID(r['id']).hex not in {j['id'] for j in jobs} for r in rows)
    assert 'actor_binding' not in str(contexts) and contexts[0]['actor_tag']==contexts[1]['actor_tag']

def test_retention_expires_only_new_web_binding(store):
    store.usage_bind_web(BINDING)
    with store.connect() as db:db.execute('update vxusage_web_bindings set created_at=?',((datetime.now(timezone.utc)-timedelta(days=400)).isoformat(),));db.commit()
    result=store.usage_retention()
    assert result['bindings']==1 and not store.usage_web_bound(BINDING)

def test_insecure_owner_device_is_calls_only_no_retry_or_task_association(store,monkeypatch):
    activity=uid();store.usage_start(activity,BINDING,'desktop','issue')
    event_id=uid();monkeypatch.setattr(telemetry,'store',lambda *a,**k:store)
    context={'enabled':True,'channel':'desktop','binding':BINDING,'owner_device':True,'owner':False,'secure':False}
    telemetry.execute(lambda:{'isError':False},tool='search_documents',context=context,metadata={'event_id':event_id,'activity_id':activity,'attempt':3})
    with store.connect() as db:row=dict(db.execute('select * from vxusage_events').fetchone())
    assert row['activity_id'] is None and row['event_id']!=event_id and row['attempt']==1

def test_web_month_boundary_does_not_reuse_previous_month_actor(store,tmp_path,monkeypatch):
    from types import SimpleNamespace
    class Clock:
        value=datetime(2026,10,31,14,59,tzinfo=timezone.utc)
        @classmethod
        def now(cls,*args):return cls.value
    monkeypatch.setattr(telemetry,'datetime',Clock);monkeypatch.setattr(telemetry,'store',lambda *a,**k:store)
    settings=SimpleNamespace(root=tmp_path,secrets=SimpleNamespace(vxvue_usage_signing_key='s'*40,vxvue_usage_owner_device_ids='[]'))
    ctx=telemetry.start_web(settings,{'id':uuid.uuid4().hex,'owner':'synthetic-browser'})
    Clock.value=datetime(2026,10,31,15,30,tzinfo=timezone.utc)
    telemetry.execute(lambda:{'isError':False},tool='get_document',context=ctx)
    with store.connect() as db:row=dict(db.execute('select * from vxusage_events').fetchone())
    assert row['month']=='2026-11' and row['actor_tag'] is None

def test_real_sqlite_lock_is_bounded_and_does_not_change_answer(store,monkeypatch):
    import time
    monkeypatch.setattr(telemetry,'store',lambda *a,**k:store)
    result={'isError':False,'content':[{'type':'text','text':'unchanged'}]}
    lock=sqlite3.connect(store.db_path);lock.execute('BEGIN IMMEDIATE')
    started=time.monotonic()
    try:assert telemetry.execute(lambda:result,tool='search_documents',context={'enabled':True,'channel':'desktop','binding':BINDING}) is result
    finally:lock.rollback();lock.close()
    assert time.monotonic()-started<.75


def test_retention_registered_job_only_prunes_usage(store,monkeypatch):
    from app.modules.vxvue_usage import scheduled_jobs
    old=(datetime.now(timezone.utc)-timedelta(days=400)).isoformat()
    store.usage_event(event(occurred_at=old));chat=store.vxchat_create('preserved')
    monkeypatch.setattr(telemetry,'store',lambda *a,**k:store)
    class Scheduler:
        def add_job(self,fn,*args,**kwargs):self.operation=fn;self.kwargs=kwargs
    scheduler=Scheduler();scheduled_jobs.register_scheduled_jobs(scheduler);scheduler.operation()
    with store.connect() as db:assert db.execute('select count(*) from vxusage_events').fetchone()[0]==0
    assert store.vxchat_get('preserved',chat['id']) is not None
