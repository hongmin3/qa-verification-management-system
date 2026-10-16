"""설정 탭(`/settings`)과 관리자 로그인 (REQ-WEB-008, REQ-AICALL-007, NFR-SEC-004).

저장·시험 발송은 관리자 로그인 쿠키 또는 서버 직접 접속(SSH 터널 포함)일 때만 받는다.
화면과 응답에는 가린 메일 주소만 보이고, 로그인한 관리자 자신의 입력칸에만 원래 주소가 들어간다.
비밀번호 원문과 암호화 값은 어디에도 내지 않는다. 설정 조회·저장·시험 발송은 Claude 를 부르지 않는다.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core import admin_auth, notifier
from app.core.config import app_bind, get_settings, reload_settings
from app.core.setting_overrides import (
    ALLOWED_KEYS, CHOICE_KEYS, EMAIL_KEY, EMAIL_LIST_KEY, MASKED_KEYS, PASSWORD_KEY, PERCENT_KEYS, SETTING_LABELS,
    SettingOverrides, choices, default_of, effective_of, normalize, validate,
)
from app.modules.daily_qa.settings import report_recipients

logger = logging.getLogger(__name__)
router = APIRouter(tags=["설정"])
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

SECTIONS = ("login", "admin-mail", "recipients", "test-mail", "ai", "base-url", "password", "history")
#: 주소 뒤에 붙는 안내 문구는 이 표의 고정 문장만 쓴다. 입력받은 글을 화면에 되돌려 쓰지 않는다.
NOTICES = {
    "saved": "저장했습니다. 다음 AI 호출과 다음 점검 실행부터 새 값을 씁니다.",
    "nochange": "바뀐 설정이 없습니다.",
    "reset": "기본값으로 되돌렸습니다. 다음 실행부터 적용됩니다.",
    "password": "비밀번호를 바꿨습니다.",
    "login": "비밀번호가 맞지 않습니다.",
    "locked": "비밀번호를 5번 틀려 10분 동안 로그인을 받지 않습니다. 잠시 뒤 다시 시도해 주세요.",
}
MAIL_RESULTS = {
    "sent": "시험 메일을 보냈습니다.",
    "not_configured": "메일 설정이 없어 보내지 않았습니다.",
    "failed": "시험 메일 발송에 실패했습니다.",
}
TEST_TARGETS = {"admin": "관리자 메일", "report": "자동 보고 메일 받는 사람"}
PASSWORD_LABEL = "관리자 비밀번호"


def _same_origin(request: Request) -> bool:
    """`Origin` 이 있으면 요청을 받은 주소와 같은 출처여야 한다. 없으면(주소창 이동·명령줄) 통과한다."""
    origin = request.headers.get("origin")
    if not origin:
        return True
    parsed = urlsplit(origin)
    return (parsed.scheme, parsed.netloc) == (request.url.scheme, request.url.netloc)


def can_edit(request: Request) -> bool:
    """서버 직접 접속(SSH 터널 포함)이면 로그인 없이 저장할 수 있다 (REQ-AICALL-007).

    serve.py 는 proxy_headers=False 로 scope.client 를 TCP peer 그대로 유지한다.
    nginx 는 루프백으로 접속해도 전달 헤더를 붙이므로 아래 조건으로 거절된다.
    """
    if not request.client or request.client.host not in ("127.0.0.1", "::1"):
        return False
    if any(key == "forwarded" or key == "x-real-ip" or key.startswith("x-forwarded-")
           for key in request.headers):
        return False
    return _same_origin(request)


def admin_session(request: Request) -> tuple[str, str] | None:
    """(행위자 이름, 방법). 방법은 `cookie`(관리자 로그인)와 `local`(서버 직접 접속). 둘 다 아니면 None."""
    cookie = request.cookies.get(admin_auth.COOKIE_NAME)
    if cookie and _same_origin(request) and admin_auth.verify(cookie, time.time(), get_settings().root):
        return "관리자 로그인", "cookie"
    if can_edit(request):
        return request.client.host, "local"
    return None


def require_admin(request: Request) -> str:
    """관리자 쿠키 또는 서버 직접 접속이면 행위자 이름을 돌려주고, 아니면 403."""
    session = admin_session(request)
    if session is None:
        raise HTTPException(403, "설정 저장은 관리자 로그인 또는 서버 직접 접속에서만 가능합니다.")
    return session[0]


def _require_same_origin(request: Request) -> None:
    if not _same_origin(request):
        raise HTTPException(403, "다른 주소에서 보낸 요청은 받지 않습니다.")


def _read_db(settings) -> tuple[list[dict], list[str], str]:
    db_path = settings.root / "data/app.db"
    history, running, error = [], [], ""
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
            logger.warning("설정 변경 이력을 읽지 못했습니다.")
            error = "변경 이력을 읽지 못했습니다. DB 상태를 확인해 주세요."
    return history, running, error


def _mask_list(addresses) -> str:
    return ", ".join(notifier.mask_address(item) for item in addresses)


def _effective(settings, key: str) -> tuple[str, str, str]:
    """(원래 값, 화면에 보이는 값, 출처). 메일 주소 키의 화면 값은 가린 주소다."""
    overridden = key in settings.overrides
    if key == EMAIL_KEY:
        addresses = notifier.admin_recipients(settings)
        source = "화면에서 바꿈" if overridden else ("비밀 설정 NOTIFY_EMAIL_TO 의 첫 주소" if addresses else "설정 없음")
        return ", ".join(addresses), _mask_list(addresses), source
    if key == EMAIL_LIST_KEY:
        addresses = report_recipients(settings)
        secrets = settings.secrets
        if overridden:
            source = "화면에서 바꿈"
        elif str(secrets.daily_qa_email_to or "").strip():
            source = "비밀 설정 DAILY_QA_EMAIL_TO"
        elif str(secrets.notify_email_to or "").strip():
            source = "비밀 설정 NOTIFY_EMAIL_TO"
        else:
            source = "관리자 메일" if addresses else "설정 없음"
        return ", ".join(addresses), _mask_list(addresses), source
    value = effective_of(settings, key)
    return value, value, "화면에서 바꿈" if overridden else "기본값"


def _rows(settings) -> list[dict]:
    rows = []
    for key, label in SETTING_LABELS.items():
        raw, shown, source = _effective(settings, key)
        default = "" if key in MASKED_KEYS else default_of(settings, key)
        kind = ("choice" if key in CHOICE_KEYS else "percent" if key in PERCENT_KEYS
                else "email" if key == EMAIL_KEY else "emails" if key == EMAIL_LIST_KEY else "url")
        rows.append({"key": key, "label": label, "kind": kind, "value": shown, "default": default, "source": source,
                     "choices": choices(settings, key), "_raw": raw})
    return rows


def _state(request: Request) -> dict:
    settings = get_settings()
    history, running, error = _read_db(settings)
    session = admin_session(request)
    rows = _rows(settings)
    return {"settings": [{k: v for k, v in row.items() if k != "_raw"} for row in rows],
            "history": history, "running_runs": running, "can_edit": session is not None,
            "is_admin": session is not None, "port": app_bind(settings)[1], "error": error}


def _context(request: Request, notice: str = "", notice_kind: str = "info", mail_result: dict | None = None,
             status_section: str = "") -> dict:
    settings = get_settings()
    state = _state(request)
    session = admin_session(request)
    rows = _rows(settings)
    labels = {**SETTING_LABELS, PASSWORD_KEY: PASSWORD_LABEL}
    context = {**state, "by_key": {row["key"]: row for row in rows}, "actor": session[0] if session else "",
               "via": session[1] if session else "", "labels": labels, "notice": notice, "notice_kind": notice_kind,
               "mail_result": mail_result or {}, "status_section": status_section,
               "edit": {row["key"]: row["_raw"] for row in rows} if session else {},
               "recipient_masks": [notifier.mask_address(item) for item in report_recipients(settings)],
               "admin_masks": [notifier.mask_address(item) for item in notifier.admin_recipients(settings)],
               "mail_kinds": [("report", "예약 점검 요약 (예시)"), ("change_report", "변경 리포트 (예시)"), *notifier.KIND_LABELS.items()],
               "test_targets": TEST_TARGETS, "sections": SECTIONS}
    return context


def _render(request: Request, status: int = 200, **kwargs) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="settings.html", context=_context(request, **kwargs),
                                      status_code=status)


async def _payload(request: Request) -> tuple[dict, bool]:
    """요청 본문을 `(값 묶음, 양식 여부)` 로 읽는다. JSON 이면 그대로, 양식이면 글 값만 모은다."""
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype == "application/json":
        try:
            data = await request.json()
        except ValueError as exc:
            raise HTTPException(422, "요청 형식을 확인해 주세요.") from exc
        return data, False
    form = await request.form()
    data: dict = {}
    for key in form.keys():
        values = [item for item in form.getlist(key) if isinstance(item, str)]
        data[key] = values if key.startswith("_") and key != "_section" else (values[-1] if values else "")
    return data, True


def _section(data: dict, default: str = "") -> str:
    value = data.get("_section")
    return value if isinstance(value, str) and value in SECTIONS else default


def _back(path_query: str, section: str = "") -> RedirectResponse:
    return RedirectResponse(f"/settings?{path_query}" + (f"#{section}" if section else ""), status_code=303)


@router.get("/settings/ai")
def old_ai_settings() -> RedirectResponse:
    """옛 `AI 분석 설정` 주소. 설정 탭의 `AI 모델과 사용률 기준` 구획으로 보낸다."""
    return RedirectResponse("/settings#ai", status_code=303)


@router.get("/settings/values")
def values(request: Request):
    return _state(request)


@router.get("/settings", response_class=HTMLResponse)
def screen(request: Request, saved: str = "", reset: str = "", password: str = "", error: str = ""):
    notice, kind = "", "info"
    if error in ("login", "locked"):
        notice, kind = NOTICES[error], "error"
    elif saved in ("1", "0"):
        notice = NOTICES["saved" if saved == "1" else "nochange"]
    elif reset == "1":
        notice = NOTICES["reset"]
    elif password == "1":
        notice = NOTICES["password"]
    return _render(request, notice=notice, notice_kind=kind)


@router.post("/settings/login")
async def login(request: Request):
    _require_same_origin(request)
    data, _ = await _payload(request)
    plain = data.get("password") if isinstance(data, dict) else None
    settings = get_settings()
    outcome = await run_in_threadpool(admin_auth.attempt_login, plain, settings, time.time())
    if outcome != "ok":
        return _back(f"error={'locked' if outcome == 'locked' else 'login'}")
    response = RedirectResponse("/settings", status_code=303)
    response.set_cookie(admin_auth.COOKIE_NAME, admin_auth.issue(time.time(), settings.root),
                        max_age=admin_auth.SESSION_SECONDS, path="/", httponly=True, samesite="strict")
    return response


@router.post("/settings/logout")
def logout(request: Request):
    _require_same_origin(request)
    response = RedirectResponse("/settings", status_code=303)
    response.delete_cookie(admin_auth.COOKIE_NAME, path="/", httponly=True, samesite="strict")
    return response


def _current_raw(settings, key: str) -> str:
    return _effective(settings, key)[0]


@router.post("/settings")
async def save(request: Request, actor: str = Depends(require_admin)):
    data, is_form = await _payload(request)
    settings = get_settings()
    section = _section(data) if is_form else ""
    try:
        if not isinstance(data, dict):
            raise ValueError("바꿀 설정을 선택해 주세요.")
        submitted = {key: value for key, value in data.items() if not key.startswith("_")} if is_form else data
        if PASSWORD_KEY in submitted:
            raise ValueError("비밀번호는 `관리자 비밀번호 변경`에서만 바꿀 수 있습니다.")
        submitted = normalize(submitted)
        if is_form:
            # 양식은 칸 전체를 보내므로 값이 그대로인 칸은 저장하지 않는다(처음 값이 비어 있는 칸이 오류가 되지 않게).
            submitted = {key: value for key, value in submitted.items()
                         if key not in ALLOWED_KEYS or str(value) != _current_raw(settings, key)}
            if not submitted:
                return _back("saved=0", section)
        validate(submitted, settings)
        SettingOverrides(settings.root / "data/app.db").save(submitted, settings, actor)
        reload_settings()
    except ValueError as exc:
        if is_form:
            return _render(request, 422, notice=str(exc), notice_kind="error", status_section=section)
        raise HTTPException(422, str(exc)) from exc
    except (sqlite3.Error, OSError) as exc:
        raise HTTPException(503, "설정을 저장하지 못했습니다. DB 상태를 확인해 주세요.") from exc
    return _back("saved=1", section) if is_form else _state(request)


@router.post("/settings/reset")
async def reset(request: Request, actor: str = Depends(require_admin)):
    data, is_form = await _payload(request)
    keys = data.get("_keys", []) if is_form else (data.get("keys") if isinstance(data, dict) else None)
    settings = get_settings()
    if (not isinstance(keys, list) or not keys or any(not isinstance(key, str) or key not in ALLOWED_KEYS or key == PASSWORD_KEY
                                                       for key in keys)):
        if is_form:
            return _render(request, 422, notice="바꿀 수 없는 설정 키입니다.", notice_kind="error")
        raise HTTPException(422, "바꿀 수 없는 설정 키입니다.")
    try:
        SettingOverrides(settings.root / "data/app.db").reset(keys, settings, actor)
        reload_settings()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (sqlite3.Error, OSError) as exc:
        raise HTTPException(503, "설정을 저장하지 못했습니다. DB 상태를 확인해 주세요.") from exc
    return _back("reset=1", _section(data) if is_form else "") if is_form else _state(request)


@router.post("/settings/password")
async def change_password(request: Request, actor: str = Depends(require_admin)):
    data, is_form = await _payload(request)
    settings = get_settings()
    new = data.get("password") if isinstance(data, dict) else None
    try:
        if is_form and data.get("confirm", new) != new:
            raise ValueError("비밀번호 확인 칸이 새 비밀번호와 다릅니다.")
        await run_in_threadpool(admin_auth.set_password, new, actor, settings)
        reload_settings()
    except ValueError as exc:
        if is_form:
            return _render(request, 422, notice=str(exc), notice_kind="error", status_section="password")
        raise HTTPException(422, str(exc)) from exc
    except (sqlite3.Error, OSError) as exc:
        raise HTTPException(503, "설정을 저장하지 못했습니다. DB 상태를 확인해 주세요.") from exc
    return _back("password=1", "password") if is_form else _state(request)


@router.post("/settings/test-mail")
async def test_mail(request: Request, actor: str = Depends(require_admin)):
    data, is_form = await _payload(request)
    settings = get_settings()
    kind = data.get("kind") if isinstance(data, dict) else None
    target = data.get("target") if isinstance(data, dict) else None
    valid_kinds = {"report", "change_report", *notifier.KIND_LABELS}
    problem = ""
    if kind not in valid_kinds:
        problem = "알 수 없는 시험 종류입니다."
    elif target not in TEST_TARGETS:
        problem = "받을 곳을 골라 주세요."
    recipients = (notifier.admin_recipients(settings) if target == "admin" else report_recipients(settings)) if not problem else ()
    if not problem and not recipients:
        problem = f"{TEST_TARGETS[target]}에 등록된 주소가 없습니다."
    if problem:
        if is_form:
            return _render(request, 422, notice=problem, notice_kind="error", status_section="test-mail")
        raise HTTPException(422, problem)
    result = await run_in_threadpool(notifier.send_test, kind, tuple(recipients))
    status = result.get("status") if isinstance(result, dict) else "failed"
    masks = [str(item) for item in (result.get("recipient_masks") or [])] if isinstance(result, dict) else []
    summary = {"status": status, "message": MAIL_RESULTS.get(status, MAIL_RESULTS["failed"]), "masks": masks}
    if status == "failed" and isinstance(result, dict) and result.get("error"):
        summary["error"] = str(result["error"])[:300]
    logger.info("settings_test_mail kind=%s target=%s status=%s", kind, target, status)
    if is_form:
        return _render(request, 200, mail_result=summary, status_section="test-mail")
    return {**summary, **_state(request)}
