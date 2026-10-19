"""제품 상세의 `지금 수집` 요청과 자료 구역 숨김.

Validates: REQ-KNOW-021, REQ-KNOW-018, REQ-KNOW-020

운영 서버는 지식 폴더를 볼 수 없으므로 버튼은 요청만 남기고, 담당자 PC 의 1분 예약이 요청을 보고
바로 올린다. 설정·DB·수집 기록은 모두 tmp 에 둔다.
"""
from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.core.product_config as product_config
import app.core.product_knowledge as product_knowledge
import app.modules.knowledge.router as knowledge_router
from app.core import knowledge_collect_request, knowledge_retry
from app.core.storage import Storage
from app.main import app

ROOT = Path(__file__).resolve().parents[1]
NO_FOLDER_YAML = """
product: No Folder
specification:
  source: manual
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    products = tmp_path / "config" / "products"
    products.mkdir(parents=True)
    for name in ("vxvue.yaml", "bellalun-viewer.yaml"):
        shutil.copy(ROOT / "config" / "products" / name, products / name)
    (products / "no-folder.yaml").write_text(NO_FOLDER_YAML, encoding="utf-8")
    monkeypatch.setattr(product_config, "_products_dir", lambda root=None: products)
    monkeypatch.setattr(product_knowledge, "get_settings", lambda: SimpleNamespace(root=tmp_path))
    storage = Storage(tmp_path / "app.db")
    monkeypatch.setattr(knowledge_router, "storage", storage)
    # 운영 서버처럼 지식 폴더를 볼 수 없다.
    monkeypatch.setattr(knowledge_router, "source_available", lambda config: False)
    return SimpleNamespace(root=tmp_path, storage=storage, client=TestClient(app))


def test_collect_without_folder_access_leaves_a_request(env):
    response = env.client.post("/knowledge/products/vxvue/collect")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "REQUESTED"
    assert "1분 안에" in body["detail"]
    pending = env.client.get("/knowledge/product-knowledge/request", params={"product": "VXvue"}).json()
    assert pending["requested_at"]
    page = env.client.get("/knowledge/products/vxvue").text
    assert "수집 요청 대기 중" in page


def test_finished_folder_sync_after_request_clears_it(env):
    env.client.post("/knowledge/products/vxvue/collect")
    sync = env.storage.sync_start("VXvue", "product_knowledge", "upload")
    env.storage.sync_finish(sync, "SUCCESS", "등록 1건")
    pending = env.client.get("/knowledge/product-knowledge/request", params={"product": "VXvue"}).json()
    assert pending["requested_at"] == ""
    assert "수집 요청 대기 중" not in env.client.get("/knowledge/products/vxvue").text


def test_request_older_than_last_sync_is_not_pending(env):
    sync = env.storage.sync_start("VXvue", "product_knowledge", "upload")
    env.storage.sync_finish(sync, "SUCCESS", "등록 1건")
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    knowledge_collect_request.request_collect("VXvue", env.root, now=old)
    assert knowledge_collect_request.pending_request("VXvue", env.storage, env.root) == ""


def test_collect_with_folder_access_runs_sync_directly(env, monkeypatch):
    monkeypatch.setattr(knowledge_router, "source_available", lambda config: True)
    monkeypatch.setattr(knowledge_router, "sync_and_register",
                        lambda product, storage=None: {"status": "SUCCESS", "detail": "수집 1건", "duplicates": []})
    body = env.client.post("/knowledge/products/vxvue/collect").json()
    assert body["status"] == "SUCCESS"
    assert knowledge_collect_request.pending_request("VXvue", env.storage, env.root) == ""


def test_collect_refuses_product_without_folder_or_config(env):
    assert env.client.post("/knowledge/products/no-folder/collect").status_code == 400
    assert env.client.post("/knowledge/products/no-such/collect").status_code == 404
    assert "/collect" not in env.client.get("/knowledge/products/no-folder").text


def test_detail_page_has_one_collect_button_in_the_header(env):
    page = env.client.get("/knowledge/products/vxvue").text
    assert page.count('action="/knowledge/products/vxvue/collect"') == 1
    assert "/knowledge/sync/product-knowledge" not in page                  # 구역마다 따로 두지 않는다
    assert page.index("knowledge-sync-form") < page.index('id="kind-specification"')


def test_hidden_kind_has_no_section_and_no_verdict(env):
    page = env.client.get("/knowledge/products/vxvue").text
    assert 'id="kind-instruction_prompt"' not in page
    assert 'id="kind-instruction_prompt"' in env.client.get("/knowledge/products/bellalun-viewer").text
    health = knowledge_router.knowledge_status.product_health(product_config.load_product_config("vxvue"), env.storage, env.root)
    assert "instruction_prompt" not in [asset.kind for asset in health.assets]


def test_vxvue_manual_section_offers_new_document_and_replace(env):
    page = env.client.get("/knowledge/products/vxvue").text
    manual = page[page.index('id="kind-manual"'):page.index('id="kind-qa_rules"')]
    assert "새 문서 등록" in manual and 'accept=".pdf,.docx"' in manual


# -- 담당자 PC 쪽 (REQ-SYNC-002 6번) ------------------------------------------------------------


def _done_today(tmp_path, now):
    """그날 업로드를 이미 마친 상태를 만든다."""
    result = knowledge_retry.run_due("VXvue", "http://a", state_dir=tmp_path, now=now,
                                     push=lambda *a, **k: {"status": "SUCCESS", "detail": "daily"}, check_request=lambda: "")
    assert result["status"] == "SUCCESS"


def test_pc_pushes_once_for_a_new_request_even_after_daily_success(tmp_path):
    now = datetime(2026, 10, 19, 1, tzinfo=timezone.utc)   # 10:00 KST
    _done_today(tmp_path, now)
    calls = []
    push = lambda *a, **k: calls.append(a) or {"status": "SUCCESS", "detail": "requested"}
    request = lambda: "2026-10-19T01:30:00+00:00"
    later = now + timedelta(minutes=31)
    assert knowledge_retry.run_due("VXvue", "http://a", state_dir=tmp_path, now=later, push=push, check_request=request)["status"] == "SUCCESS"
    assert knowledge_retry.run_due("VXvue", "http://a", state_dir=tmp_path, now=later + timedelta(minutes=1),
                                   push=push, check_request=request)["status"] == "NOT_DUE"
    assert len(calls) == 1


def test_pc_runs_a_request_before_eight_am_on_first_install(tmp_path):
    early = datetime(2026, 10, 19, 6, tzinfo=timezone(timedelta(hours=9)))
    result = knowledge_retry.run_due("VXvue", "http://a", state_dir=tmp_path, now=early,
                                     push=lambda *a, **k: {"status": "SUCCESS", "detail": "requested"},
                                     check_request=lambda: "2026-10-18T20:59:00+00:00")
    assert result["status"] == "SUCCESS"


def test_retry_script_asks_the_server_for_requests(tmp_path, monkeypatch):
    import importlib
    import sys

    script = importlib.import_module("scripts.retry_product_knowledge")
    push_module = importlib.import_module("app.core.knowledge_push")
    asked = []
    monkeypatch.setattr(push_module, "fetch_collect_request", lambda product, url: asked.append((product, url)) or "")
    seen = {}

    def run(*args, **kwargs):
        seen["request"] = kwargs["check_request"]()
        return {"status": "NOT_DUE", "detail": "none"}

    monkeypatch.setattr(knowledge_retry, "run_due", run)
    monkeypatch.setattr(script, "get_settings", lambda: SimpleNamespace(root=tmp_path))
    monkeypatch.setattr(sys, "argv", ["retry_product_knowledge.py", "--upload-to", "http://test.invalid"])
    assert script.main() == 0
    assert asked == [("VXvue", "http://test.invalid")] and seen["request"] == ""


def test_pc_ignores_request_lookup_failure(tmp_path):
    now = datetime(2026, 10, 19, 1, tzinfo=timezone.utc)
    _done_today(tmp_path, now)

    def broken():
        raise ConnectionError("server down")

    result = knowledge_retry.run_due("VXvue", "http://a", state_dir=tmp_path, now=now + timedelta(minutes=5),
                                     push=lambda *a, **k: pytest.fail("요청이 없으면 올리지 않는다"), check_request=broken)
    assert result["status"] == "NOT_DUE"
