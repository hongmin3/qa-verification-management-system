"""Claude 사용량 남은 양 보기 (SPEC REQ-AICALL-006).

서버에 로그인된 Claude CLI 계정의 5시간·주간 사용률을 화면에 보인다. 두 곳에서 값을 얻는다.

1. 지금 조회(`live`): CLI 인증 파일의 접근 토큰으로 사용량 조회 주소를 부른다. 공식 문서에 없는
   주소라 응답 모양이 바뀌면 읽을 수 있는 칸만 채운다.
2. 마지막 점검 값(`last_run`): 일일 점검이 CLI stream-json 출력에서 받은 `rate_limit_event` 를
   `data/claude_usage.json` 에 남긴 것.

토큰은 읽기만 한다. 새로 받는(refresh) 일은 CLI 가 한다 — 둘이 동시에 받으면 한쪽 로그인이 끊긴다.
접근 토큰은 몇 시간 뒤 만료되고 CLI 는 실행될 때만 새로 받는다. 그래서 조회가 "만료"로 실패하면
아주 작은 CLI 호출을 한 번 해서 CLI 가 새로 받게 한 뒤 다시 조회한다(설정한 간격에 한 번까지).
토큰 값은 응답·로그·저장 파일 어디에도 남기지 않는다.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

logger = logging.getLogger("regression_analyzer")

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
TIMEOUT_SECONDS = 5
KST = ZoneInfo("Asia/Seoul")
WINDOWS = ("five_hour", "seven_day")

HttpGet = Callable[[str, dict, int], tuple[int, str]]

_lock = threading.Lock()
_cache: dict[str, Any] = {}


def reset_cache() -> None:
    with _lock:
        _cache.clear()


def _settings(settings):
    if settings is None:
        from app.core.config import get_settings

        settings = get_settings()
    return settings


def state_path(settings=None) -> Path:
    settings = _settings(settings)
    return Path(settings.root) / "data" / "claude_usage.json"


def _credentials_path(settings) -> Path:
    raw = str(settings.get("ai.claude.usage.credentials_path", "") or "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".claude" / ".credentials.json"


def _kst(moment: datetime | None) -> str | None:
    return moment.astimezone(KST).strftime("%Y-%m-%d %H:%M") if moment else None


def _parse_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        text = str(value).strip().replace("Z", "+00:00")
        moment = datetime.fromisoformat(text)
        return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def _window(utilization: Any, resets_at: Any, *, fraction: bool) -> dict | None:
    """사용률을 0~100 으로 맞춘 한 창. 사용률을 못 읽으면 None."""
    if isinstance(utilization, bool) or not isinstance(utilization, (int, float)):
        return None
    used = float(utilization) * (100.0 if fraction else 1.0)
    used = round(min(max(used, 0.0), 100.0), 1)
    return {"used_percent": used, "remaining_percent": round(100.0 - used, 1), "resets_at": _kst(_parse_time(resets_at))}


def _redact(text: str, token: str) -> str:
    return text.replace(token, "<token>") if token else text


def _default_http_get(url: str, headers: dict, timeout: int) -> tuple[int, str]:
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")


def _read_token(settings) -> str:
    data = json.loads(_credentials_path(settings).read_text(encoding="utf-8"))
    return str((data.get("claudeAiOauth") or {}).get("accessToken") or "")


def _fetch_live(settings, http_get: HttpGet) -> tuple[dict | None, str]:
    """(창 두 개, 오류 한 줄). 실패하면 (None, 이유)."""
    try:
        token = _read_token(settings)
    except FileNotFoundError:
        return None, "로그인 정보 없음"
    except (OSError, ValueError, AttributeError):
        return None, "로그인 정보를 읽지 못함"
    if not token:
        return None, "로그인 정보 없음"
    headers = {"Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20", "Accept": "application/json"}
    try:
        status, body = http_get(USAGE_URL, headers, TIMEOUT_SECONDS)
    except Exception as error:  # noqa: BLE001 — 네트워크 오류는 화면에 한 줄로만 보인다
        logger.info("claude_usage_fetch_failed %s", _redact(type(error).__name__, token))
        return None, "조회 실패"
    if status == 401:
        return None, "로그인 토큰 만료"
    if status == 403:
        return None, "조회 권한 없음"
    if status != 200:
        return None, f"조회 실패 (HTTP {status})"
    try:
        payload = json.loads(body)
    except ValueError:
        return None, "응답을 읽지 못함"
    windows = {}
    for name in WINDOWS:
        item = payload.get(name) if isinstance(payload, dict) else None
        windows[name] = _window(item.get("utilization"), item.get("resets_at"), fraction=False) if isinstance(item, dict) else None
    return windows, ""


def _refresh_with_cli(settings) -> int:
    """아주 작은 CLI 호출 한 번. CLI 가 만료된 접근 토큰을 새로 받는다. 쓴 토큰 수를 돌려준다."""
    from app.core.claude_cli import request_structured

    model = str(settings.get("ai.claude.usage.refresh_model", "claude-haiku-5-5") or "claude-haiku-5-5")
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
    result = request_structured("ok", system_prompt='Return {"ok": true}.', schema=schema, model=model, settings=settings)
    return int((result.get("token_usage") or {}).get("total_tokens") or 0)


def _write_state(settings, state: dict) -> None:
    path = state_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def _load_state(settings) -> dict:
    try:
        data = json.loads(state_path(settings).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def record_rate_limit_event(info: dict, settings=None, now: datetime | None = None) -> None:
    """일일 점검이 받은 `rate_limit_event.rate_limit_info` 를 저장한다. 다른 창의 앞선 값은 남긴다."""
    if not isinstance(info, dict):
        return
    settings = _settings(settings)
    now = now or datetime.now(timezone.utc)
    state = _load_state(settings)
    windows = dict(state.get("windows") or {})
    unified = info.get("unifiedWindows")
    if isinstance(unified, dict):
        for name in WINDOWS:
            item = unified.get(name)
            if isinstance(item, dict):
                window = _window(item.get("utilization"), item.get("resetsAt"), fraction=True)
                if window:
                    windows[name] = window
    kind = info.get("rateLimitType")
    if kind in WINDOWS:
        window = _window(info.get("utilization"), info.get("resetsAt"), fraction=True)
        if window:
            windows[kind] = window
    if not windows:
        return
    _write_state(settings, {**state, "windows": windows, "measured_at": _kst(now)})
    reset_cache()      # 120초 안에 다시 불러도 새로 받은 값을 보이게 한다


def _maybe_refresh(settings, now: datetime, refresh) -> bool:
    """만료된 토큰을 CLI 로 새로 받게 한다. 간격 안에 이미 시도했으면 하지 않는다."""
    if not bool(settings.get("ai.claude.usage.refresh_with_cli", True)):
        return False
    interval = int(settings.get("ai.claude.usage.refresh_interval_minutes", 60) or 60)
    state = _load_state(settings)
    last = _parse_time(state.get("last_refresh_attempt"))
    if last and (now - last).total_seconds() < interval * 60:
        return False
    state["last_refresh_attempt"] = now.isoformat()
    _write_state(settings, state)
    try:
        tokens = int((refresh or _refresh_with_cli)(settings) or 0)
    except Exception as error:  # noqa: BLE001 — 갱신 실패는 "만료" 로만 보인다
        logger.info("claude_usage_token_refresh_failed %s", type(error).__name__)
        return False
    state = _load_state(settings)
    state.update({"last_refresh_at": _kst(now), "last_refresh_tokens": tokens})
    _write_state(settings, state)
    logger.info("claude_usage_token_refresh tokens=%s", tokens)
    return True


def get_usage(settings=None, *, now: datetime | None = None, http_get: HttpGet | None = None, refresh=None) -> dict:
    """`GET /claude/usage` 의 응답 (REQ-AICALL-006 결과 표)."""
    settings = _settings(settings)
    now = now or datetime.now(timezone.utc)
    empty = {"source": "none", "five_hour": None, "seven_day": None, "measured_at": None, "error": ""}
    if not bool(settings.get("ai.claude.usage.enabled", True)):
        return {**empty, "error": "사용량 조회를 꺼 두었습니다"}
    cache_seconds = int(settings.get("ai.claude.usage.cache_seconds", 120) or 120)
    with _lock:
        cached = _cache.get("value")
        if cached and (now - _cache["at"]).total_seconds() < cache_seconds:
            return dict(cached)
    http_get = http_get or _default_http_get
    windows, error = _fetch_live(settings, http_get)
    if windows is None and error == "로그인 토큰 만료" and _maybe_refresh(settings, now, refresh):
        windows, error = _fetch_live(settings, http_get)
    if windows is not None:
        result = {"source": "live", **windows, "measured_at": _kst(now), "error": ""}
    else:
        state = _load_state(settings)
        saved = state.get("windows") or {}
        if any(saved.get(name) for name in WINDOWS):
            result = {"source": "last_run", "five_hour": saved.get("five_hour"), "seven_day": saved.get("seven_day"),
                      "measured_at": state.get("measured_at"), "error": error}
        else:
            result = {**empty, "error": error}
    with _lock:
        _cache["value"], _cache["at"] = dict(result), now
    return result
