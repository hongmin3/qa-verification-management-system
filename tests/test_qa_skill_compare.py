"""Validates REQ-VXVUECHATBOT-027 before any operational file is sent."""

from copy import deepcopy
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from scripts import qa_skill_compare as gate
from scripts import qa_skill_eval as evals


def results():
    return [
        {"case": case.id, "phase": phase, "trial": trial, "status": "PASS",
         "model": "model", "snapshot_id": "bellalun" if case.prefix else "vxvue",
         "xipl_snapshot_id": "xipl" if case.kind == "xipl" else None,
         "live_evidence_sha256": ["evidence"] if case.kind in ("redmine", "polarion") else [],
         "answer": case.expected}
        for case, phase, trial in gate.matrix()
    ]


def test_evaluates_12_full_and_six_core_per_version():
    baseline = results()
    assert len(baseline) == 18
    report = gate.compare(baseline, deepcopy(baseline))
    assert report["status"] == "PASS"
    assert not report["regressions"]


def test_new_failure_and_core_pass_count_drop_block():
    baseline = results()
    candidate = deepcopy(baseline)
    first = candidate[0]
    first["status"] = "FAIL"
    next(row for row in candidate if row["phase"] == "core" and row["case"] == first["case"])["status"] = "FAIL"
    report = gate.compare(baseline, candidate)
    assert report["status"] == "REGRESSION"
    assert {(row["case"], row["phase"]) for row in report["regressions"]} == {
        (first["case"], "full"), (first["case"], "core"),
    }


@pytest.mark.parametrize("kind", ["snapshot", "live", "model", "missing", "duplicate", "run_error"])
def test_dissimilar_or_unavailable_inputs_block(kind):
    baseline = results()
    candidate = deepcopy(baseline)
    if kind == "snapshot":
        candidate[0]["snapshot_id"] = "new"
    elif kind == "live":
        next(row for row in candidate if row["live_evidence_sha256"])["live_evidence_sha256"] = ["new"]
    elif kind == "model":
        candidate[0]["model"] = "new"
    elif kind == "missing":
        candidate.pop()
    elif kind == "duplicate":
        candidate.append(candidate[-1])
    else:
        candidate[0]["status"] = "RUN_ERROR"
    assert gate.compare(baseline, candidate)["status"] == "INCOMPARABLE"


def test_deploy_gate_precedes_file_transfer_and_uses_tested_copy():
    script = Path("scripts/deploy.ps1").read_text(encoding="utf-8-sig")
    assert script.index("qa_skill_compare.py") < script.index("scp -r")
    assert '$gateExit -ne 0' in script
    assert '$skillStage' in script[script.index("qa_skill_compare.py"):script.index("scp -r")]


def test_unchanged_skills_skip_model_and_codex_config(tmp_path, monkeypatch):
    skills = {"vxvue-ai": b"vx", "bellalun-server-ai": b"bell"}
    monkeypatch.setattr(gate, "download_baseline", lambda *_: skills)
    report = gate.inspect(None, "http://example.invalid", skills, SimpleNamespace(force=False), tmp_path)
    assert report["status"] == "UNCHANGED"
    assert not report["changed"]


def test_wrong_ssh_baseline_blocks_even_when_download_matches_candidate(tmp_path, monkeypatch):
    skills = {"vxvue-ai": b"vx", "bellalun-server-ai": b"bell"}
    monkeypatch.setattr(gate, "download_baseline", lambda *_: skills)
    with pytest.raises(ValueError, match="SSH"):
        gate.inspect(None, "http://example.invalid", skills,
                     SimpleNamespace(force=False, baseline_hashes={}), tmp_path)


def test_changed_skill_runs_every_case_with_its_version(tmp_path, monkeypatch):
    baseline = {"vxvue-ai": b"vx", "bellalun-server-ai": b"bell"}
    candidate = {**baseline, "vxvue-ai": b"candidate"}
    available = {row["case"]: row for row in results()}
    (tmp_path / "config.toml").write_text(
        'model = "model"\n[mcp_servers.vxvue-ai]\nurl = "http://test/vxvue-desktop/mcp"\n', encoding="utf-8",
    )
    monkeypatch.setattr(gate, "download_baseline", lambda *_: baseline)
    monkeypatch.setattr(gate, "capture_snapshots",
                        lambda *_: {"vxvue-ai": "vxvue", "bellalun-server-ai": "bellalun", "xipl": "xipl"})
    calls = []

    def evaluate(case, args, home, *, skill):
        calls.append(skill)
        return deepcopy(available[case.id])

    monkeypatch.setattr(evals, "run_case", evaluate)
    report = gate.inspect(None, "http://test", candidate, SimpleNamespace(force=False, model=None, jobs=2), tmp_path)
    assert report["status"] == "PASS"
    assert report["changed"] == ["vxvue-ai"]
    assert len(calls) == 36 and calls.count(b"vx") == calls.count(b"candidate") == 9


def test_existing_failure_can_be_reported_without_blocking():
    baseline = results()
    baseline[0]["status"] = "FAIL"
    report = gate.compare(baseline, deepcopy(baseline))
    assert report["status"] == "PASS"
    assert report["cases"][0]["status"] == "EXISTING_FAILURE"


@pytest.mark.skipif(not shutil.which("powershell.exe"), reason="Windows deployment entry point")
def test_deploy_guard_aborts_actual_transfer_block_on_regression(tmp_path):
    script = Path("scripts/deploy.ps1").read_text(encoding="utf-8-sig")
    deployment = "$qaState = Assert-QaIdle" + script.split("$qaState = Assert-QaIdle", 1)[1]
    deployment = deployment.split('Write-Host "파일 전송 완료', 1)[0]
    path = tmp_path / "preflight.ps1"
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / "sample").write_text("candidate", encoding="utf-8")
    path.write_text(r'''
$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$deploy = @{ DEPLOY_SSH_HOST = "qa.example" }
$appPort = 24357
function Assert-QaIdle { return @{ running_products=@(); active_jobs=0 } }
function Get-QaSkillReleaseState { return @{ vxvue=("a"*64); bellalun=("b"*64); port=24357 } }
function ssh { throw "REMOTE_WRITE_REACHED" }
function Invoke-Native([scriptblock]$Command) {
    if ("$Command" -notmatch "qa_skill_compare.py") { throw "TRANSFER_REACHED" }
    if ((Get-Content -LiteralPath (Join-Path $skillStage "sample")) -ne "candidate") { throw "WRONG_CANDIDATE" }
    [IO.File]::WriteAllText((Join-Path $PSScriptRoot "gate-reached"), "1")
    Write-Output 1
}
''' + deployment, encoding="utf-8-sig")
    result = subprocess.run(["powershell.exe", "-NoProfile", "-File", str(path)],
                            capture_output=True, timeout=30)
    assert result.returncode != 0
    assert (tmp_path / "gate-reached").read_text() == "1"
    assert b"qa_skill_compare.py" not in result.stderr
    assert all(marker not in result.stderr for marker in (b"REMOTE_WRITE_REACHED", b"TRANSFER_REACHED", b"WRONG_CANDIDATE"))
