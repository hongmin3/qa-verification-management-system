"""Creator-only loopback SSH connection. Existing shared QA connections stay unchanged.

Configuration: ~/.vxvue-ai/owner-transport.json (no password/token in it).
The existing per-device token is read from credentials.json only when posting over SSH.
"""
import argparse
import csv
from dataclasses import asdict,dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import webbrowser

HOME=Path.home()/'.vxvue-ai'
OWNER_TOOLS={'begin_vxvue_activity','finish_vxvue_activity'}

@dataclass(frozen=True)
class Config:
    server_url:str
    ssh_host:str
    ssh_user:str
    local_port:int=24359
    remote_port:int=24357

def load_config(path=None):
    try:
        raw=json.loads(Path(path or HOME/'owner-transport.json').read_text(encoding='utf-8-sig'))
        cfg=Config(**raw)
        parts=urlsplit(cfg.server_url)
        if parts.scheme not in ('http','https') or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment or parts.path not in ('','/'):
            raise ValueError()
        for value in (cfg.local_port,cfg.remote_port):
            if isinstance(value,bool) or not isinstance(value,int) or not 1024<=value<=65535:raise ValueError()
        if not isinstance(cfg.ssh_user,str) or not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_-]{0,63}',cfg.ssh_user):raise ValueError()
        if not isinstance(cfg.ssh_host,str) or len(cfg.ssh_host)>253 or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9.:-]*',cfg.ssh_host):raise ValueError()
        if parts.hostname.casefold()!=cfg.ssh_host.casefold() or (parts.port or (443 if parts.scheme=='https' else 80))!=cfg.remote_port:raise ValueError()
        return cfg
    except (OSError,ValueError,TypeError,AttributeError):
        raise ValueError('본인 보호 연결 설정을 확인하세요.') from None

def ssh_arguments(cfg):
    forward=f'127.0.0.1:{cfg.local_port}:127.0.0.1:{cfg.remote_port}'
    return ['-N','-T','-o','BatchMode=yes','-o','ExitOnForwardFailure=yes','-o','ConnectTimeout=10',
            '-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3','-L',forward,'-l',cfg.ssh_user,cfg.ssh_host]

def is_listening(port):
    try:
        with socket.create_connection(('127.0.0.1',port),timeout=.3):return True
    except OSError:return False

def process_is_ssh(pid):
    if isinstance(pid,bool) or not isinstance(pid,int) or pid<1:return False
    try:
        if os.name=='nt':
            result=subprocess.run(['tasklist','/FI',f'PID eq {pid}','/FO','CSV','/NH'],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=5)
            return any(len(row)>1 and row[0].casefold()=='ssh.exe' and row[1]==str(pid) for row in csv.reader(result.stdout.splitlines()))
        result=subprocess.run(['ps','-p',str(pid),'-o','comm='],capture_output=True,text=True,timeout=5)
        return result.returncode==0 and Path(result.stdout.strip()).name=='ssh'
    except (OSError,subprocess.SubprocessError):return False

def spawn_tunnel(cfg,state_dir):
    log=open(Path(state_dir)/'owner-tunnel.log','ab')
    options={'stdin':subprocess.DEVNULL,'stdout':subprocess.DEVNULL,'stderr':log,'close_fds':True}
    if os.name=='nt':options['creationflags']=subprocess.CREATE_NO_WINDOW|subprocess.CREATE_NEW_PROCESS_GROUP
    else:options['start_new_session']=True
    try:return subprocess.Popen(['ssh',*ssh_arguments(cfg)],**options).pid
    finally:log.close()

def _state(path):
    try:
        value=json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):return {}

def ensure_tunnel(cfg,state_dir=None):
    state_dir=Path(state_dir or HOME);state_dir.mkdir(parents=True,exist_ok=True)
    pid_file=state_dir/'owner-tunnel.json';lock=state_dir/'owner-tunnel.lock'
    fingerprint=hashlib.sha256(json.dumps(asdict(cfg),sort_keys=True).encode()).hexdigest()
    def ours():
        state=_state(pid_file)
        return state.get('configuration')==fingerprint and process_is_ssh(state.get('pid'))
    if is_listening(cfg.local_port):
        if ours():return
        raise ValueError('보호 연결 포트를 다른 프로그램이 사용 중입니다. 기존 프로그램은 변경하지 않았습니다.')
    deadline=time.monotonic()+15
    while True:
        try:fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);break
        except FileExistsError:
            if is_listening(cfg.local_port) and ours():return
            if time.monotonic()>deadline:raise ValueError('보호 연결을 준비 중입니다. 잠시 후 다시 시도하세요.') from None
            try:
                if time.time()-lock.stat().st_mtime>30:lock.unlink(missing_ok=True)
            except OSError:pass
            time.sleep(.1)
    try:
        os.close(fd)
        if is_listening(cfg.local_port):
            if ours():return
            raise ValueError('보호 연결 포트를 다른 프로그램이 사용 중입니다.')
        try:pid=spawn_tunnel(cfg,state_dir)
        except OSError:raise ValueError('SSH 보호 연결을 시작하지 못했습니다. 기존 SSH 접속 설정을 확인하세요.') from None
        temp=pid_file.with_suffix('.tmp')
        temp.write_text(json.dumps({'pid':pid,'configuration':fingerprint}),encoding='utf-8');os.replace(temp,pid_file)
        while time.monotonic()<deadline:
            if is_listening(cfg.local_port) and process_is_ssh(pid):return
            time.sleep(.15)
        raise ValueError('SSH 보호 연결에 응답이 없습니다. 본인 연결 설정을 확인하세요.')
    finally:lock.unlink(missing_ok=True)

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def read_token(cfg,credential_file=None):
    try:
        data=json.loads(Path(credential_file or HOME/'credentials.json').read_text(encoding='utf-8-sig'))
        value=data[cfg.server_url.rstrip('/')]['token']
        if not isinstance(value,str) or not 32<=len(value)<=512:raise ValueError()
        return value
    except (OSError,ValueError,KeyError,TypeError):raise ValueError('이 PC의 기존 VXvue 연결 등록을 확인하세요.') from None

