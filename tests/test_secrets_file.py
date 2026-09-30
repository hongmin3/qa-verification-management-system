from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import build_settings
from app.core.secrets_loader import (
    parse_json_like,
    parse_key_value_lines,
    parse_plain_text,
    resolve_secrets,
    secret_files_state,
)

FAKE_KEY = "AIza-test-key-0000000000000000000000000"
CONFIG_YAML = Path(__file__).resolve().parents[1] / "config.yaml"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "config.yaml").write_text(CONFIG_YAML.read_text(encoding="utf-8"), encoding="utf-8")
    return tmp_path


def test_plain_text_key_value():
    values = parse_plain_text("# 주석\nGEMINI_API_KEY=" + FAKE_KEY + "\nGEMINI_MODEL=gemini-2.5-pro\n")
    assert values == {"gemini_api_key": FAKE_KEY, "gemini_model": "gemini-2.5-pro"}


def test_plain_text_bare_key_only():
    assert parse_plain_text("# 여기에 붙여넣기\n\n  " + FAKE_KEY + "  \n") == {"gemini_api_key": FAKE_KEY}


def test_plain_text_tolerates_quotes_and_crlf():
    assert parse_plain_text('GEMINI_API_KEY = "' + FAKE_KEY + '"\r\n') == {"gemini_api_key": FAKE_KEY}


def test_key_name_aliases_are_accepted():
    assert parse_key_value_lines("gemini-api-key: " + FAKE_KEY) == {"gemini_api_key": FAKE_KEY}
    assert parse_key_value_lines("api key = " + FAKE_KEY) == {"gemini_api_key": FAKE_KEY}


def test_unknown_keys_are_ignored():
    assert parse_key_value_lines("SSH_PASSWORD=nope\nGEMINI_API_KEY=" + FAKE_KEY) == {"gemini_api_key": FAKE_KEY}


def test_json_file_is_parsed():
    text = json.dumps({"_안내": "설명", "GEMINI_API_KEY": FAKE_KEY}, ensure_ascii=False)
    assert parse_json_like(text) == {"gemini_api_key": FAKE_KEY}


def test_broken_json_falls_back_to_line_parsing():
    broken = '{\n  "GEMINI_API_KEY": "' + FAKE_KEY + '"\n  "GEMINI_MODEL": "gemini-2.5-pro",\n}'
    assert parse_json_like(broken) == {"gemini_api_key": FAKE_KEY, "gemini_model": "gemini-2.5-pro"}


def test_placeholder_values_count_as_missing(project: Path):
    (project / "secrets.txt").write_text("GEMINI_API_KEY=<여기에 키를 입력>\n", encoding="utf-8")
    assert resolve_secrets(project, {})[0]["gemini_api_key"] == ""
    (project / "secrets.txt").write_text("GEMINI_API_KEY=여기에_Key를_붙여넣습니다\n", encoding="utf-8")
    values, sources = resolve_secrets(project, {})
    assert values["gemini_api_key"] == ""
    assert sources["gemini_api_key"] == "기본값"


def test_priority_env_over_json_over_text_over_dotenv(project: Path):
    (project / ".env").write_text("GEMINI_API_KEY=from-dotenv\n", encoding="utf-8")
    values, sources = resolve_secrets(project, {})
    assert (values["gemini_api_key"], sources["gemini_api_key"]) == ("from-dotenv", ".env")

    (project / "secrets.txt").write_text("GEMINI_API_KEY=from-text\n", encoding="utf-8")
    values, sources = resolve_secrets(project, {})
    assert (values["gemini_api_key"], sources["gemini_api_key"]) == ("from-text", "secrets.txt")

    (project / "secrets.json").write_text('{"GEMINI_API_KEY": "from-json"}', encoding="utf-8")
    values, sources = resolve_secrets(project, {})
    assert (values["gemini_api_key"], sources["gemini_api_key"]) == ("from-json", "secrets.json")

    values, sources = resolve_secrets(project, {"GEMINI_API_KEY": "from-env"})
    assert (values["gemini_api_key"], sources["gemini_api_key"]) == ("from-env", "환경변수")


def test_empty_higher_priority_file_falls_through(project: Path):
    (project / "secrets.txt").write_text("GEMINI_API_KEY=\n", encoding="utf-8")
    (project / ".env").write_text("GEMINI_API_KEY=" + FAKE_KEY + "\n", encoding="utf-8")
    values, sources = resolve_secrets(project, {})
    assert (values["gemini_api_key"], sources["gemini_api_key"]) == (FAKE_KEY, ".env")


def test_utf8_bom_file_is_readable(project: Path):
    (project / "secrets.txt").write_text("GEMINI_API_KEY=" + FAKE_KEY + "\n", encoding="utf-8-sig")
    assert resolve_secrets(project, {})[0]["gemini_api_key"] == FAKE_KEY


def test_missing_files_are_reported_without_error(project: Path):
    state = {item["name"]: item["exists"] for item in secret_files_state(project)}
    assert state == {"secrets.json": False, "secrets.txt": False, ".env": False}


def test_secret_status_never_exposes_the_key(project: Path):
    (project / "secrets.txt").write_text("GEMINI_API_KEY=" + FAKE_KEY + "\n", encoding="utf-8")
    settings = build_settings(project)
    status = settings.secret_status()
    assert settings.secrets.gemini_api_key == FAKE_KEY
    assert status["gemini_api_key"] == {"configured": True, "length": len(FAKE_KEY), "source": "secrets.txt"}
    assert FAKE_KEY not in json.dumps(status, ensure_ascii=False)


def test_config_status_endpoint_hides_the_key():
    from app.main import app

    response = TestClient(app).get("/config/status")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"gemini_api_key", "gemini_model", "files", "daily_token_usage", "ai_provider"}
    assert isinstance(body["gemini_api_key"]["configured"], bool)
    assert "value" not in body["gemini_api_key"]
    assert set(body["daily_token_usage"]) == {"used", "limit", "exceeded"}
