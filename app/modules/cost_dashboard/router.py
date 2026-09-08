from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.core.config import get_settings
from app.core.storage import Storage
from app.modules.impact_analyzer.router import daily_token_status

router = APIRouter()
templates = Jinja2Templates(directory=[Path(__file__).parent / "templates", get_settings().root / "app" / "web" / "templates"])
storage = Storage()

MODULE_LABELS = {"impact_analyzer": "Regression 영향 분석", "manual_review": "매뉴얼 개정 검증"}


@router.get("/cost-dashboard/guide", response_class=HTMLResponse)
def cost_dashboard_guide(request: Request):
    return templates.TemplateResponse(request, "guide.html", {})


@router.get("/cost-dashboard", response_class=HTMLResponse)
def cost_dashboard(request: Request, days: int = 30):
    stats = storage.cost_dashboard_stats(days=days)
    max_daily_tokens = max((item["tokens"] for item in stats["daily"]), default=0)
    return templates.TemplateResponse(
        request,
        "cost_dashboard.html",
        {
            "stats": stats,
            "days": days,
            "module_labels": MODULE_LABELS,
            "daily_token_status": daily_token_status(),
            "max_daily_tokens": max_daily_tokens,
        },
    )
