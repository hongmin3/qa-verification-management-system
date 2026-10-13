"""AI 모델 설정 화면·API. 서버 직접 접속만 저장한다 (REQ-AICALL-007)."""
from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict

from app.core.config import app_bind, get_settings, reload_settings
from app.core.setting_overrides import ALLOWED_KEYS, SETTING_LABELS, SettingOverrides, choices, validate

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/settings/ai", tags=["AI 설정"])
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def can_edit(request: Request) -> bool:
    # serve.py 는 proxy_headers=False 로 scope.client 를 TCP peer 그대로 유지한다.
    # nginx 는 루프백으로 접속해도 전달 헤더를 붙이므로 아래 조건으로 거절된다.
    if not request.client or request.client.host not in ("127.0.0.1", "::1"):
        return False
    if any(key == "forwarded" or key == "x-real-ip" or key.startswith("x-forwarded-")
           for key in request.headers):
        return False
    origin = request.headers.get("origin")
    if origin:
        parsed = urlsplit(origin)
        if (parsed.scheme, parsed.netloc) != (request.url.scheme, request.url.netloc):
            return False
    return True


def require_local(request: Request) -> str:
    if not can_edit(request):
        raise HTTPException(403, "설정 저장은 서버 직접 접속에서만 가능합니다. SSH 터널을 사용해 주세요.")
    return request.client.host


def _state(request: Request) -> dict:
    settings = get_settings()
    db_path = settings.root / "data/app.db"
    history, running = [], []
    error = ""
    if db_path.is_file():
        try:
            with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
                db.row_factory = sqlite3.Row
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "app_setting_changes" in tables:
                    history = [dict(row) for row in db.execute("SELECT * FROM app_setting_changes ORDER BY id DESC LIMIT 10")]
                if "daily_qa_runs" in tables:
                    running = [row[0] for row in db.execute("SELECT id FROM daily_qa_runs WHERE status='RUNNING' ORDER BY started_at DESC")]
        except sqlite3.Error:
            logger.warning("AI 설정 변경 이력을 읽지 못했습니다.")
            error = "변경 이력을 읽지 못했습니다. DB 상태를 확인해 주세요."
    rows = [{"key": key, "label": label, "value": settings.get(key, ""),
             "default": settings.default_value(key, ""),
             "source": "화면에서 바꿈" if key in settings.overrides else "기본값",
             "choices": choices(settings, key)} for key, label in SETTING_LABELS.items()]
    return {"settings": rows, "history": history, "running_runs": running,
            "can_edit": can_edit(request), "port": app_bind(settings)[1], "error": error}


@router.get("/values")
def values(request: Request):
    return _state(request)


@router.get("", response_class=HTMLResponse)
def screen(request: Request):
    return templates.TemplateResponse(request=request, name="settings_ai.html", context=_state(request))


@router.post("")
def save(request: Request, values: dict = Body(...), client: str = Depends(require_local)):
    settings = get_settings()
    try:
        validate(values, settings)
        SettingOverrides(settings.root / "data/app.db").save(values, settings, client)
        reload_settings()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (sqlite3.Error, OSError) as exc:
        raise HTTPException(503, "설정을 저장하지 못했습니다. DB 상태를 확인해 주세요.") from exc
    return _state(request)


class ResetBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    keys: list[str]


@router.post("/reset")
def reset(request: Request, body: ResetBody, client: str = Depends(require_local)):
    settings = get_settings()
    if not body.keys or any(key not in ALLOWED_KEYS for key in body.keys):
        raise HTTPException(422, "바꿀 수 없는 설정 키입니다.")
    try:
        SettingOverrides(settings.root / "data/app.db").reset(body.keys, settings, client)
        reload_settings()
    except (sqlite3.Error, OSError) as exc:
        raise HTTPException(503, "설정을 저장하지 못했습니다. DB 상태를 확인해 주세요.") from exc
    return _state(request)
