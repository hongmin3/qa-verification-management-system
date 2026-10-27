"""Hidden creator UI/API. All data endpoints require a signed creator session."""
import json,secrets,sqlite3,uuid
from pathlib import Path
from urllib.parse import parse_qs
from fastapi import APIRouter,HTTPException,Request
from fastapi.responses import HTMLResponse,JSONResponse,RedirectResponse,Response,FileResponse
from fastapi.templating import Jinja2Templates
from app.core.config import get_settings
from . import auth,telemetry,export

router=APIRouter()
templates=Jinja2Templates(directory=[Path(__file__).parent/'templates',Path(__file__).parents[2]/'web/templates'])


def get_storage():return telemetry.store(get_settings())


def storage_call(method,*args,**kwargs):
    try:return getattr(get_storage(),method)(*args,**kwargs)
    except ValueError as exc:raise HTTPException(400,str(exc)) from None
    except (sqlite3.Error,OSError):raise HTTPException(503,'이력 저장소를 사용할 수 없습니다. 잠시 후 다시 확인하세요.') from None


def require(request,write=False):
    return auth.csrf(request,get_settings()) if write else auth.session(request,get_settings())


async def bounded_body(request,limit):
    try:size=int(request.headers.get('content-length','0'))
    except ValueError:raise HTTPException(400,'입력 크기 형식을 확인하세요.') from None
    if size<0 or size>limit:raise HTTPException(413,'입력이 너무 큽니다.')
    raw=bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw)>limit:raise HTTPException(413,'입력이 너무 큽니다.')
    return bytes(raw)


async def body(request,allowed):
    raw=await bounded_body(request,300000)
    try:value=json.loads(raw)
    except (ValueError,UnicodeError):raise HTTPException(400,'JSON 입력을 확인하세요.') from None
    if not isinstance(value,dict) or set(value)-set(allowed):raise HTTPException(400,'허용된 입력 항목을 확인하세요.')
    return value


def private(response):
    response.headers['Cache-Control']='no-store';response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='same-origin'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    return response


@router.get('',response_class=HTMLResponse)
@router.get('/',response_class=HTMLResponse)
def index(request:Request):
    auth.require_transport(request)
    settings=get_settings();logged_in=False
    if auth.configured(settings):
        try:auth.session(request,settings);logged_in=True
        except HTTPException:pass
    token=secrets.token_urlsafe(32)
    response=templates.TemplateResponse(request,'usage.html',{'configured':auth.configured(settings),'logged_in':logged_in,'login_csrf':token})
    if not logged_in:response.set_cookie(auth.PRE_CSRF,token,httponly=True,samesite='strict',secure=auth.transport_is_https(request),max_age=600,path='/vxvue-usage')
    return private(response)


@router.post('/login')
async def login(request:Request):
    auth.require_transport(request)
    auth.same_origin(request)
    raw=await bounded_body(request,16384)
    try:
        parsed=parse_qs(raw.decode('utf-8'),keep_blank_values=True,max_num_fields=3)
        if set(parsed)-{'username','password','csrf'} or any(len(v)!=1 for v in parsed.values()):raise ValueError()
        data={k:v[0] for k,v in parsed.items()}
    except (ValueError,UnicodeError):raise HTTPException(400,'로그인 입력 형식을 확인하세요.') from None
    csrf=data.get('csrf','');expected=request.cookies.get(auth.PRE_CSRF,'')
    import hmac
    if not isinstance(csrf,str) or not expected or not hmac.compare_digest(csrf.encode(),expected.encode()):raise HTTPException(403,'요청 확인값이 맞지 않습니다.')
    if not auth.login_valid(request,get_settings(),data.get('username',''),data.get('password','')):raise HTTPException(403,'로그인 정보를 확인하세요.')
    response=RedirectResponse('/vxvue-usage',status_code=303)
    response.set_cookie(auth.COOKIE,auth.issue_session(get_settings()),httponly=True,samesite='strict',secure=auth.transport_is_https(request),max_age=8*3600,path='/vxvue-usage')
    response.delete_cookie(auth.PRE_CSRF,path='/vxvue-usage')
    return private(response)


