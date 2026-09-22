from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from app.core.secrets_loader import DEFAULTS, resolve_secrets, secret_files_state

ROOT = Path(__file__).resolve().parents[2]

# listen 주소·포트의 단일 원본은 `config.yaml` 의 `app.host` / `app.port` 다. 아래 값은
# 그 키가 없거나 비었을 때만 쓰이는 최후 기본값이며, 운영 포트를 바꾸는 자리가 아니다.
#
# 24357 을 고른 이유: 1024 초과(권한 불필요), 서버의 ephemeral 범위(실측 32768~60999)
# 밖이라 나가는 연결의 source port 와 겹칠 수 없고, 잘 알려진 서비스가 없는 비라운드
# 번호라 같은 호스트의 다른 도구가 우연히 집을 확률이 낮다.
DEFAULT_APP_HOST = "0.0.0.0"
DEFAULT_APP_PORT = 24357


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


def app_bind(settings: Settings | None = None) -> tuple[str, int]:
    """`config.yaml` 의 `app.host` / `app.port` 를 uvicorn 인자로 바꾼다.

    키가 없거나 값이 비어 있으면 위 기본값으로 떨어진다. 포트가 정수로 읽히지 않으면
    조용히 기본값으로 가지 않고 그대로 터뜨린다 — 오타 난 포트로 엉뚱한 자리에 뜨는
    것보다 뜨지 않는 편이 낫다.
    """
    settings = settings if settings is not None else get_settings()
    host = str(settings.get("app.host") or DEFAULT_APP_HOST)
    raw_port = settings.get("app.port")
    port = DEFAULT_APP_PORT if raw_port in (None, "") else int(raw_port)
    return host, port


def app_self_url(settings: Settings | None = None) -> str:
    """이 앱이 자기 자신을 HTTP 로 호출할 때 쓰는 주소.

    예약 동기화와 Knowledge 동기화가 같은 프로세스의 REST API 를 다시 부른다. 항상
    루프백으로 나가므로 `app.host` 가 `0.0.0.0` 이어도 `127.0.0.1` 을 쓴다 — 바깥으로
    나가지 않고, 방화벽 규칙과도 무관하다.
    """
    _, port = app_bind(settings)
    return f"http://127.0.0.1:{port}"
