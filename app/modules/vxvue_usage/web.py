"""Optional, explicit import of a currently authenticated browser's own chat job."""
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from . import auth,telemetry
from .router import body,private


def checked(request,settings,store,write=False):
    state=(auth.csrf if write else auth.session)(request,settings)
    web_owner=auth.read_web_proof(request,settings,state)
    binding=telemetry.digest(settings,'web|'+web_owner)
    return web_owner,binding


def bind(request,settings,store,owner):
    state=auth.consume_web_ticket(request)
    web_owner=owner(request);binding=telemetry.digest(settings,'web|'+web_owner)
    store.usage_bind_web(binding)
    response=private(JSONResponse({'ok':True}))
    response.set_cookie(auth.WEB_COOKIE,auth.web_proof(settings,web_owner,state),httponly=True,samesite='strict',secure=auth.transport_is_https(request),max_age=8*3600,path='/vxvue-usage')
    return response


def jobs(request,settings,store):
    web_owner,binding=checked(request,settings,store)
    if not store.usage_web_bound(binding):raise HTTPException(403,'현재 브라우저의 본인 대화 연결을 먼저 선택하세요.')
    return private(JSONResponse(store.usage_web_jobs(web_owner)))


async def import_job(request,job_id,settings,store):
    web_owner,binding=checked(request,settings,store,True)
    if not store.usage_web_bound(binding):raise HTTPException(403,'현재 브라우저의 본인 대화 연결을 먼저 선택하세요.')
    job=store.vxchat_job(web_owner,job_id)
    if not job:raise HTTPException(404,'이 브라우저의 웹 작업을 찾을 수 없습니다.')
    data=await body(request,{'save_original','summary','feature'})
    chat=store.vxchat_get(web_owner,job['conversation_id'])
    messages={m['id']:m['content'] for m in chat['messages']}
    question=messages.get(job['user_message_id'],'');answer=messages.get(job['assistant_message_id'],'')
    status='ANSWER_RECORDED' if job['status']=='DONE' and answer.strip() else 'CANCELLED' if job['status']=='CANCELLED' else 'FAILED' if job['status']=='FAILED' else 'INCOMPLETE'
    try:activity_id=store.usage_import_web(telemetry.web_activity_id(settings,job['id']),telemetry.web_task_binding(settings,web_owner,job['id']),question,answer,status,created_at=job['created_at'],**data)
    except ValueError as exc:raise HTTPException(400,str(exc)) from None
    return private(JSONResponse({'activity_id':activity_id}))
