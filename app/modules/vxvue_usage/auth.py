"""Optional creator authentication; signing key is independent of legacy app settings."""
import base64,hashlib,hmac,json,secrets,time,threading
from urllib.parse import urlsplit
from fastapi import HTTPException
from .telemetry import signing_key

COOKIE='vxvue_usage_session'
PRE_CSRF='vxvue_usage_login_csrf'
_limits={}
_lock=threading.Lock()


def password_hash(password,salt=None):
    salt=salt or secrets.token_bytes(16)
    return 'pbkdf2_sha256$600000$'+salt.hex()+'$'+hashlib.pbkdf2_hmac('sha256',password.encode(),salt,600000).hex()


def valid_hash(encoded):
    try:
        algorithm,rounds,salt,digest=encoded.split('$')
        return algorithm=='pbkdf2_sha256' and 100000<=int(rounds)<=1000000 and 16<=len(bytes.fromhex(salt))<=64 and len(bytes.fromhex(digest))==32
    except (ValueError,TypeError,AttributeError):return False


def verify_password(password,encoded):
    if not isinstance(password,str) or len(password)>1024 or not valid_hash(encoded):return False
    _,rounds,salt,digest=encoded.split('$')
    result=hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),int(rounds))
    return hmac.compare_digest(result,bytes.fromhex(digest))


def configured(settings):
    from .telemetry import owner_ids
    if not owner_ids(settings):return False
    value=getattr(settings,'secrets',None)
    return bool(signing_key(settings) and getattr(value,'vxvue_usage_owner_username','') and valid_hash(getattr(value,'vxvue_usage_owner_password_hash','')))


def issue_session(settings):
    payload=base64.urlsafe_b64encode(json.dumps({'exp':int(time.time())+8*3600,'csrf':secrets.token_urlsafe(32),'nonce':secrets.token_hex(16)},separators=(',',':')).encode()).decode().rstrip('=')
    signature=hmac.new(signing_key(settings),payload.encode(),hashlib.sha256).hexdigest()
    return payload+'.'+signature


def session(request,settings):
    require_transport(request)
    if not configured(settings):raise HTTPException(403,'제작자 인증 설정이 필요합니다.')
    token=request.cookies.get(COOKIE,'')
    if len(token)>2048:raise HTTPException(403,'제작자 로그인이 필요합니다.')
    try:
        payload,signature=token.rsplit('.',1)
        expected=hmac.new(signing_key(settings),payload.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature,expected):raise ValueError()
        value=json.loads(base64.urlsafe_b64decode(payload+'='*(-len(payload)%4)))
        if not isinstance(value,dict) or not isinstance(value.get('exp'),int) or not time.time()<value['exp']<=time.time()+8*3600+5 or not isinstance(value.get('csrf'),str) or len(value['csrf'])<32:raise ValueError()
        return value
    except (ValueError,KeyError,TypeError,UnicodeError):raise HTTPException(403,'제작자 로그인이 필요합니다.') from None


def same_origin(request):
    if request.headers.get('sec-fetch-site')=='cross-site':raise HTTPException(403,'다른 사이트의 요청은 허용되지 않습니다.')
    origin=request.headers.get('origin')
    if origin and (urlsplit(origin).netloc!=request.headers.get('host') or urlsplit(origin).scheme not in ('http','https')):raise HTTPException(403,'다른 사이트의 요청은 허용되지 않습니다.')


def csrf(request,settings):
    value=session(request,settings);same_origin(request)
    token=request.headers.get('x-csrf-token','')
    if not token or not hmac.compare_digest(token.encode(),value['csrf'].encode()):raise HTTPException(403,'요청 확인값이 맞지 않습니다.')
    return value


def reset_rate_limits():
    with _lock:_limits.clear()


def rate_key(request,settings):
    from app.modules.vxvue_chatbot.desktop_enroll import client_address
    host=client_address(request)
    return hmac.new(signing_key(settings),host.encode(),hashlib.sha256).hexdigest()


def check_rate(request,settings):
    key=rate_key(request,settings)
    with _lock:
        now=time.monotonic()
        for old in list(_limits):
            if now-_limits[old][0]>=300:_limits.pop(old,None)
        if _limits.get(key,(now,0))[1]>=5:raise HTTPException(429,'잠시 후 다시 로그인하세요.')
        if len(_limits)>=1024 and key not in _limits:raise HTTPException(429,'잠시 후 다시 로그인하세요.')
    return key


