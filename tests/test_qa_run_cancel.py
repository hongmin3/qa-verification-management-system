"""Validates: REQ-QAINTEL-036. 외부 AI·Polarion·메일을 부르지 않는다."""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.daily_qa.store import DailyQaStore
from tests.daily_qa_fixtures import make_settings
from tests.test_qa_intel_pipeline import DAY1, DAY2, env, analysis_producer  # noqa: F401


@pytest.fixture
def cancel_world(tmp_path, monkeypatch):
    from app.modules.qa_agent import dashboard as dash
    from app.modules.qa_agent import router as qa_router
    from app.modules.daily_qa import cancellation
    root = tmp_path / 'repo'
    root.mkdir()
    cfg = make_settings(root, tmp_path / 'ws')
    store = DailyQaStore(cfg.db_path)
    run_id = '20261013-123000-vxvue'
    store.create_run(run_id, False, product=cfg.slug)
    store.update_progress(run_id, {'phase': 'ai', 'label': 'AI 분석', 'done': 4, 'total': 18})
    cfg.lock_path.parent.mkdir(parents=True, exist_ok=True)
    owner = {'pid': 12345, 'start': 'instance-one', 'run_id': run_id, 'product': cfg.slug}
    cfg.lock_path.write_text(json.dumps(owner), encoding='utf-8')
    identity = {'pid': 12345, 'start': 'instance-one', 'executable': '/usr/bin/python3',
                'argv': ['/usr/bin/python3', str(root / 'scripts/run_daily_qa.py'), '--product', cfg.product]}
    monkeypatch.setattr(cancellation, 'process_identity', lambda *args, **kwargs: dict(identity))
    choice = dash.ProductChoice(cfg, store, [cfg.product])
    monkeypatch.setattr(dash, 'choose', lambda product='': choice if product in ('', cfg.product, cfg.slug) else None)
    app = FastAPI()
    app.include_router(qa_router.router, prefix='/qa-agent')
    return {'cfg': cfg, 'store': store, 'run_id': run_id, 'client': TestClient(app), 'identity': identity}


def test_current_run_cancel_is_idempotent(cancel_world):
    w = cancel_world
    url = f'/qa-agent/runs/{w["run_id"]}/cancel?product=VXvue'
    response = w['client'].post(url)
    assert response.status_code == 202
    assert response.json()['status'] == 'cancelling'
    requested = w['store'].get_run(w['run_id'])['cancel_requested_at']
    assert requested
    assert w['client'].post(url).status_code == 202
    assert w['store'].get_run(w['run_id'])['cancel_requested_at'] == requested
    status = w['client'].get('/qa-agent/status?product=VXvue').json()
    assert status['current_run_id'] == w['run_id'] and status['cancel_requested']
    assert status['progress']['done'] == 4 and status['progress']['total'] == 18
    w['store'].finish_run(w['run_id'], 'CANCELLED', {}, {})
    assert w['client'].post(url).status_code == 200


@pytest.mark.parametrize('change', ['pid-reused', 'wrong-script', 'wrong-product-command', 'wrong-lock-run', 'ended', 'email'])
def test_cancel_rejects_other_processes_and_finished_runs(cancel_world, change):
    w = cancel_world
    if change == 'pid-reused':
        w['identity']['start'] = 'instance-two'
    elif change == 'wrong-script':
        w['identity']['argv'][1] = str(w['cfg'].root / 'other.py')
    elif change == 'wrong-product-command':
        w['identity']['argv'][-1] = 'Another product'
    elif change == 'wrong-lock-run':
        owner = json.loads(w['cfg'].lock_path.read_text())
        owner['run_id'] = 'another-run'
        w['cfg'].lock_path.write_text(json.dumps(owner), encoding='utf-8')
    elif change == 'ended':
        w['store'].finish_run(w['run_id'], 'SUCCESS', {}, {})
    elif change == 'email':
        w['store'].update_progress(w['run_id'], {'phase': 'email'})
    response = w['client'].post(f'/qa-agent/runs/{w["run_id"]}/cancel?product=VXvue')
    assert response.status_code == 409
    assert not w['store'].get_run(w['run_id'])['cancel_requested_at']


