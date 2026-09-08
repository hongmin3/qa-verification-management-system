"""여러 modules/* 의 라우터를 하나의 FastAPI 앱에 취합한다. 이 파일 자체는 비즈니스
로직을 갖지 않는다 — 어떤 모듈이 어떤 URL prefix를 쓰는지만 결정한다."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.config import get_settings
from app.modules.cost_dashboard.router import router as cost_dashboard_router
from app.modules.impact_analyzer.router import router as impact_analyzer_router
from app.modules.knowledge.router import router as knowledge_router
from app.modules.manual_review.router import router as manual_review_router
from app.modules.qa_agent.router import router as qa_agent_router


templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def external_services() -> list[dict[str, str]]:
    """config.yaml `services.*` 중 URL이 설정된 하위 서비스만 허브 카드로 노출한다.

    핵심 앱과 같은 프로세스에서 뜨지 않는 별도 배포 단위(예: services/qa-manual-hub)를
    링크로만 연결한다. URL이 비어 있으면 카드를 만들지 않으므로, 하위 서비스를
    배포하지 않은 환경에서는 깨진 링크가 노출되지 않는다."""
    configured = get_settings().get("services") or {}
    cards: list[dict[str, str]] = []
    for key, value in configured.items():
        if not isinstance(value, dict):
            continue
        url = str(value.get("url") or "").strip()
        if not url:
            continue
        cards.append(
            {
                "key": key,
                "name": str(value.get("name") or key),
                "description": str(value.get("description") or ""),
                "url": url,
            }
        )
    return cards


def relative_service_urls() -> dict[str, str]:
    """URL이 상대 경로인 하위 서비스만 골라 {첫 경로 조각: URL} 로 돌려준다.

    상대 경로는 "같은 호스트의 nginx가 이 경로를 하위 서비스로 프록시한다"는 뜻이다.
    그런데 이 앱은 nginx 없이 자기 포트로도 직접 접속할 수 있고(예: :12000), 그때는
    같은 링크가 이 앱으로 들어와 404가 된다. 그 경우를 처리하기 위해 필요하다."""
    prefixes: dict[str, str] = {}
    for card in external_services():
        url = card["url"]
        if not url.startswith("/"):
            continue
        segment = url.strip("/").split("/")[0]
        if segment:
            prefixes[segment] = url
    return prefixes


def service_fallback(request: Request):
    """앱 포트로 직접 들어온 하위 서비스 요청을 nginx origin(포트 없는 같은 호스트)으로 보낸다.

    nginx를 거쳐 들어오면 이 경로는 애초에 이 앱까지 오지 않으므로, 이 라우트가 타는 것은
    사용자가 앱 포트로 직접 접속한 경우뿐이다. 홈 화면의 하위 서비스 카드가 접속 경로에
    상관없이 동작하게 만든다.

    요청에 명시적 포트가 없으면 이미 기본 포트(=nginx)로 들어온 것이므로 되돌려 보내지
    않는다. 그대로 리다이렉트하면 같은 주소로 무한히 돌게 되고, 진짜 원인(nginx에
    하위 서비스 경로가 설정되지 않음)이 가려진다."""
    if request.url.port is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"{request.url.path} 는 이 앱이 서비스하지 않습니다. "
                "같은 호스트의 nginx가 이 경로를 하위 서비스로 프록시하도록 설정되어야 합니다 "
                "(deploy/nginx/qa-platform.conf 참고)."
            ),
        )
    hostname = request.url.hostname or "localhost"
    target = f"{request.url.scheme}://{hostname}{request.url.path}"
    if request.url.query:
        target = f"{target}?{request.url.query}"
    return RedirectResponse(target, status_code=307)


def hub(request: Request):
    """QA 자동화 기능을 선택하는 공용 진입점."""
    return templates.TemplateResponse(
        request, "hub.html", {"external_services": external_services()}
    )


def build_router() -> APIRouter:
    api = APIRouter()
    api.add_api_route("/", hub, methods=["GET"], response_class=HTMLResponse, name="hub")
    for segment in relative_service_urls():
        # 이 앱은 하위 서비스를 실제로 서비스하지 않는다. 앱 포트로 직접 들어온 요청만
        # nginx origin 으로 되돌려 보낸다.
        api.add_api_route(
            f"/{segment}" + "{rest:path}",
            service_fallback,
            methods=["GET"],
            name=f"service_fallback_{segment}",
        )
    api.include_router(knowledge_router)
    api.include_router(impact_analyzer_router)
    api.include_router(manual_review_router, prefix="/manual-review")
    api.include_router(qa_agent_router, prefix="/qa-agent")
    api.include_router(cost_dashboard_router)
    return api
