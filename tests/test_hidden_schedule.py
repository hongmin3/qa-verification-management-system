"""Validates: REQ-SYNC-002, REQ-BELLALUNSHARED-006."""
import json
from pathlib import Path
import subprocess
import sys
import os

import pytest

RUNNER = Path(__file__).resolve().parents[1] / 'scripts/run_scheduled_python.py'


def invoke(tmp_path, code, *args):
    return subprocess.run([sys.executable, str(RUNNER), '--error-log', str(tmp_path / 'error.log'),
                           '--', sys.executable, '-X', 'utf8', '-c', code, *args], cwd=tmp_path,
                          capture_output=True, timeout=20)


def test_hidden_child_keeps_arguments_cwd_and_has_no_console(tmp_path):
    result = invoke(tmp_path,
        "import ctypes,json,os,sys;from pathlib import Path;"
        "Path('result.json').write_text(json.dumps({'args':sys.argv[1:],'cwd':os.getcwd(),"
        "'console':ctypes.windll.kernel32.GetConsoleWindow() if os.name=='nt' else 0}),encoding='utf8')",
        'space and "quote"', '한글')
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    value = json.loads((tmp_path / 'result.json').read_text(encoding='utf-8'))
    assert value['args'] == ['space and "quote"', '한글']
    assert Path(value['cwd']) == tmp_path
    assert value['console'] == 0
    assert not (tmp_path / 'error.log').exists()


def test_child_failure_keeps_exit_code_and_records_output(tmp_path):
    result = invoke(tmp_path, "import sys;print('합성 실패');sys.exit(7)")
    assert result.returncode == 7
    assert '합성 실패' in (tmp_path / 'error.log').read_text(encoding='utf-8')


def test_missing_child_is_reported_without_a_console(tmp_path):
    result = subprocess.run([sys.executable, str(RUNNER), '--error-log', str(tmp_path / 'error.log'),
                             '--', str(tmp_path / 'missing.exe')], capture_output=True, timeout=20)
    assert result.returncode == 1
    assert 'FileNotFoundError' in (tmp_path / 'error.log').read_text(encoding='utf-8')


@pytest.mark.skipif(os.name != 'nt', reason='Windows installer')
def test_installed_runner_survives_source_checkout_removal(tmp_path):
    """Execute the installer's real copy block with synthetic source files."""
    source = tmp_path / 'checkout'
    target = tmp_path / 'installed'
    (source / 'scripts').mkdir(parents=True)
    (source / 'app/core').mkdir(parents=True)
    for name in ['knowledge_retry.py', 'knowledge_push.py', 'skill_knowledge.py']:
        (source / 'app/core' / name).write_text('# synthetic worker\n')
    (source / 'scripts/retry_product_knowledge.py').write_text('print("worker")\n')
    (source / 'scripts/run_scheduled_python.py').write_bytes(RUNNER.read_bytes())
    installer = (RUNNER.parent / 'install_product_knowledge_schedule.ps1').read_text(encoding='utf-8-sig')
    block = installer.split('$workerSources =', 1)[1].split('$action =', 1)[0]
    quote = lambda p: "'" + str(p).replace("'", "''") + "'"
    script = ("$ErrorActionPreference='Stop'\n"
              f"$sourceRoot={quote(source)}\n$ProjectRoot={quote(target)}\n"
              "$script=Join-Path $sourceRoot 'scripts/retry_product_knowledge.py'\n"
              "$hiddenRunner=Join-Path $sourceRoot 'scripts/run_scheduled_python.py'\n"
              f"$Python={quote(sys.executable)}\n$TargetUrl='http://example.invalid'\n"
              '$workerSources =' + block + '\nWrite-Output $hiddenArguments\n')
    harness = tmp_path / 'copy-worker.ps1'
    harness.write_text(script, encoding='utf-8-sig')
    result = subprocess.run(['powershell.exe', '-NoProfile', '-File', str(harness)],
                            capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    source.rename(tmp_path / 'removed-checkout')
    installed_runner = next(target.glob('data/knowledge_retry/worker-*/run_scheduled_python.py'), None)
    assert installed_runner is not None, 'runner must be copied into the installed worker'
    assert str(installed_runner).encode() in result.stdout
    readback = tmp_path / 'ran.txt'
    child = subprocess.run([sys.executable, str(installed_runner), '--error-log', str(tmp_path / 'failure.log'),
                            '--', sys.executable, '-c',
                            'from pathlib import Path; import sys; Path(sys.argv[1]).write_text("ok")', str(readback)],
                           capture_output=True, timeout=20)
    assert child.returncode == 0, child.stderr.decode(errors='replace')
    assert readback.read_text() == 'ok'