def test_missing_or_other_product_cancel_is_not_found(cancel_world):
    w = cancel_world
    assert w['client'].post('/qa-agent/runs/missing/cancel?product=VXvue').status_code == 404
    assert w['client'].post(f'/qa-agent/runs/{w["run_id"]}/cancel?product=Unknown').status_code == 404


def test_cancel_and_email_claim_are_mutually_exclusive(cancel_world):
    w = cancel_world
    assert w['store'].request_cancel(w['run_id'])
    assert not w['store'].begin_email(w['run_id'], {'phase': 'email'}, {})
    other = '20261013-123001-vxvue'
    w['store'].create_run(other, False, product='vxvue')
    assert w['store'].begin_email(other, {'phase': 'email'}, {})
    assert not w['store'].request_cancel(other)


def test_native_process_identity_identifies_this_python():
    from app.modules.daily_qa.cancellation import process_identity
    identity = process_identity(os.getpid())
    assert identity['pid'] == os.getpid() and identity['start']
    assert 'python' in identity['executable'].lower()
    assert identity['argv']


@pytest.mark.parametrize('cli_started', [True, False])
def test_pipeline_cancel_preserves_results_counts_and_pending_events(env, monkeypatch, cli_started):
    from dataclasses import replace
    from app.modules.daily_qa.agent_runner import FakeRunner, RunnerOutcome
    from app.modules.daily_qa import pipeline
    from tests.daily_qa_fixtures import issue_item
    env['run'](DAY1)
    env['cfg'] = replace(env['cfg'], batch_size=1)
    # fixture 의 run closure 에 있는 cfg 도 인자 경계에서 바꾼다.
    original_run = pipeline.run_daily
    mails = []
    def execute(cfg, **kwargs):
        kwargs['send_email'] = lambda subject, *_: ("변경 리포트" in subject or mails.append('sent')) or {'status': 'sent'}
        return original_run(replace(cfg, batch_size=1), **kwargs)
    monkeypatch.setattr('tests.test_qa_intel_pipeline.run_daily', execute)
    env['state']['issues'] += [issue_item(f'VP-{n}', '2026-09-29T09:00:00Z', ['VP-10'], review='', status='open') for n in (201, 202, 203)]
    producer = analysis_producer(env['calls'])
    called = []
    def produce(task, workspace):
        called.append(task.task_id)
        if len(called) == 1:
            producer(task, workspace)
        else:
            assert env['store'].request_cancel(workspace.run_id)
            return RunnerOutcome(False, -1, .1, '사용자 중단', meta={} if cli_started else {'cli_started': False}, cancelled=True)
    outcome = env['run'](DAY2, runner=FakeRunner(produce))
    assert outcome['status'] == 'CANCELLED'
    row = env['store'].get_run(outcome['run_id'])
    assert row['progress']['done'] == 1 and row['progress']['total'] == 3
    assert row['progress']['phase'] == 'cancelled'
    assert len(env['store'].list_findings(run_id=outcome['run_id'])) == 1
    events = env['store'].list_events(run_id=outcome['run_id'])
    assert [event['analysis_status'] for event in events].count('done') == 1
    assert [event['analysis_status'] for event in events].count('pending') == 2
    assert all(event['attempts'] == 0 for event in events)
    assert not mails and row['email_status'] == 'skipped'
    assert row['summary']['claude_calls'] == (2 if cli_started else 1)
    assert row['summary']['token_usage'].get('usage_unknown_calls', 0) == (1 if cli_started else 0)
    assert not env['cfg'].lock_path.exists()
    assert (env['cfg'].srs_snapshot_dir / '2026-09-30.json').exists()
    audit = json.loads((env['cfg'].output_dir / outcome['run_id'] / 'audit.json').read_text(encoding='utf-8'))
    assert audit['cancelled']['reason'] and audit['cancelled']['done'] == 1


