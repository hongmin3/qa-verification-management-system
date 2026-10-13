"""Validates: REQ-QAAGENT-016 / TEST-QAAGENT-011. No external AI, mail, or server calls."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.storage import Storage
from app.modules.daily_qa.store import DailyQaStore
from app.modules.qa_agent import dashboard, router
from tests.daily_qa_fixtures import make_settings

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def retired_app(tmp_path, monkeypatch):
    import app.main as main
    cfg = make_settings(tmp_path / "root", tmp_path / "workspace")
    store = Storage(cfg.db_path)
    store.ensure_product("VXvue")
    preserved = {"issue_id": "VP-100", "token_usage": {"total_tokens": 23}, "summary": "Synthetic archived result"}
    store.create_analysis("legacy", module="qa_agent", request={"product": "VXvue"})
    store.update_analysis("legacy", "DONE", result=preserved)
    store.save_qa_agent_approval("legacy", "issue", "VP-100", "APPROVED", "Synthetic note")
    request = {"product": "VXvue", "issue_id": "VP-100"}
    store.create_analysis("queued-retired", module="qa_agent", request=request)
    calls = {"legacy_recovery": 0, "scheduler": []}
    monkeypatch.setattr(main, "storage", store)
    monkeypatch.setattr(router, "storage", store)
    monkeypatch.setattr(main, "_ensure_configured_products", lambda: None)
    monkeypatch.setattr(main, "resume_queued_manual_jobs", lambda: None)
    monkeypatch.setattr(main, "start_scheduler", lambda registrars: calls["scheduler"].append(registrars))
    monkeypatch.setattr(main, "stop_scheduler", lambda: None)
    if hasattr(main, "resume_queued_qa_agent_jobs"):
        def legacy_recovery(): calls["legacy_recovery"] += 1
        monkeypatch.setattr(main, "resume_queued_qa_agent_jobs", legacy_recovery)
    if hasattr(router, "QaAgentAnalyzer"):
        class InertAnalyzer:
            def __init__(self, **kwargs): pass
            def available_issue_ids(self, product): return []
        monkeypatch.setattr(router, "QaAgentAnalyzer", InertAnalyzer)
    if hasattr(router, "_product_readiness"):
        monkeypatch.setattr(router, "_product_readiness", lambda product: {})
    choice = dashboard.ProductChoice(cfg, DailyQaStore(storage=store), ["VXvue"])
    monkeypatch.setattr(dashboard, "choose", lambda product="": choice)
    monkeypatch.setattr(dashboard, "_documents_storage", lambda cfg: store)
    with TestClient(main.app) as client:
        yield client, store, calls, preserved, request


@pytest.mark.parametrize("method,path", [
    ("GET", "/qa-agent/issue-analysis"), ("GET", "/qa-agent/analyses"),
    ("GET", "/qa-agent/analyses/legacy"), ("GET", "/qa-agent/analyses/legacy/export"),
    ("GET", "/qa-agent/issues?product=VXvue"), ("GET", "/qa-agent/readiness?product=VXvue"),
    ("POST", "/qa-agent/analyses"), ("POST", "/qa-agent/analyses/legacy/approve"),
])
def test_retired_ui_and_apis_are_404(retired_app, method, path):
    client, _, _, _, _ = retired_app
    response = client.request(method, path, data={"product": "VXvue", "claim_kind": "issue",
                              "claim_label": "VP-100", "qa_decision": "APPROVED"} if method == "POST" else None)
    assert response.status_code == 404


@pytest.mark.parametrize("path", ["/", "/qa-agent", "/qa-agent/guide", "/qa-agent/rules"])
def test_current_pages_offer_no_retired_workflow(retired_app, path):
    response = retired_app[0].get(path)
    assert response.status_code == 200
    assert "/qa-agent/issue-analysis" not in response.text
    assert "/qa-agent/analyses" not in response.text
    assert "단일 이슈 분석" not in response.text


def test_actual_app_lifespan_preserves_history_and_never_resumes_retired_jobs(retired_app):
    client, store, calls, preserved, request = retired_app
    assert client.get("/health").json() == {"status": "ok"}
    assert calls["legacy_recovery"] == 0
    assert store.get_analysis("legacy")["result"] == preserved
    assert store.qa_agent_approval_map("legacy")[("issue", "VP-100")]["qa_decision"] == "APPROVED"
    queued = store.get_analysis("queued-retired")
    assert queued["status"] == "FAILED" and queued["request"] == request
    assert "기능이 제거" in queued["error"]
    assert store.active_analysis_count() == 0
    import app.main as main
    assert main.register_knowledge_jobs in calls["scheduler"][0]
    assert main.register_daily_qa_jobs in calls["scheduler"][0]


def test_dedicated_engine_assets_removed_and_common_rules_axes_parser_retained():
    for name in ("analyzer.py", "ai_client.py", "gates.py", "evidence.py", "validation.py", "standard_findings.py", "approval_history.py"):
        assert not (ROOT / "app/modules/qa_agent" / name).exists()
    for name in ("index.html", "analysis.html", "history.html"):
        assert not (ROOT / "app/modules/qa_agent/templates" / name).exists()
    assert not (ROOT / "app/prompts/qa_agent_issue_impact.yaml").exists()
    assert not (ROOT / "scripts/compare_models.py").exists()
    from app.modules.qa_agent.schemas import AXIS_LABELS, REGRESSION_AXES
    from app.core.qa_rules import RuleSet
    from app.parsers.polarion_issue import html_to_text, split_marked_sections
    assert len(REGRESSION_AXES) == 8 and AXIS_LABELS["GENERATOR"] == "Generator"
    assert RuleSet and split_marked_sections
    assert html_to_text("<p>synthetic</p>")[0] == "synthetic"


def test_retired_model_selector_removed_and_common_model_services_remain():
    """Validates: REQ-QAAGENT-016; legacy REQ-AICALL-004 retired."""
    from app.core import model_router
    for name in ("route_qa_analysis", "looks_like_integration", "RoutingDecision", "TIER_LABELS", "INTEGRATION_TERMS"):
        assert not hasattr(model_router, name), name
    assert model_router.model_for(model_router.TIER_STANDARD)
    assert model_router.ai_provider() in ("claude_cli", "gemini")
    assert model_router.estimate_cost_usd("gemini-2.5-flash", 0, 0) == 0
