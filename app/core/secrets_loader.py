"""사용자가 직접 편집하는 비밀정보 파일을 읽는다.

`.env`는 Windows에서 확장자가 없어 열기 불편하므로, 같은 값을 메모장으로 바로 열 수 있는
`secrets.txt` 또는 `secrets.json`에 입력할 수 있게 한다.

우선순위 (앞쪽이 이김):
    1. OS 환경변수
    2. secrets.json
    3. secrets.txt
    4. .env
    5. 코드 기본값

이 모듈은 값 자체를 로그·화면·Report에 출력하지 않는다. 외부에는 값의 존재 여부,
길이, 출처 이름만 노출한다.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

JSON_FILENAME = "secrets.json"
TEXT_FILENAME = "secrets.txt"
DOTENV_FILENAME = ".env"

DEFAULTS: dict[str, str] = {
    "gemini_api_key": "",
    "gemini_model": "gemini-2.5-flash",
    "app_secret_key": "",
    # 하위 서비스(매뉴얼 서버) 조회용 전용 계정. 비어 있으면 연동 자체가 비활성이다.
    "manual_hub_user": "",
    "manual_hub_password": "",
    # 운영 알림 (app/core/notifier.py). 비어 있으면 알림이 꺼진 채로 동작한다.
    # 개인 메일 주소는 공개 저장소에 커밋하지 않으므로 여기(secrets)에 둔다.
    "notify_email_to": "",
    "smtp_user": "",
    "smtp_password": "",
}

# 정규화된 이름(영숫자 소문자만) -> 표준 키
ALIASES: dict[str, str] = {
    "geminiapikey": "gemini_api_key",
    "apikey": "gemini_api_key",
    "key": "gemini_api_key",
    "geminikey": "gemini_api_key",
    "geminimodel": "gemini_model",
    "model": "gemini_model",
    "appsecretkey": "app_secret_key",
    "manualhubuser": "manual_hub_user",
    "manualhubid": "manual_hub_user",
    "manualhubloginid": "manual_hub_user",
    "manualhubpassword": "manual_hub_password",
    "manualhubpw": "manual_hub_password",
    "notifyemailto": "notify_email_to",
    "notifyemail": "notify_email_to",
    "alertemail": "notify_email_to",
    "emailto": "notify_email_to",
    "smtpuser": "smtp_user",
    "smtpid": "smtp_user",
    "smtppassword": "smtp_password",
    "smtppw": "smtp_password",
}

# 예제 파일을 그대로 둔 경우를 미입력으로 취급하기 위한 표시값
PLACEHOLDERS: frozenset[str] = frozenset(
    {
        "changeme",
        "yourapikeyhere",
        "yourgeminiapikeyhere",
        "pasteyourkeyhere",
        "pastekeyhere",
        "여기에키를붙여넣으세요",
        "여기에키입력",
        "여기에값을입력하세요",
    }
)


def _normalize_name(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _canonical_key(name: str) -> str | None:
    normalized = _normalize_name(name)
    if normalized in ALIASES:
        return ALIASES[normalized]
    for key in DEFAULTS:
        if normalized == _normalize_name(key):
            return key
    return None


def _clean_value(value: str) -> str:
    value = value.strip().strip(",").strip()
    for quote in ('"', "'"):
        if len(value) >= 2 and value.startswith(quote) and value.endswith(quote):
            value = value[1:-1]
            break
    return value.strip()


def _is_placeholder(value: str) -> bool:
    if not value:
        return True
    if value.startswith("<") and value.endswith(">"):
        return True
    normalized = _normalize_name(value)
    if normalized in PLACEHOLDERS:
        return True
    return "여기에" in value and ("입력" in value or "붙여넣" in value)


def _read_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp949"):
        try:
            return path.read_text(encoding=encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def parse_key_value_lines(text: str) -> dict[str, str]:
    """`KEY=value`, `KEY: value`, `"KEY": "value",`, `export KEY=value`를 모두 읽는다."""
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line[0] in "{}[]" or line.startswith("#") or line.startswith("//"):
            continue
        if line.lower().startswith("export "):
            line = line[len("export ") :].strip()
        positions = [index for index in (line.find("="), line.find(":")) if index > 0]
        if not positions:
            continue
        separator = min(positions)
        key = _canonical_key(_clean_value(line[:separator]))
        if key:
            values[key] = _clean_value(line[separator + 1 :])
    return values


def parse_json_like(text: str) -> dict[str, str]:
    """정상 JSON이면 JSON으로, 쉼표 누락 등으로 깨졌으면 줄 단위로 읽는다."""
    stripped = text.strip()
    if not stripped:
        return {}
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return parse_key_value_lines(stripped)
    if not isinstance(data, dict):
        return {}
    values: dict[str, str] = {}
    for name, value in data.items():
        key = _canonical_key(str(name))
        if key and not isinstance(value, (dict, list)):
            values[key] = _clean_value("" if value is None else str(value))
    return values


def parse_plain_text(text: str) -> dict[str, str]:
    """`KEY=value` 형식을 우선 읽고, 키 문자열만 한 줄 적혀 있으면 API Key로 본다."""
    values = parse_key_value_lines(text)
    if values:
        return values
    for raw_line in text.splitlines():
        line = _clean_value(raw_line)
        if line and not line.startswith("#") and not line.startswith("//"):
            return {"gemini_api_key": line}
    return {}


def _load_file(path: Path, parser) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        parsed = parser(_read_text(path))
    except OSError:
        return {}
    return {key: value for key, value in parsed.items() if not _is_placeholder(value)}


def resolve_secrets(root: Path, environ: Mapping[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """비밀정보 값과 각 값의 출처 이름을 우선순위대로 결정한다."""
    env_values = {
        key: _clean_value(environ[key.upper()])
        for key in DEFAULTS
        if _clean_value(environ.get(key.upper(), ""))
    }
    layers: list[tuple[str, dict[str, str]]] = [
        ("환경변수", env_values),
        (JSON_FILENAME, _load_file(root / JSON_FILENAME, parse_json_like)),
        (TEXT_FILENAME, _load_file(root / TEXT_FILENAME, parse_plain_text)),
        (DOTENV_FILENAME, _load_file(root / DOTENV_FILENAME, parse_key_value_lines)),
    ]
    values: dict[str, str] = {}
    sources: dict[str, str] = {}
    for key, default in DEFAULTS.items():
        for source, layer in layers:
            candidate = layer.get(key, "")
            if candidate:
                values[key] = candidate
                sources[key] = source
                break
        else:
            values[key] = default
            sources[key] = "기본값"
    return values, sources


def secret_files_state(root: Path) -> list[dict[str, object]]:
    """어떤 입력 파일이 존재하는지만 알려준다. 내용은 노출하지 않는다."""
    return [
        {"name": name, "exists": (root / name).is_file()}
        for name in (JSON_FILENAME, TEXT_FILENAME, DOTENV_FILENAME)
    ]