def test_runner_cooperative_cancel_terminates_only_owned_child(tmp_path, monkeypatch):
    from app.modules.daily_qa import agent_runner
    from app.modules.daily_qa.cancellation import RunCancelled
    from app.modules.daily_qa.packages import Task
    from app.modules.daily_qa.workspace import prepare
    workspace = prepare(tmp_path / 'ws', tmp_path / 'repo', 'cancel-native', None, None)
    unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)'])
    monkeypatch.setattr(agent_runner, 'build_command', lambda *args, **kwargs: [sys.executable, '-c', 'import time; time.sleep(20)'])
    # Windows 파이프 버퍼보다 큰 입력. 자식이 stdin 을 읽지 않아도 취소를 확인한다.
    monkeypatch.setattr(agent_runner, 'build_prompt', lambda *args: '합성 입력\n' * 10000)
    started = time.monotonic()
    cancelled_at = []
    def check():
        if time.monotonic() - started > .5:
            cancelled_at.append(time.monotonic())
            raise RunCancelled('사용자 중단')
    try:
        runner = agent_runner.ClaudeRunner('unused', '', 10, cancel_check=check)
        outcome = runner.run(Task('TASK-1', 'qa-new-issue-analysis', {}), workspace)
        assert outcome.cancelled and not outcome.ok
        # 취소 확인은 입력 파이프가 막혀도 빠르다. Windows taskkill/핸들/출력/쓰기 스레드 정리는 별도로 제한한다.
        assert cancelled_at and cancelled_at[0] - started < 2
        assert time.monotonic() - cancelled_at[0] < 5 + 2 + 2 + 2
        assert unrelated.poll() is None
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=5)


def test_runner_cancel_before_popen_records_no_cli_start(tmp_path, monkeypatch):
    from app.modules.daily_qa import agent_runner
    from app.modules.daily_qa.cancellation import RunCancelled
    from app.modules.daily_qa.packages import Task
    from app.modules.daily_qa.workspace import prepare
    workspace = prepare(tmp_path / 'ws', tmp_path / 'repo', 'cancel-before-start', None, None)
    def check(): raise RunCancelled('사용자 중단')
    monkeypatch.setattr(agent_runner.subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('취소된 실행이 CLI 를 시작했습니다.'))
    outcome = agent_runner.ClaudeRunner('unused', '', 10, cancel_check=check).run(
        Task('TASK-1', 'qa-new-issue-analysis', {}), workspace)
    assert outcome.cancelled and outcome.meta.get('cli_started') is False


def test_polarion_cancel_stops_before_next_comment_page(tmp_path):
    from dataclasses import replace
    from app.modules.daily_qa.polarion import ReadOnlyPolarionClient
    from app.modules.daily_qa.cancellation import RunCancelled
    cfg = make_settings(tmp_path, tmp_path / 'ws')
    cancelled, requests = [], []
    def check():
        if cancelled:
            raise RunCancelled('사용자 중단')
    def transport(url, params):
        requests.append(params['page[number]'])
        cancelled.append(True)
        return {'data': [{'id': 'comment-1'}], 'links': {'next': 'next'}}
    client = ReadOnlyPolarionClient(replace(cfg.polarion, host='https://fixture.invalid', token='fixture'), transport, cancel_check=check)
    with pytest.raises(RunCancelled):
        client.get_comments('VP-1', strict=True)
    assert requests == [1]


