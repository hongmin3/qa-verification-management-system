"""화면에서 바꿀 수 있는 AI 설정과 변경 이력 (REQ-AICALL-007)."""
from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.config import Settings

logger = logging.getLogger(__name__)
SETTING_LABELS = {
    "ai.claude.models.light": "가벼운 작업 모델",
    "ai.claude.models.standard": "기본 작업 모델 (일일 QA 분석)",
    "ai.claude.models.complex": "복잡한 작업 모델",
    "ai.claude.effort": "추론 정도",
    "daily_qa.intelligence.audit_model": "현재 상태 점검 모델",
}
ALLOWED_KEYS = frozenset(SETTING_LABELS)
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
    if key == "ai.claude.effort":
        return list(EFFORTS)
    models = settings.get("ai.claude.allowed_models", list(DEFAULT_MODELS))
    if not isinstance(models, list) or not all(isinstance(model, str) and model for model in models):
        raise ValueError("허용 모델 목록을 확인해 주세요.")
    return ([""] if key == "daily_qa.intelligence.audit_model" else []) + list(dict.fromkeys(models))


def validate(values: dict, settings: Settings) -> None:
    if not values:
        raise ValueError("바꿀 설정을 선택해 주세요.")
    for key, value in values.items():
        if not isinstance(value, str) or value not in choices(settings, key):
            raise ValueError("허용 모델과 추론 정도 중에서 선택해 주세요.")


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
                default = settings.default_value(key, "")
                old = row[0] if row else default
                new = default if reset else value
                if reset:
                    db.execute("DELETE FROM app_setting_overrides WHERE key=?", (key,))
                else:
                    db.execute("INSERT INTO app_setting_overrides VALUES(?,?,?) "
                               "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                               (key, value, now))
                db.execute("INSERT INTO app_setting_changes(key,old_value,new_value,changed_at,client) VALUES(?,?,?,?,?)",
                           (key, str(old), str(new), now, client))
