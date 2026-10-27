"""Payload-free, best-effort observation. Nothing is appended to tool responses."""
from datetime import datetime,timezone
import hashlib,hmac,json,logging,re,threading,time,uuid
from pathlib import Path
from app.core.vxusage_storage import month_value

_META={
 'find_testcases':('testcase','document'),'trace_requirements':('specification','document'),'compare_packages':('specification','document'),
 'get_status':('diagnostic','none'),'get_workflow':('diagnostic','none'),
 'get_polarion_item':('polarion','alm'),'polarion_query':('polarion','alm'),'read_polarion_result':('polarion','alm'),
 'get_redmine_issue':('redmine','issue'),'search_redmine_issues':('redmine','issue'),'read_redmine_result':('redmine','issue'),
 'get_generator_evidence':('generator','generator'),'get_manual_review':('manual','manual'),'make_manual_review_report':('manual','manual'),
 'read_attachment':('documents','attachment'),'begin_vxvue_activity':('diagnostic','none'),'finish_vxvue_activity':('diagnostic','none')}
_DOCS={'search_documents','get_document','get_document_page','get_document_pages','find_testcases','compare_packages','trace_requirements','batch_lookup'}
_failures=0
_lock=threading.Lock()
_pruned={}


def signing_key(settings):
    value=getattr(getattr(settings,'secrets',None),'vxvue_usage_signing_key','')
    return value.encode() if isinstance(value,str) and len(value.encode())>=32 else None


def digest(settings,value):
    key=signing_key(settings)
    return hmac.new(key,value.encode(),hashlib.sha256).hexdigest() if key else None


def owner_ids(settings):
    try:ids=json.loads(getattr(getattr(settings,'secrets',None),'vxvue_usage_owner_device_ids','[]') or '[]')
    except (ValueError,TypeError):return []
    if not isinstance(ids,list) or not 1<=len(ids)<=100 or any(not isinstance(v,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',v) for v in ids) or len(set(ids))!=len(ids):return []
    return ids


def is_owner(settings,identity):
    if not signing_key(settings) or not identity or identity.get('kind')!='device' or not identity.get('device_id'):return False
    return identity['device_id'] in owner_ids(settings)


def actor_tag(binding,occurred_at):
    return hmac.new(bytes.fromhex(binding),month_value(occurred_at).encode(),hashlib.sha256).hexdigest() if binding else None


def context(settings,identity=None,channel='desktop',activity_id=None,secure=False):
    if not signing_key(settings):return {'enabled':False}
    identity=identity or {};kind=identity.get('kind');identifier=identity.get('device_id') if kind=='device' else identity.get('browser') if kind=='web' else None
    value=f'{kind}|{identifier}' if isinstance(identifier,str) else None
    return {'enabled':True,'channel':channel,'binding':digest(settings,value) if value else None,
            'actor_tag':actor_tag(digest(settings,value),datetime.now(timezone.utc).isoformat()) if value else None,
            'owner_device':is_owner(settings,identity),'owner':is_owner(settings,identity) and secure,'secure':secure,'activity_id':activity_id,'source':'live','root':str(settings.root)}


def store(settings=None,root=None):
    from app.core.config import get_settings
    from app.core.storage import Storage
    path=Path(root) if root is not None else (settings or get_settings()).root
    value=object.__new__(Storage);value.db_path=path/'data/app.db'
    return value


def persist(value,context):
    target=store(root=context.get('root'))
    if not value['activity_id'] and context.get('owner'):
        value['activity_id']=target.usage_active(context.get('binding'))
    target.usage_event(value,binding=context.get('binding'))
    key=str(target.db_path);now=time.monotonic()
    if now-_pruned.get(key,-86400)>=86400:
        target.usage_retention();_pruned[key]=now


def failure():
    global _failures
    with _lock:_failures+=1
    logging.getLogger('uvicorn.error').warning('VXvue 실행 계측 저장 실패. 원문 없이 계속합니다.')


def health():
    return {'failures':_failures,'scope':'현재 프로세스의 계측 실패 수입니다. 재시작되면 초기화되며 별도 웹 실행 프로세스의 누락은 포함하지 않을 수 있습니다.'}


def uuid_or_none(value):
    try:return str(uuid.UUID(str(value))) if value else None
    except (ValueError,TypeError,AttributeError):return None


def execute(operation,*,tool,context=None,metadata=None):
    context=context or {};metadata=metadata or {};started=time.perf_counter();result=None;failed=True
    try:
        result=operation();failed=isinstance(result,dict) and bool(result.get('isError'));return result
    finally:
        if context.get('enabled'):
            try:
                known=tool in _META or tool in _DOCS
                name=tool if known else 'unknown'
                feature,evidence=_META.get(name,('documents','document') if name in _DOCS else ('other','unknown'))
                owner=context.get('owner',False)
                attempt=metadata.get('attempt',1) if owner else 1
                if isinstance(attempt,bool) or not isinstance(attempt,int) or not 1<=attempt<=20:attempt=1
                event_id=uuid_or_none(metadata.get('event_id')) if owner else None
                activity=uuid_or_none(metadata.get('activity_id')) if owner else None
                occurred_at=datetime.now(timezone.utc).isoformat()
                event={'event_id':event_id or str(uuid.uuid4()),'occurred_at':occurred_at,
                       'channel':context.get('channel','desktop'),'feature':feature,'tool':name,'evidence':evidence,
                       'status':'error' if failed else 'success','duration_ms':min(86400000,max(0,int((time.perf_counter()-started)*1000))),
                       'actor_tag':(context.get('actor_tag') if context.get('actor_month')==month_value(occurred_at) else None) if context.get('channel')=='web_chat' else actor_tag(context.get('binding'),occurred_at),'activity_id':activity or context.get('activity_id'),
                       'attempt':attempt,'source':context.get('source','live'),'diagnostic':feature=='diagnostic' or bool(context.get('diagnostic'))}
                persist(event,context)
            except Exception:failure()

def web_activity_id(settings,job_id):
    raw=uuid_or_none(job_id)
    return str(uuid.UUID(hex=digest(settings,'web-job|'+raw)[:32])) if raw and signing_key(settings) else None


def web_task_binding(settings,web_owner,job_id):
    raw=uuid_or_none(job_id)
    stable=digest(settings,'web|'+web_owner)
    return hmac.new(bytes.fromhex(stable),('web-task|'+raw).encode(),hashlib.sha256).hexdigest() if raw and stable else None


def start_web(settings,job):
    usage=context(settings,{'kind':'web','browser':job.get('owner')},'web_chat',web_activity_id(settings,job.get('id')))
    if not usage.get('enabled') or not usage.get('activity_id') or not usage.get('binding'):return usage
    # Only the monthly tag and per-job binding cross the stdio boundary. A job
    # crossing KST months records a null actor rather than reusing a stale tag.
    usage['actor_month']=month_value(datetime.now(timezone.utc).isoformat())
    usage['binding']=web_task_binding(settings,job['owner'],job['id'])
    try:store(settings).usage_start(usage['activity_id'],usage['binding'],'web_chat','other',scope='anonymous')
    except Exception:failure()
    return usage


def finish_web(usage,status):
    if not usage.get('enabled') or not usage.get('activity_id') or not usage.get('binding'):return
    try:store(root=usage.get('root')).usage_finish(usage['activity_id'],usage['binding'],status=status)
    except Exception:failure()