def test_cancel_actual_http_and_native_worker(tmp_path, monkeypatch):
    """실제 TCP HTTP·OS 프로세스·SQLite. 합성 worker이며 외부 서비스는 부르지 않는다."""
    import socket
    import threading
    import httpx
    import uvicorn
    from app.modules.qa_agent import dashboard as dash
    from app.modules.qa_agent import router as qa_router
    root = tmp_path / 'repo'
    (root / 'scripts').mkdir(parents=True)
    cfg = make_settings(root, tmp_path / 'ws')
    store = DailyQaStore(cfg.db_path)
    run_id = '20261013-123501-vxvue'
    worker_script = root / 'scripts/run_daily_qa.py'
    source_root = str(__import__('pathlib').Path(__file__).resolve().parents[1])
    worker_script.write_text(
        "import sys,time\nfrom pathlib import Path\n"
        f"sys.path.insert(0, {source_root!r})\n"
        "from app.modules.daily_qa.store import DailyQaStore\n"
        "from app.modules.daily_qa.pipeline import _Lock\n"
        f"store=DailyQaStore(Path({str(cfg.db_path)!r}))\nrun_id={run_id!r}\n"
        f"with _Lock(Path({str(cfg.lock_path)!r})) as lock:\n"
        " store.create_run(run_id,False,'vxvue')\n lock.bind(run_id,'vxvue')\n"
        " store.update_progress(run_id,{'phase':'ai','label':'AI 분석','done':4,'total':18})\n"
        " while not store.get_run(run_id)['cancel_requested_at']: time.sleep(.05)\n"
        " time.sleep(.3)\n store.set_email_status(run_id,'skipped')\n"
        " store.finish_run(run_id,'CANCELLED',{}, {})\n"
        " store.update_progress(run_id,{'phase':'cancelled','label':'사용자 중단','done':4,'total':18})\n",
        encoding='utf-8')
    choice = dash.ProductChoice(cfg, store, [cfg.product])
    monkeypatch.setattr(dash, 'choose', lambda product='': choice if product in ('', cfg.product, cfg.slug) else None)
    app = FastAPI()
    app.include_router(qa_router.router, prefix='/qa-agent')
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(128)
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', lifespan='off', proxy_headers=False, ws='none'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    worker = subprocess.Popen([sys.executable, str(worker_script), '--product', 'VXvue'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 60
        while not server.started or not (store.get_run(run_id) or {}).get('progress'):
            assert worker.poll() is None
            assert time.monotonic() < deadline
            time.sleep(.05)
        base = f'http://127.0.0.1:{listener.getsockname()[1]}'
        from app.modules.daily_qa.cancellation import process_identity, validate_owner
        owner = json.loads(cfg.lock_path.read_text(encoding='utf-8'))
        assert validate_owner(cfg, run_id), {'owner': owner, 'identity': process_identity(owner['pid'])}
        with httpx.Client(base_url=base, timeout=15) as client:
            response = client.post(f'/qa-agent/runs/{run_id}/cancel?product=VXvue')
            assert response.status_code == 202, response.text
            assert response.json()['status'] == 'cancelling'
            assert store.get_run(run_id)['cancel_requested_at']
            worker.wait(timeout=5)
            stdout, stderr = worker.communicate()
            assert worker.returncode == 0, stderr.decode(errors='replace')
            status = client.get('/qa-agent/status?product=VXvue').json()
            assert not status['running'] and status['last_run']['status'] == 'CANCELLED'
            assert status['progress']['done'] == 4 and status['progress']['total'] == 18
            assert store.get_run(run_id)['email_status'] == 'skipped'
            assert client.post(f'/qa-agent/runs/{run_id}/cancel?product=VXvue').status_code == 200
            detail = client.get(f'/qa-agent/runs/{run_id}?product=VXvue')
            assert detail.status_code == 200 and '사용자 중단' in detail.text
            assert not cfg.lock_path.exists()
    finally:
        if worker.poll() is None:
            worker.terminate()
            worker.wait(timeout=5)
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()


def test_linux_child_group_termination_uses_only_owned_pid(monkeypatch):
    """Linux 분기 계약 시험. Windows 호스트에서 Linux 실행 증거로 세지 않는다."""
    from app.modules.daily_qa import agent_runner
    calls = []
    class Child:
        pid = 4567
        def poll(self):
            # 부모가 먼저 끝나도 손자의 열린 stdout 때문에 communicate 는 대기한다.
            return 0
        def communicate(self, timeout):
            calls.append(('communicate', timeout))
            if len([call for call in calls if call[0] == 'communicate']) == 1:
                raise subprocess.TimeoutExpired('owned', timeout)
            return 'partial output', ''
    monkeypatch.setattr(agent_runner.sys, 'platform', 'linux')
    monkeypatch.setattr(agent_runner.signal, 'SIGKILL', 9, raising=False)
    monkeypatch.setattr(agent_runner.os, 'killpg', lambda pid, signal: calls.append(('killpg', pid, signal)), raising=False)
    agent_runner._stop_owned_child(Child())
    assert calls == [('killpg', 4567, agent_runner.signal.SIGTERM), ('communicate', 2),
                     ('killpg', 4567, agent_runner.signal.SIGKILL), ('communicate', 2)]


def test_lock_cleanup_preserves_replacement_owner(tmp_path):
    from app.modules.daily_qa.pipeline import _Lock
    path = tmp_path / 'run.lock'
    with _Lock(path) as lock:
        lock.bind('run-one', 'vxvue')
        path.write_text('{"run_id":"replacement-owner"}', encoding='utf-8')
    assert path.read_text(encoding='utf-8') == '{"run_id":"replacement-owner"}'


