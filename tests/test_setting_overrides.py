"""Validates: REQ-AICALL-007."""
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from app.core.config import ROOT, build_settings


@pytest.fixture
def settings_root(tmp_path):
    shutil.copyfile(ROOT / "config.yaml", tmp_path / "config.yaml")
    return tmp_path


def test_persist_reset_and_snapshot(settings_root):
    from app.core.setting_overrides import SettingOverrides
    initial = build_settings(settings_root)
    store = SettingOverrides(settings_root / "data/app.db")
    key = "ai.claude.models.standard"
    store.save({key: "claude-sonnet-5-5"}, initial, "127.0.0.1")
    updated = build_settings(settings_root)
    assert updated.get(key) == "claude-sonnet-5-5"
    assert initial.get(key) == initial.raw["ai"]["claude"]["models"]["standard"]
    assert store.history()[0]["old_value"] == initial.get(key)
    store.reset([key], updated, "127.0.0.1")
    assert build_settings(settings_root).get(key) == initial.get(key)
    assert len(store.history()) == 2


@pytest.mark.parametrize("values", [
    {"app.port": "1234"}, {"ai.claude.models.light": "unknown"},
    {"ai.claude.effort": "ultra"}, {"ai.claude.effort": 3},
    {"ai.claude.effort": "high", "ai.claude.models.standard": "unknown"},
])
def test_reject_invalid_bundle(settings_root, values):
    from app.core.setting_overrides import SettingOverrides
    store = SettingOverrides(settings_root / "data/app.db")
    with pytest.raises(ValueError):
        store.save(values, build_settings(settings_root), "127.0.0.1")
    assert store.read() == {}
    assert store.history() == []


def test_invalid_persisted_values_are_ignored(settings_root):
    from app.core.setting_overrides import SettingOverrides
    store = SettingOverrides(settings_root / "data/app.db")
    with sqlite3.connect(store.db_path) as db:
        for key, value in [("app.port", "1234"), ("ai.claude.effort", "ultra")]:
            db.execute("INSERT INTO app_setting_overrides VALUES(?,?,?)", (key, value, "now"))
    settings = build_settings(settings_root)
    assert settings.get("app.port") != "1234"
    assert settings.get("ai.claude.effort") == "medium"


def test_corrupt_database_uses_defaults(settings_root, caplog):
    (settings_root / "data").mkdir()
    (settings_root / "data/app.db").write_bytes(b"invalid database")
    assert build_settings(settings_root).get("ai.claude.effort") == "medium"
    assert "설정" in caplog.text


def test_separate_process_reads_overrides(settings_root):
    from app.core.setting_overrides import SettingOverrides
    settings = build_settings(settings_root)
    SettingOverrides(settings_root / "data/app.db").save({"ai.claude.effort": "high"}, settings, "127.0.0.1")
    result = subprocess.run([sys.executable, "-c",
        "import sys; from pathlib import Path; from functools import partial; from app.core import config; "
        "from app.modules.daily_qa.settings import load; "
        "config.build_settings=partial(config.build_settings,root=Path(sys.argv[1])); "
        "print(config.get_settings().get('ai.claude.effort')); print(load().claude_effort)", str(settings_root)],
        cwd=ROOT, text=True, capture_output=True, check=True)
    assert result.stdout.splitlines() == ["high", "high"]
