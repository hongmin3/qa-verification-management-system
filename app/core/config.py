from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from app.core.secrets_loader import DEFAULTS, resolve_secrets, secret_files_state

ROOT = Path(__file__).resolve().parents[2]


class Secrets(BaseModel):
    gemini_api_key: str = DEFAULTS["gemini_api_key"]
    gemini_model: str = DEFAULTS["gemini_model"]
    app_secret_key: str = DEFAULTS["app_secret_key"]
    manual_hub_user: str = DEFAULTS["manual_hub_user"]
    manual_hub_password: str = DEFAULTS["manual_hub_password"]
    # 운영 알림 (app/core/notifier.py). 개인 메일 주소·SMTP 자격증명은 공개 저장소에
    # 커밋하지 않으므로 secrets 로만 받는다. 비어 있으면 알림이 꺼진 채 동작한다.
    notify_email_to: str = DEFAULTS["notify_email_to"]
    smtp_user: str = DEFAULTS["smtp_user"]
    smtp_password: str = DEFAULTS["smtp_password"]


class Settings(BaseModel):
    raw: dict[str, Any]
    secrets: Secrets
    secret_sources: dict[str, str] = {}
    root: Path = ROOT

    def get(self, dotted: str, default: Any = None) -> Any:
        value: Any = self.raw
        for key in dotted.split("."):
            if not isinstance(value, dict) or key not in value:
                return default
            value = value[key]
        return value

    def path(self, dotted: str) -> Path:
        return self.root / str(self.get(dotted))

    def secret_status(self) -> dict[str, Any]:
        """Key 값 자체는 절대 포함하지 않고 설정 여부만 반환한다."""
        api_key = self.secrets.gemini_api_key
        return {
            "gemini_api_key": {
                "configured": bool(api_key),
                "length": len(api_key),
                "source": self.secret_sources.get("gemini_api_key", "기본값"),
            },
            "gemini_model": {
                "value": self.secrets.gemini_model,
                "source": self.secret_sources.get("gemini_model", "기본값"),
            },
            "files": secret_files_state(self.root),
        }


def build_settings(root: Path = ROOT) -> Settings:
    with (root / "config.yaml").open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    values, sources = resolve_secrets(root, os.environ)
    settings = Settings(raw=raw, secrets=Secrets(**values), secret_sources=sources, root=root)
    for key in ("upload_dir", "specification_dir", "testcase_dir", "index_dir", "report_dir", "export_dir", "generated_tc_dir", "log_dir", "manual_revision_dir", "manual_review_comment_dir", "manual_hub_cache_dir"):
        settings.path(f"storage.{key}").mkdir(parents=True, exist_ok=True)
    return settings


@lru_cache
def get_settings() -> Settings:
    return build_settings()


def reload_settings() -> Settings:
    """`secrets.txt`/`secrets.json`을 수정한 뒤 재시작 없이 다시 읽는다."""
    get_settings.cache_clear()
    return get_settings()