def test_windows_taskkill_timeout_still_closes_owned_handle(monkeypatch):
    from app.modules.daily_qa import agent_runner
    calls = []
    class Child:
        pid = 4567
        def poll(self): return None
        def wait(self, timeout):
            calls.append(('wait', timeout))
            if len(calls) == 1: raise subprocess.TimeoutExpired('owned', timeout)
            return -9
        def kill(self): calls.append(('kill', self.pid))
    def timed_out(*args, **kwargs): raise subprocess.TimeoutExpired('taskkill', 5)
    monkeypatch.setattr(agent_runner.sys, 'platform', 'win32')
    monkeypatch.setattr(agent_runner.subprocess, 'CREATE_NO_WINDOW', 0, raising=False)
    monkeypatch.setattr(agent_runner.subprocess, 'run', timed_out)
    agent_runner._stop_owned_child(Child())
    assert calls == [('wait', 2), ('kill', 4567), ('wait', 2)]


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows native handle 확인')
def test_windows_external_command_and_process_identity():
    from app.modules.daily_qa import cancellation
    child = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'])
    try:
        identity = cancellation.process_identity(child.pid)
        assert identity and identity['pid'] == child.pid and identity['start']
        assert identity['argv'][1:] == ['-c', 'import time;time.sleep(60)']
    finally:
        child.terminate()
        child.wait(timeout=5)


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows native command-line 확인')
def test_windows_external_command_identity_avoids_slow_helper_process(monkeypatch):
    """Validates: REQ-QAINTEL-036. 명령 조회에 PowerShell을 시작하지 않는다."""
    from app.modules.daily_qa import cancellation
    child = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)', '공백 있는 인자'])
    try:
        def forbidden(*args, **kwargs):
            pytest.fail('process identity started an external command-line helper')
        monkeypatch.setattr(cancellation.subprocess, 'run', forbidden)
        identity = cancellation.process_identity(child.pid)
        assert identity and identity['pid'] == child.pid and identity['start']
        assert identity['argv'][1:] == ['-c', 'import time;time.sleep(60)', '공백 있는 인자']
    finally:
        child.terminate()
        child.wait(timeout=5)


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows CIM 대체 경로 확인')
def test_windows_identity_keeps_cim_when_native_memory_read_is_unavailable(monkeypatch):
    from app.modules.daily_qa import cancellation
    args = [sys.executable, '-c', 'import time;time.sleep(60)']
    child = subprocess.Popen(args)
    calls = []
    try:
        monkeypatch.setattr(cancellation, '_windows_command_line', lambda kernel, handle: None)
        def cim(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, subprocess.list2cmdline(args), '')
        monkeypatch.setattr(cancellation.subprocess, 'run', cim)
        identity = cancellation.process_identity(child.pid)
        assert identity and identity['argv'][1:] == ['-c', 'import time;time.sleep(60)']
        assert len(calls) == 1 and calls[0][0] == 'powershell.exe'
    finally:
        child.terminate()
        child.wait(timeout=5)


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows 소유 Job의 실제 손자 종료 확인')
def test_windows_taskkill_timeout_cancels_owned_grandchild_and_preserves_unrelated(tmp_path, monkeypatch):
    from app.modules.daily_qa import agent_runner, cancellation
    from app.modules.daily_qa.packages import Task
    from app.modules.daily_qa.workspace import prepare
    workspace = prepare(tmp_path / 'ws', tmp_path / 'repo', 'cancel-owned-tree', None, None)
    pid_file = tmp_path / 'owned-grandchild.pid'
    code = ('import subprocess,sys,time;from pathlib import Path;'
            "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
            f'Path({str(pid_file)!r}).write_text(str(p.pid));time.sleep(60)')
    monkeypatch.setattr(agent_runner, 'build_command', lambda *args, **kwargs: [sys.executable, '-c', code])
    def timed_out(*args, **kwargs):
        raise subprocess.TimeoutExpired('synthetic taskkill failure', 5)
    monkeypatch.setattr(agent_runner.subprocess, 'run', timed_out)
    unrelated = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'])
    owned = []
    def check():
        if pid_file.exists():
            if not owned:
                owned.append(cancellation.process_identity(int(pid_file.read_text()), include_command=False))
            raise cancellation.RunCancelled('사용자 중단')
    try:
        result = agent_runner.ClaudeRunner('unused', '', 30, cancel_check=check).run(
            Task('TASK-1', 'qa-new-issue-analysis', {}), workspace)
        assert result.cancelled and owned and owned[0]
        current = cancellation.process_identity(owned[0]['pid'], include_command=False)
        assert current is None or current['start'] != owned[0]['start']
        assert unrelated.poll() is None
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=5)
        # RED에서도 남은 소유 손자를 같은 시작 식별값의 핸들로만 정리한다.
        if owned and owned[0]:
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
            kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.OpenProcess(0x1001, False, owned[0]['pid'])
            if handle:
                try:
                    fields = [wintypes.FILETIME() for _ in range(4)]
                    if kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in fields)):
                        start = str((fields[0].dwHighDateTime << 32) | fields[0].dwLowDateTime)
                        if start == owned[0]['start']:
                            kernel.TerminateProcess(handle, 1)
                finally:
                    kernel.CloseHandle(handle)


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows 일시 정지 CLI의 오류 정리 확인')
def test_windows_resume_failure_leaves_no_suspended_owned_process(tmp_path, monkeypatch):
    from app.modules.daily_qa import agent_runner, windows_job
    from app.modules.daily_qa.packages import Task
    from app.modules.daily_qa.workspace import prepare
    workspace = prepare(tmp_path / 'ws', tmp_path / 'repo', 'resume-failed', None, None)
    processes = []
    class BrokenResume(windows_job.WindowsJob):
        def resume(self):
            processes.append(self.process)
            raise OSError('synthetic resume failure')
    monkeypatch.setattr(windows_job, 'WindowsJob', BrokenResume)
    monkeypatch.setattr(agent_runner, 'build_command', lambda *args, **kwargs: [sys.executable, '-c', 'import time;time.sleep(60)'])
    with pytest.raises(OSError, match='synthetic resume failure'):
        agent_runner.ClaudeRunner('unused', '', 30, cancel_check=lambda: None).run(
            Task('TASK-1', 'qa-new-issue-analysis', {}), workspace)
    assert len(processes) == 1 and processes[0].poll() is not None


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows 실제 nested Job·정상 CLI 자식 정리 확인')
def test_windows_nested_job_normal_completion_cleans_owned_children(tmp_path):
    from app.modules.daily_qa.windows_job import CREATE_SUSPENDED, WindowsJob
    from pathlib import Path
    source_root = str(Path(__file__).resolve().parents[1])
    pid_file = tmp_path / 'nested-owned-child.pid'
    worker = tmp_path / 'nested-worker.py'
    child_code = ('import subprocess,sys;from pathlib import Path;'
                  "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
                  f'Path({str(pid_file)!r}).write_text(str(p.pid))')
    worker.write_text(
        "import ctypes,json,os,sys\nfrom ctypes import wintypes\n"
        f"sys.path.insert(0, {source_root!r})\n"
        "from app.modules.daily_qa.agent_runner import _run_cancellable\n"
        "from app.modules.daily_qa.cancellation import process_identity\n"
        "from pathlib import Path\n"
        "kernel=ctypes.WinDLL('kernel32')\n"
        "kernel.GetCurrentProcess.restype=wintypes.HANDLE\n"
        "kernel.IsProcessInJob.argtypes=[wintypes.HANDLE,wintypes.HANDLE,ctypes.POINTER(wintypes.BOOL)]\n"
        "nested=wintypes.BOOL()\nassert kernel.IsProcessInJob(kernel.GetCurrentProcess(),None,ctypes.byref(nested))\n"
        f"result,cancelled=_run_cancellable([sys.executable,'-c',{child_code!r}],prompt='',cwd={str(tmp_path)!r},env=os.environ.copy(),timeout=30,check=lambda:None)\n"
        f"pid=int(Path({str(pid_file)!r}).read_text())\n"
        "print(json.dumps({'already_in_outer_job':bool(nested.value),'returncode':result.returncode,'cancelled':cancelled,'child_stopped':process_identity(pid,include_command=False) is None}))\n",
        encoding='utf-8')
    parent = subprocess.Popen([sys.executable, str(worker)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              creationflags=CREATE_SUSPENDED | subprocess.CREATE_NO_WINDOW)
    outer = WindowsJob(parent)
    try:
        assert outer.attach()
        outer.resume()
        stdout, stderr = parent.communicate(timeout=60)
        assert parent.returncode == 0, stderr.decode(errors='replace')
        payload = json.loads(stdout.decode('utf-8'))
        assert payload == {'already_in_outer_job': True, 'returncode': 0, 'cancelled': False, 'child_stopped': True}
    finally:
        outer.close()
        parent.wait(timeout=5)


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows Job 준비 실패 시 안전 거절 확인')
@pytest.mark.parametrize('failure', ['assign', 'kill_on_close'])
def test_windows_job_setup_failure_refuses_before_resume(tmp_path, monkeypatch, failure):
    from app.modules.daily_qa import agent_runner, windows_job
    from app.modules.daily_qa.packages import Task
    from app.modules.daily_qa.workspace import prepare
    workspace = prepare(tmp_path / 'ws', tmp_path / 'repo', 'job-setup-failed', None, None)
    marker = tmp_path / 'should-not-start.txt'
    processes = []
    class FailedSetup(windows_job.WindowsJob):
        def __init__(self, process):
            super().__init__(process)
            processes.append(process)
            if failure == 'kill_on_close':
                self.kernel.SetInformationJobObject = lambda *args: False
        def attach(self):
            return False if failure == 'assign' else super().attach()
    monkeypatch.setattr(windows_job, 'WindowsJob', FailedSetup)
    command = [sys.executable, '-c', f'from pathlib import Path;Path({str(marker)!r}).write_text("started")']
    monkeypatch.setattr(agent_runner, 'build_command', lambda *args, **kwargs: command)
    with pytest.raises(OSError, match='Job'):
        agent_runner.ClaudeRunner('unused', '', 30, cancel_check=lambda: None).run(
            Task('TASK-1', 'qa-new-issue-analysis', {}), workspace)
    assert not marker.exists()
    assert len(processes) == 1 and processes[0].poll() is not None


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows kill-on-close의 실제 종료 확인')
def test_windows_job_close_still_terminates_when_explicit_termination_fails():
    from app.modules.daily_qa.windows_job import CREATE_SUSPENDED, WindowsJob
    class FailedTerminate(WindowsJob):
        def terminate(self):
            return False
    child = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'],
                             creationflags=CREATE_SUSPENDED | subprocess.CREATE_NO_WINDOW)
    job = FailedTerminate(child)
    try:
        assert job.attach()
        job.resume()
        job.close()
        try:
            child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pytest.fail('owned Job process survived its last handle closing')
    finally:
        job.close()
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)


