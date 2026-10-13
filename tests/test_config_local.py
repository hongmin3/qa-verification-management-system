"""그 컴퓨터에만 쓰는 설정 덮어쓰기 (SPEC REQ-CONF-001 순서 2, TEST-CONF-003).

Validates: REQ-CONF-001
"""

from __future__ import annotations

from app.core.config import build_settings

BASE = """
storage:
  log_dir: output/logs
daily_qa:
  enabled: true
  schedule:
    day_of_week: "mon-fri"
    time: "07:30"
"""
LOCAL_OFF = """
daily_qa:
  schedule:
    enabled: false
"""


def test_local_file_overrides_only_the_keys_it_names(tmp_path, monkeypatch):
    monkeypatch.delenv("QA_SKIP_LOCAL_CONFIG", raising=False)
    (tmp_path / "config.yaml").write_text(BASE, encoding="utf-8")
    (tmp_path / "config.local.yaml").write_text("daily_qa:\n  schedule:\n    enabled: false\n", encoding="utf-8")
    settings = build_settings(tmp_path)
    assert settings.get("daily_qa.schedule.enabled") is False
    assert settings.get("daily_qa.schedule.time") == "07:30"          # 적지 않은 키는 config.yaml 값
    assert settings.get("daily_qa.enabled") is True


def test_without_or_with_empty_local_file_config_yaml_is_used(tmp_path, monkeypatch):
    monkeypatch.delenv("QA_SKIP_LOCAL_CONFIG", raising=False)
    (tmp_path / "config.yaml").write_text(BASE, encoding="utf-8")
    assert build_settings(tmp_path).get("daily_qa.schedule.enabled", True) is True
    (tmp_path / "config.local.yaml").write_text("", encoding="utf-8")
    assert build_settings(tmp_path).get("daily_qa.schedule.time") == "07:30"


def test_tests_ignore_the_local_file(tmp_path, monkeypatch):
    monkeypatch.setenv("QA_SKIP_LOCAL_CONFIG", "1")
    (tmp_path / "config.yaml").write_text(BASE, encoding="utf-8")
    (tmp_path / "config.local.yaml").write_text(LOCAL_OFF, encoding="utf-8")
    assert build_settings(tmp_path).get("daily_qa.schedule.enabled", True) is True
