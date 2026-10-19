"""화면에서 바꿀 수 있는 설정(AI·메일·사용률·서버 주소)과 변경 이력 (REQ-AICALL-007, REQ-WEB-008)."""
from __future__ import annotations

import logging
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from app.core.config import Settings

logger = logging.getLogger(__name__)
SETTING_LABELS = {
    "ai.claude.models.light": "가벼운 작업 모델",
    "ai.claude.models.standard": "기본 작업 모델 (일일 QA 분석)",
    "ai.claude.models.complex": "복잡한 작업 모델",
    "ai.claude.effort": "추론 정도",
    "daily_qa.intelligence.audit_model": "현재 이슈 전체 점검 모델",
    "daily_qa.intelligence.comment_model": "새 댓글 분석 모델",
    "daily_qa.savings.analysis_model": "일반 분석 모델 (예약 점검)",
    "daily_qa.savings.review_model": "선택 재검토 모델",
    "daily_qa.usage_guard.five_hour_percent": "5시간 사용률 기준 (%)",
    "daily_qa.usage_guard.seven_day_percent": "주간 사용률 기준 (%)",
    "notifications.admin_email": "관리자 메일",
    "daily_qa.report_recipients": "자동 보고 메일 받는 사람",
    "daily_qa.review_base_url": "메일 링크 서버 주소",
}
#: 화면에 이름·값을 보이지 않는 키. 관리자 비밀번호의 암호화 값이다 (REQ-WEB-008).
PASSWORD_KEY = "admin.password_hash"
#: 모델·추론 정도처럼 목록에서 고르는 키.
CHOICE_KEYS = ("ai.claude.models.light", "ai.claude.models.standard", "ai.claude.models.complex", "ai.claude.effort",
               "daily_qa.intelligence.audit_model", "daily_qa.intelligence.comment_model",
               "daily_qa.savings.analysis_model", "daily_qa.savings.review_model")
PERCENT_KEYS = ("daily_qa.usage_guard.five_hour_percent", "daily_qa.usage_guard.seven_day_percent")
EMAIL_KEY = "notifications.admin_email"
EMAIL_LIST_KEY = "daily_qa.report_recipients"
URL_KEY = "daily_qa.review_base_url"
MASKED_KEYS = (EMAIL_KEY, EMAIL_LIST_KEY)
MAX_RECIPIENTS = 10
#: `config.yaml` 에 적히지 않은 키의 처음 값. 코드(`daily_qa/settings.py`)가 쓰는 기본값과 같아야 한다.
BUILTIN_DEFAULTS = {"daily_qa.savings.analysis_model": "claude-sonnet-5-5", "daily_qa.savings.review_model": "claude-opus-5-5"}
_EMAIL = re.compile(r"^[A-Za-z0-9._%+'\-]+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)+$")
_HASH = re.compile(r"^pbkdf2_sha256\$\d{1,9}\$[0-9a-f]{32}\$[0-9a-f]{64}$")
#: 빈 값(가벼운 작업 모델 따름)을 고를 수 있는 키.
OPTIONAL_MODEL_KEYS = ("daily_qa.intelligence.audit_model", "daily_qa.intelligence.comment_model")
ALLOWED_KEYS = frozenset(SETTING_LABELS) | {PASSWORD_KEY}
DEFAULT_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1")
EFFORTS = ("", "low", "medium", "high", "xhigh", "max")
SCHEMA = """
CREATE TABLE IF NOT EXISTS app_setting_overrides (
    key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS app_setting_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL,
    old_value TEXT NOT NULL, new_value TEXT NOT NULL,
    changed_at TEXT NOT NULL, client TEXT NOT NULL
);
"""


def choices(settings: Settings, key: str) -> list[str]:
    if key not in ALLOWED_KEYS:
        raise ValueError("바꿀 수 없는 설정 키입니다.")
    if key not in CHOICE_KEYS:
        return []
    if key == "ai.claude.effort":
        return list(EFFORTS)
    models = settings.get("ai.claude.allowed_models", list(DEFAULT_MODELS))
    if not isinstance(models, list) or not all(isinstance(model, str) and model for model in models):
        raise ValueError("허용 모델 목록을 확인해 주세요.")
    return ([""] if key in OPTIONAL_MODEL_KEYS else []) + list(dict.fromkeys(models))


def default_of(settings: Settings, key: str) -> str:
    """설정 파일의 기본값. 파일에 없으면 코드가 쓰는 처음 값, 그것도 없으면 빈 값이다."""
    value = settings.default_value(key, None)
    if value is None:
        value = BUILTIN_DEFAULTS.get(key, "")
    return str(value)


def effective_of(settings: Settings, key: str) -> str:
    value = settings.get(key, None)
    return str(value) if value is not None else default_of(settings, key)


def split_addresses(raw: str) -> list[str]:
    """쉼표·세미콜론·줄바꿈으로 나눈 주소 목록. 빈 칸은 버리고 같은 주소(대소문자 무시)는 한 번만 둔다."""
    seen, result = set(), []
    for part in re.split(r"[,;\n\r]+", raw or ""):
        part = part.strip()
        if part and part.casefold() not in seen:
            seen.add(part.casefold())
            result.append(part)
    return result


def normalize(values: dict) -> dict:
    """저장 전에 값을 한 가지 모양으로 맞춘다. 화면 입력의 공백·구분자·정수 표기를 정리한다."""
    if not isinstance(values, dict):
        raise ValueError("바꿀 설정을 선택해 주세요.")
    result = {}
    for key, value in values.items():
        if key in PERCENT_KEYS and isinstance(value, int) and not isinstance(value, bool):
            result[key] = str(value)
        elif isinstance(value, str) and key == EMAIL_LIST_KEY:
            result[key] = ", ".join(split_addresses(value))
        elif isinstance(value, str) and key in (EMAIL_KEY, URL_KEY):
            result[key] = value.strip()
        else:
            result[key] = value
    return result