def call_owner(name,arguments,cfg=None,*,credential_file=None,opener=None):
    if name not in OWNER_TOOLS or not isinstance(arguments,dict):raise ValueError('본인 선택 업무 도구만 사용할 수 있습니다.')
    cfg=cfg or load_config();ensure_tunnel(cfg)
    token=read_token(cfg,credential_file)
    body=json.dumps({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':name,'arguments':arguments}},ensure_ascii=False).encode('utf-8')
    if len(body)>256*1024:raise ValueError('선택한 업무 내용이 허용 크기를 넘었습니다.')
    opener=opener or urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    def exchange(payload):
        request=urllib.request.Request(f'http://127.0.0.1:{cfg.local_port}/vxvue-desktop/rpc',data=payload,
            headers={'Content-Type':'application/json','Authorization':'Bearer '+token,'X-VXvue-Bridge':'1.2.1'},method='POST')
        with opener.open(request,timeout=15) as response:
            data=response.read(1024*1024+1)
        if len(data)>1024*1024:raise ValueError()
        result=json.loads(data)
        if not isinstance(result,dict) or result.get('error') or not isinstance(result.get('result'),dict):raise ValueError()
        return result['result']
    try:
        # Never send selected originals after a failed owner-permission check.
        # The server also rechecks permissions on tools/call if they change meanwhile.
        preflight=exchange(json.dumps({'jsonrpc':'2.0','id':0,'method':'tools/list','params':{}}).encode())
        allowed=preflight.get('tools')
        if not isinstance(allowed,list) or not OWNER_TOOLS.issubset({item.get('name') for item in allowed if isinstance(item,dict) and isinstance(item.get('name'),str)}):raise ValueError()
        return exchange(body)
    except (OSError,ValueError,urllib.error.URLError):
        raise ValueError('본인 보호 연결에서 업무를 처리하지 못했습니다. 서버 연결을 확인하세요.') from None

def serve(cfg):
    from mcp.server.fastmcp import FastMCP
    from mcp.types import CallToolResult,TextContent,ToolAnnotations
    server=FastMCP('vxvue-my-history',instructions='본인이 선택한 VXvue 업무의 질문과 최종 답변·활용 요약만 보관하는 개인 연결입니다. '
        '일반 QA 조회는 기존 VXvue 연결을 사용합니다. 선택 업무 시작·종료에 아래 두 도구를 사용하고 기록 안내를 답변에 덧붙이지 않습니다. '
        '확인되지 않은 성공·절약 시간을 만들지 않으며 완료하지 못하면 INCOMPLETE·FAILED로 남깁니다.')
    annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=False,openWorldHint=False)
    def invoke(name,args):
        try:return CallToolResult(**call_owner(name,args,cfg))
        except ValueError as error:return CallToolResult(content=[TextContent(type='text',text=str(error))],isError=True)
    @server.tool(annotations=annotations)
    def begin_vxvue_activity(activity_id:str|None=None,feature:str='other',question:str='',save_original:bool=False)->CallToolResult:
        """본인이 선택한 VXvue 업무를 시작한다. 원문 선택 저장은 save_original=True로 요청한다. 재시도는 같은 activity_id를 사용한다."""
        args={'feature':feature,'question':question,'save_original':save_original}
        if activity_id is not None:args['activity_id']=activity_id
        return invoke('begin_vxvue_activity',args)
    @server.tool(annotations=annotations)
    def finish_vxvue_activity(activity_id:str,answer:str='',summary:str='',status:str='ANSWER_RECORDED')->CallToolResult:
        """선택한 본인 업무의 최종 답변·활용 요약 또는 미완료/실패를 남긴다."""
        return invoke('finish_vxvue_activity',{'activity_id':activity_id,'answer':answer,'summary':summary,'status':status})
    server.run(transport='stdio')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['serve','open','ensure'])
    parser.add_argument('--config',type=Path)
    args=parser.parse_args();cfg=load_config(args.config)
    if args.action=='serve':serve(cfg)
    else:
        ensure_tunnel(cfg)
        if args.action=='open':webbrowser.open(f'http://127.0.0.1:{cfg.local_port}/vxvue-usage')

if __name__=='__main__':
    try:main()
    except ValueError as error:
        print(str(error),file=sys.stderr);raise SystemExit(1)