def login_valid(request,settings,username,password):
    require_transport(request)
    if not configured(settings):raise HTTPException(403,'제작자 인증 설정이 필요합니다.')
    key=check_rate(request,settings)
    valid=isinstance(username,str) and len(username)<=64 and hmac.compare_digest(username.encode(),settings.secrets.vxvue_usage_owner_username.encode())
    valid=verify_password(password,settings.secrets.vxvue_usage_owner_password_hash) and valid
    with _lock:
        if valid:_limits.pop(key,None)
        else:
            start,count=_limits.get(key,(time.monotonic(),0));_limits[key]=(start,count+1)
    return valid


def _loopback_peer(request):
    import ipaddress
    try:return bool(request.client and ipaddress.ip_address(request.client.host).is_loopback)
    except ValueError:return False


def _hosts(request):
    values=[v.decode('latin1') for k,v in request.scope.get('headers',[]) if k.lower()==b'host']
    if len(values)!=1:return None
    value=values[0]
    if any(c in value for c in ('@','/','\\','?', '#',',',' ','\t','\r','\n')):return None
    try:
        parsed=urlsplit('http://'+value)
        parsed.port
        return parsed.hostname
    except ValueError:return None


def transport_is_https(request):
    if request.scope.get('scheme')=='https':return True
    if not _loopback_peer(request):return False
    values=[v.decode('latin1') for k,v in request.scope.get('headers',[]) if k.lower()==b'x-forwarded-proto']
    return values==['https']


def secure_transport(request):
    host=_hosts(request)
    if not host:return False
    if transport_is_https(request):return True
    if not _loopback_peer(request):return False
    if any(k.lower().startswith(b'x-forwarded-') or k.lower() in (b'x-real-ip',b'forwarded') for k,v in request.scope.get('headers',[])):return False
    return host in ('localhost','127.0.0.1','::1')


def require_transport(request):
    if not secure_transport(request):raise HTTPException(403,'제작자 기능은 HTTPS 또는 보호된 로컬 연결에서 사용하세요.')

WEB_COOKIE='vxvue_usage_browser_binding'
_tickets={}


def issue_web_ticket(state):
    token=secrets.token_urlsafe(32);key=hashlib.sha256(token.encode()).hexdigest()
    with _lock:
        now=time.monotonic()
        for old in list(_tickets):
            if now-_tickets[old][0]>60:_tickets.pop(old,None)
        if len(_tickets)>=64:_tickets.pop(next(iter(_tickets)))
        _tickets[key]=(now,dict(state))
    return token


def consume_web_ticket(request):
    require_transport(request);same_origin(request)
    token=request.headers.get('x-vxvue-owner-ticket','')
    if len(token)>128:raise HTTPException(403,'웹 연결 확인값이 맞지 않습니다.')
    key=hashlib.sha256(token.encode()).hexdigest()
    with _lock:value=_tickets.pop(key,None)
    if not value or time.monotonic()-value[0]>60 or value[1]['exp']<=time.time():raise HTTPException(403,'웹 연결 확인값이 만료되었거나 사용되었습니다.')
    csrf=request.headers.get('x-csrf-token','')
    if not hmac.compare_digest(csrf.encode(),value[1]['csrf'].encode()):raise HTTPException(403,'요청 확인값이 맞지 않습니다.')
    return value[1]


def web_proof(settings,web_owner,state):
    payload=base64.urlsafe_b64encode(json.dumps({'owner':web_owner,'nonce':state['nonce'],'exp':state['exp']},separators=(',',':')).encode()).decode().rstrip('=')
    signature=hmac.new(signing_key(settings),('web-proof|'+payload).encode(),hashlib.sha256).hexdigest()
    return payload+'.'+signature


def read_web_proof(request,settings,state):
    token=request.cookies.get(WEB_COOKIE,'')
    try:
        if len(token)>2048:raise ValueError()
        payload,signature=token.rsplit('.',1)
        expected=hmac.new(signing_key(settings),('web-proof|'+payload).encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature,expected):raise ValueError()
        value=json.loads(base64.urlsafe_b64decode(payload+'='*(-len(payload)%4)))
        import re
        if not isinstance(value,dict) or value.get('nonce')!=state['nonce'] or not isinstance(value.get('exp'),int) or not time.time()<value['exp']<=state['exp'] or not isinstance(value.get('owner'),str) or not re.fullmatch('[0-9a-f]{64}',value['owner']):raise ValueError()
        return value['owner']
    except (ValueError,TypeError,KeyError,UnicodeError):raise HTTPException(403,'현재 보호 연결의 본인 웹 대화를 먼저 연결하세요.') from None