def _valid_address(value: str) -> bool:
    return len(value) <= 254 and bool(_EMAIL.match(value))


def _validate_one(key: str, value, settings: Settings) -> None:
    if key not in ALLOWED_KEYS:
        raise ValueError("바꿀 수 없는 설정 키입니다.")
    if key in CHOICE_KEYS:
        if not isinstance(value, str) or value not in choices(settings, key):
            raise ValueError("허용 모델과 추론 정도 중에서 선택해 주세요.")
    elif key in PERCENT_KEYS:
        if not isinstance(value, str) or not value.isascii() or not value.isdigit() or not 50 <= int(value) <= 100:
            raise ValueError(f"{SETTING_LABELS[key]}는 50 이상 100 이하의 정수로 입력해 주세요.")
    elif key == EMAIL_KEY:
        if not isinstance(value, str) or not _valid_address(value):
            raise ValueError("관리자 메일은 메일 주소 1개로 입력해 주세요.")
    elif key == EMAIL_LIST_KEY:
        addresses = split_addresses(value) if isinstance(value, str) else []
        if not 1 <= len(addresses) <= MAX_RECIPIENTS or not all(_valid_address(item) for item in addresses):
            raise ValueError(f"자동 보고 메일 받는 사람은 메일 주소 1~{MAX_RECIPIENTS}개를 쉼표로 나눠 입력해 주세요.")
    elif key == URL_KEY:
        parsed = urlsplit(value) if isinstance(value, str) else None
        if (parsed is None or parsed.scheme not in ("http", "https") or not parsed.hostname or len(value) > 200
                or any(ch.isspace() for ch in value) or parsed.username or parsed.query or parsed.fragment):
            raise ValueError("메일 링크 서버 주소는 http:// 또는 https:// 로 시작하는 주소로 입력해 주세요.")
    elif key == PASSWORD_KEY:
        if not isinstance(value, str) or not _HASH.match(value):
            raise ValueError("비밀번호는 비밀번호 변경 칸에서만 바꿀 수 있습니다.")


def validate(values: dict, settings: Settings) -> None:
    if not isinstance(values, dict) or not values:
        raise ValueError("바꿀 설정을 선택해 주세요.")
    for key, value in values.items():
        _validate_one(key, value, settings)


def history_text(key: str, value, *, reset: bool = False, old: bool = False) -> str:
    """변경 이력에 남길 글. 주소는 가리고 비밀번호는 값 없이 사실만 남긴다 (NFR-SEC-002)."""
    if key == PASSWORD_KEY:
        return "이전 비밀번호" if old else ("처음 비밀번호로 되돌림" if reset else "새 비밀번호로 변경")
    if key in MASKED_KEYS and value:
        from app.core.notifier import mask_address
        return ", ".join(mask_address(item) for item in split_addresses(str(value)))
    return str(value)


def load_overrides(db_path: Path, settings: Settings) -> dict[str, str]:
    """시작할 때 DB 를 읽기만 한다. 미생성·오류는 기본 설정으로 동작한다."""
    if not db_path.is_file():
        logger.warning("AI 설정 DB 가 없어 설정 파일 기본값을 사용합니다.")
        return {}
    try:
        with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)) as db:
            rows = db.execute("SELECT key,value FROM app_setting_overrides").fetchall()
        result = {}
        for key, value in rows:
            if key not in ALLOWED_KEYS:
                continue
            try:
                validate({key: value}, settings)
                result[key] = value
            except ValueError:
                logger.warning("저장된 AI 설정 값이 허용 목록과 맞지 않아 기본값을 사용합니다.")
        return result
    except sqlite3.Error:
        logger.warning("AI 설정 DB 읽기에 실패해 설정 파일 기본값을 사용합니다.")
        return {}


class SettingOverrides:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(db_path)) as db:
            db.executescript(SCHEMA)

    def read(self) -> dict[str, str]:
        with closing(sqlite3.connect(self.db_path)) as db:
            return dict(db.execute("SELECT key,value FROM app_setting_overrides"))

    def history(self) -> list[dict]:
        with closing(sqlite3.connect(self.db_path)) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute("SELECT * FROM app_setting_changes ORDER BY id DESC LIMIT 10")]

    def save(self, values: dict[str, str], settings: Settings, client: str) -> None:
        values = normalize(values)
        validate(values, settings)
        self._change(values, settings, client, reset=False)

    def reset(self, keys: list[str], settings: Settings, client: str) -> None:
        if not keys or any(key not in ALLOWED_KEYS for key in keys):
            raise ValueError("바꿀 수 없는 설정 키입니다.")
        self._change(dict.fromkeys(keys), settings, client, reset=True)

    def _change(self, values: dict, settings: Settings, client: str, *, reset: bool) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            for key, value in values.items():
                row = db.execute("SELECT value FROM app_setting_overrides WHERE key=?", (key,)).fetchone()
                if reset and row is None:
                    continue
                default = default_of(settings, key)
                old = row[0] if row else default
                new = default if reset else value
                if reset:
                    db.execute("DELETE FROM app_setting_overrides WHERE key=?", (key,))
                else:
                    db.execute("INSERT INTO app_setting_overrides VALUES(?,?,?) "
                               "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                               (key, value, now))
                db.execute("INSERT INTO app_setting_changes(key,old_value,new_value,changed_at,client) VALUES(?,?,?,?,?)",
                           (key, history_text(key, old, old=True), history_text(key, new, reset=reset), now, client))
