"""FastAPI application entry point.

In production nginx serves the built SPA and reverse-proxies ``/api`` here, so
there is no CORS to configure.  ``CORS_ORIGINS`` exists only for running the Vite
dev server against a live backend.
"""
from __future__ import annotations

import logging

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse

from .config import settings
from .deps import REFRESHED_TOKEN_STATE, get_current_user, set_session_cookie
from .models import User
from .routers import auth, catalog, dashboard, documents, search, users

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
)
log = logging.getLogger("qa_manual_hub")


@asynccontextmanager
async def lifespan(_: FastAPI):
    root = settings.storage_root
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:  # pragma: no cover - surfaced in the unit log
        log.error("storage root %s is not usable: %s", root, exc)
    log.info(
        "%s started (storage=%s, max_upload=%s MB, session=%sh)",
        settings.app_name,
        root,
        settings.max_upload_mb,
        settings.session_lifetime_hours,
    )
    yield


# The built-in docs routes are public, so they are switched off here and
# re-declared below behind the login check (README: "로그인 후 접근 가능").
app = FastAPI(
    title=settings.app_name,
    version=dashboard.APP_VERSION,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)

if settings.cors_origin_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


@app.middleware("http")
async def refresh_session_cookie(request: Request, call_next):
    """Re-send the session cookie whenever ``get_current_user`` extended the
    session, so the browser's copy lives as long as the server's."""
    response = await call_next(request)
    token = getattr(request.state, REFRESHED_TOKEN_STATE, None)
    if token:
        set_session_cookie(response, token)
    return response


@app.exception_handler(RequestValidationError)
async def validation_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Turn pydantic's nested error structure into one readable Korean line."""
    parts: list[str] = []
    for err in exc.errors():
        loc = [str(x) for x in err.get("loc", []) if x not in ("body", "query")]
        field = ".".join(loc) or "요청"
        parts.append(f"{field}: {err.get('msg', '유효하지 않은 값')}")
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": " / ".join(parts)},
    )


app.include_router(auth.router)
app.include_router(users.router)
app.include_router(catalog.products_router)
app.include_router(catalog.categories_router)
app.include_router(documents.router)
app.include_router(search.router)
app.include_router(dashboard.router)


@app.get("/api/openapi.json", include_in_schema=False)
def openapi_schema(_: User = Depends(get_current_user)) -> JSONResponse:
    return JSONResponse(app.openapi())


@app.get("/api/docs", include_in_schema=False)
def api_docs(_: User = Depends(get_current_user)) -> HTMLResponse:
    # Relative URL: resolves to /api/openapi.json on a standalone install and to
    # /manual-hub/api/openapi.json under the platform nginx prefix.
    return get_swagger_ui_html(openapi_url="openapi.json", title=f"{app.title} - API")


@app.get("/api/health", tags=["health"])
def health() -> dict[str, object]:
    """Unauthenticated liveness probe used by systemd checks and the deploy
    script.  Deliberately reveals nothing about the data."""
    return {"status": "ok", "app": settings.app_name, "version": dashboard.APP_VERSION}
