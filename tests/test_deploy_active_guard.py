"""Validates: REQ-DEPLOY-002. 배포 전 다른 제품과 핵심 앱의 실행도 확인한다."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("busy,active", [(set(), 0), ({"second"}, 0), (set(), 2)])
def test_remote_deploy_guard_reads_every_product_and_core_jobs(monkeypatch, capsys, busy, active):
    from app.modules.daily_qa import settings, pipeline
    from app.core import storage
    script = Path("scripts/deploy.ps1").read_text(encoding="utf-8-sig")
    code = script.split("$guardCode = @'", 1)[1].split("'@", 1)[0]
    checked = []
    monkeypatch.setattr(settings, "configured_products", lambda: ["first", "second"])
    monkeypatch.setattr(settings, "load", lambda product: SimpleNamespace(product=product))
    def running(cfg):
        checked.append(cfg.product)
        return cfg.product in busy
    monkeypatch.setattr(pipeline, "is_running", running)
    monkeypatch.setattr(storage, "Storage", lambda: SimpleNamespace(operations_status=lambda _: {"active_jobs": active}))
    exec(compile(code, "deploy-guard", "exec"), {})
    data = json.loads(capsys.readouterr().out)
    assert checked == ["first", "second"]
    assert data["running_products"] == sorted(busy)
    assert data["active_jobs"] == active


def test_first_deploy_only_accepts_verified_empty_install(tmp_path, monkeypatch, capsys):
    script = Path("scripts/deploy.ps1").read_text(encoding="utf-8-sig")
    code = script.split("$guardCode = @'", 1)[1].split("'@", 1)[0]
    (tmp_path / ".venv").mkdir()
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exited:
        exec(compile(code, "deploy-guard", "exec"), {})
    assert exited.value.code == 0
    assert json.loads(capsys.readouterr().out)["initial_install"] is True


def test_existing_data_never_bypasses_deploy_guard(tmp_path, monkeypatch, capsys):
    from app.modules.daily_qa import settings
    from app.core import storage
    script = Path("scripts/deploy.ps1").read_text(encoding="utf-8-sig")
    code = script.split("$guardCode = @'", 1)[1].split("'@", 1)[0]
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings, "configured_products", lambda: [])
    monkeypatch.setattr(storage, "Storage", lambda: SimpleNamespace(operations_status=lambda _: {"active_jobs": 1}))
    exec(compile(code, "deploy-guard", "exec"), {})
    data = json.loads(capsys.readouterr().out)
    assert data.get("initial_install") is None
    assert data["active_jobs"] == 1