@pytest.mark.skipif(sys.platform == 'win32', reason='Linux 독립 세션·부모 종료·출력 파이프 확인')
def test_linux_parent_exit_still_cancels_owned_grandchild(tmp_path, monkeypatch):
    from pathlib import Path
    from app.modules.daily_qa import agent_runner
    from app.modules.daily_qa.cancellation import RunCancelled
    from app.modules.daily_qa.packages import Task
    from app.modules.daily_qa.workspace import prepare
    workspace = prepare(tmp_path / 'ws', tmp_path / 'repo', 'cancel-orphan', None, None)
    pid_file = tmp_path / 'owned-grandchild.pid'
    child_code = ("import os,signal,time;from pathlib import Path;"
                  "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                  f"Path({str(pid_file)!r}).write_text(str(os.getpid()));time.sleep(20)")
    parent_code = f"import subprocess,sys;subprocess.Popen([sys.executable,'-c',{child_code!r}])"
    monkeypatch.setattr(agent_runner, 'build_command', lambda *args, **kwargs: [sys.executable, '-c', parent_code])
    unrelated = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(20)'])
    started = time.monotonic()
    def check():
        if pid_file.exists() and time.monotonic() - started > .5:
            raise RunCancelled('사용자 중단')
    try:
        outcome = agent_runner.ClaudeRunner('unused', '', 10, cancel_check=check).run(
            Task('TASK-1', 'qa-new-issue-analysis', {}), workspace)
        assert outcome.cancelled and not outcome.ok
        assert time.monotonic() - started < 8
        assert unrelated.poll() is None
        stat = Path('/proc') / pid_file.read_text() / 'stat'
        assert not stat.exists() or stat.read_text().rsplit(')', 1)[1].split()[0] == 'Z'
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=5)