@router.post('/logout')
def logout(request:Request):
    require(request,True);response=JSONResponse({'ok':True});response.delete_cookie(auth.COOKIE,path='/vxvue-usage');response.delete_cookie(auth.WEB_COOKIE,path='/vxvue-usage');return private(response)


@router.get('/assets/{name}')
def asset(request:Request,name:str):
    require(request)
    if name not in {'usage.js','usage.css'}:raise HTTPException(404)
    return FileResponse(Path(__file__).parent/'static'/name,media_type='text/javascript' if name.endswith('.js') else 'text/css')


@router.get('/api/session')
def session(request:Request):return private(JSONResponse({'csrf':require(request)['csrf']}))


@router.get('/api/dashboard')
def dashboard(request:Request):
    require(request);value=storage_call('usage_dashboard');value['observation_health']=telemetry.health();return private(JSONResponse(value))


@router.post('/api/activities/search')
async def activities(request:Request):
    require(request,True)
    data=await body(request,{'search','feature','status','bookmark','offset','limit'})
    rows=storage_call('usage_rows',**data)
    for row in rows:row.pop('binding',None)
    return private(JSONResponse(rows))


@router.get('/api/activities/{activity_id}')
def activity(request:Request,activity_id:str):
    require(request);row=storage_call('usage_get',activity_id)
    if not row:raise HTTPException(404,'본인 이력을 찾을 수 없습니다.')
    row.pop('binding',None);return private(JSONResponse(row))


@router.post('/api/activities')
async def create(request:Request):
    require(request,True);data=await body(request,{'question','save_original','feature'})
    activity_id=str(uuid.uuid4());binding=telemetry.digest(get_settings(),'owner-ui')
    storage_call('usage_start',activity_id,binding,'owner_ui',data.pop('feature','other'),**data)
    return private(JSONResponse({'activity_id':activity_id}))


@router.post('/api/activities/{activity_id}/finish')
async def finish(request:Request,activity_id:str):
    require(request,True);data=await body(request,{'answer','summary','status'})
    row=storage_call('usage_get',activity_id)
    if not row:raise HTTPException(404,'본인 이력을 찾을 수 없습니다.')
    storage_call('usage_finish',activity_id,row['binding'],**data);return private(JSONResponse({'ok':True}))


@router.post('/api/activities/{activity_id}/feedback')
async def feedback(request:Request,activity_id:str):
    require(request,True);data=await body(request,{'bookmark','evaluation','baseline_minutes','actual_minutes','portfolio_note'})
    if not storage_call('usage_feedback',activity_id,**data):raise HTTPException(404,'본인 이력을 찾을 수 없습니다.')
    return private(JSONResponse({'ok':True}))


@router.post('/api/retention')
def retention(request:Request):
    require(request,True);return private(JSONResponse(storage_call('usage_retention')))


@router.get('/api/export-preview')
def preview(request:Request,include_notes:bool=False):
    state=require(request);return private(JSONResponse(export.capture(export.preview(get_storage(),include_notes),state['nonce'])))


@router.get('/export.csv')
def download(request:Request,preview_id:str=''):
    state=require(request);value=export.reviewed(preview_id,state['nonce'])
    if value is None:raise HTTPException(400,'유효한 미리보기를 먼저 확인하세요.')
    return private(Response('\ufeff'+export.csv_text(value),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="vxvue-usage.csv"'}))


@router.post('/api/web-ticket')
def web_ticket(request:Request):
    state=require(request,True)
    return private(JSONResponse({'ticket':auth.issue_web_ticket(state)}))


@router.get('/api/web-jobs')
def web_jobs(request:Request):
    from .web import jobs
    return jobs(request,get_settings(),get_storage())


@router.post('/api/web-import/{job_id}')
async def web_import(request:Request,job_id:str):
    from .web import import_job
    return await import_job(request,job_id,get_settings(),get_storage())
