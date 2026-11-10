"""화면 등록·교체 뒤 AI 사본 반영 요청과 반영 상태 표시.

Validates: REQ-KNOW-024, REQ-KNOW-021, REQ-SYNC-002

공용 AI 연결(VXvue·Bellalun·XIPL)은 담당자 PC 가 올린 지식 사본(`data/product_knowledge/<제품>/`)만 읽는다.
화면에서 문서를 바꿔도 PC 가 다시 올리기 전까지 AI 는 이전 판을 본다(2026-11-04 체크리스트 교체 사례).
설정·DB·업로드 폴더는 모두 tmp 에 둔다. 운영 서버를 부르지 않는다.
"""
from __future__ import annotations

import hashlib
import io
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from docx import Document
from fastapi.testclient import TestClient

import app.core.product_config as product_config
import app.core.product_knowledge as product_knowledge
import app.modules.knowledge.router as knowledge_router
from app.core import document_cache, knowledge_collect_request, knowledge_retry
from app.core.storage import Storage
from app.main import app

ROOT = Path(__file__).resolve().parents[1]
NO_FOLDER_YAML = """
product: No Folder
version: "1.0"
manual:
  source: manual
qa_rules:
  required: false
"""
GUIDE = "(매뉴얼) Test Guide V1.0_KO.docx"


@pytest.fixture
def env(tmp_path, monkeypatch):
    products = tmp_path / "config" / "products"
    products.mkdir(parents=True)
    shutil.copy(ROOT / "config" / "products" / "vxvue.yaml", products / "vxvue.yaml")
    (products / "no-folder.yaml").write_text(NO_FOLDER_YAML, encoding="utf-8")
    monkeypatch.setattr(product_config, "_products_dir", lambda root=None: products)
    monkeypatch.setattr(product_knowledge, "get_settings", lambda: SimpleNamespace(root=tmp_path))
    monkeypatch.setattr(document_cache, "_cache_path", lambda document_id: tmp_path / "indexes" / f"{document_id}.json")
    monkeypatch.setattr(document_cache, "_text_cache_path", lambda document_id: tmp_path / "indexes" / f"{document_id}.text")
    uploads = {"storage.specification_dir": tmp_path / "specs", "storage.testcase_dir": tmp_path / "testcases"}
    for directory in uploads.values():
        directory.mkdir()
    monkeypatch.setattr(knowledge_router, "get_settings", lambda: SimpleNamespace(path=lambda key: uploads[key], root=tmp_path))
    # 운영 서버처럼 지식 폴더를 볼 수 없다.
    monkeypatch.setattr(knowledge_router, "source_available", lambda config: False)
    storage = Storage(tmp_path / "app.db")
    monkeypatch.setattr(knowledge_router, "storage", storage)
    return SimpleNamespace(root=tmp_path, storage=storage, client=TestClient(app))


def _docx(text: str) -> bytes:
    document = Document()
    document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _upload(env, slug, name, data, replace_id=None, as_json=True):
    form = {"kind": "manual"}
    if replace_id is not None:
        form["replace_id"] = str(replace_id)
    headers = {"Accept": "application/json"} if as_json else {}
    return env.client.post(f"/knowledge/products/{slug}/documents", data=form, files={"file": (name, data)},
                           headers=headers, follow_redirects=False)


def _write_ai_copy(env, product, kind, name, data):
    """담당자 PC 가 올린 지식 사본(manifest + 원본). AI 연결이 읽는 곳이다."""
    base = product_knowledge.product_dir(product, env.root)
    path = base / "original" / kind / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    manifest = {"product": product, "synced_at": datetime.now(timezone.utc).isoformat(),
                "assets": [{"kind": kind, "file_name": name, "sha256": hashlib.sha256(data).hexdigest(),
                            "collected_at": datetime.now(timezone.utc).isoformat()}]}
    (base / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")


def test_registration_leaves_an_ai_refresh_request(env):
    response = _upload(env, "vxvue", GUIDE, _docx("새 안내"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "DONE"
    assert body["ai_refresh"]["status"] == "REQUESTED"
    assert "5분 주기" in body["ai_refresh"]["detail"]
    pending = env.client.get("/knowledge/product-knowledge/request", params={"product": "VXvue"}).json()
    assert pending["requested_at"] == body["ai_refresh"]["requested_at"]


def test_replacement_leaves_a_request_even_after_todays_upload(env):
    first = _upload(env, "vxvue", GUIDE, _docx("이전 안내")).json()
    sync = env.storage.sync_start("VXvue", "product_knowledge", "upload")
    env.storage.sync_finish(sync, "SUCCESS", "오늘 업로드 완료")
    assert knowledge_collect_request.pending_request("VXvue", env.storage, env.root) == ""
    replaced = _upload(env, "vxvue", "guide_new.docx", _docx("새 안내"), replace_id=first["document_id"]).json()
    assert replaced["ai_refresh"]["status"] == "REQUESTED"
    assert knowledge_collect_request.pending_request("VXvue", env.storage, env.root) == replaced["ai_refresh"]["requested_at"]


def test_form_registration_notice_mentions_the_ai_request(env):
    response = _upload(env, "vxvue", GUIDE, _docx("안내"), as_json=False)
    assert response.status_code == 303
    page = env.client.get(response.headers["location"]).text
    assert "AI 연결 반영을 요청했습니다" in page


def test_product_without_uploader_says_ai_is_not_refreshed(env):
    body = _upload(env, "no-folder", GUIDE, _docx("안내")).json()
    assert body["ai_refresh"]["status"] == "NO_UPLOADER"
    assert "AI 연결에 반영" in body["ai_refresh"]["detail"]
    assert not (product_knowledge.product_dir("No Folder", env.root) / knowledge_collect_request.REQUEST_FILE).exists()


def test_detail_page_shows_ai_copy_state_per_document(env):
    old = _docx("이전 안내")
    _write_ai_copy(env, "VXvue", "manual", GUIDE, old)
    body = _upload(env, "vxvue", GUIDE, _docx("새 안내")).json()
    page = env.client.get("/knowledge/products/vxvue").text
    assert "<th>AI 반영</th>" in page
    assert "반영 대기" in page
    assert "AI 연결에 아직 반영되지 않은 문서 1개" in page
    # PC 가 새 파일을 올려 지식 사본의 해시가 같아지면 반영됨으로 바뀐다.
    saved = env.storage.get_document(body["document_id"])
    _write_ai_copy(env, "VXvue", "manual", GUIDE, Path(saved["path"]).read_bytes())
    page = env.client.get("/knowledge/products/vxvue").text
    assert "반영 대기" not in page
    assert "반영됨" in page


def test_product_without_ai_copy_has_no_ai_column(env):
    _upload(env, "no-folder", GUIDE, _docx("안내"))
    page = env.client.get("/knowledge/products/no-folder").text
    assert "<th>AI 반영</th>" not in page


def test_request_only_runner_waits_for_a_screen_request(tmp_path):
    calls = []
    push = lambda product, url: calls.append(product) or {"status": "SUCCESS", "detail": "ok"}
    now = datetime(2026, 11, 4, 5, 0, tzinfo=timezone.utc)  # 14:00 KST, 매일 업로드 시각 뒤
    idle = knowledge_retry.run_due("Bellalun Viewer", "http://server", state_dir=tmp_path, now=now, push=push,
                                   check_request=lambda: "", daily=False)
    assert idle["status"] == "NOT_DUE" and calls == []
    request = "2026-11-04T04:59:00+00:00"
    done = knowledge_retry.run_due("Bellalun Viewer", "http://server", state_dir=tmp_path, now=now, push=push,
                                   check_request=lambda: request, daily=False)
    assert done["status"] == "SUCCESS" and calls == ["Bellalun Viewer"]
    again = knowledge_retry.run_due("Bellalun Viewer", "http://server", state_dir=tmp_path, now=now, push=push,
                                    check_request=lambda: request, daily=False)
    assert again["status"] == "NOT_DUE" and calls == ["Bellalun Viewer"]


def _load_bellalun_script():
    import importlib.util

    spec = importlib.util.spec_from_file_location("sync_bellalun_shared_for_test", ROOT / "scripts" / "sync_bellalun_shared.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bellalun_request_only_mode_uploads_only_for_a_new_request(tmp_path, monkeypatch, capsys):
    import sys

    import app.core.config as config_module
    import app.core.knowledge_push as knowledge_push

    script = _load_bellalun_script()
    skill, knowledge = tmp_path / "skill", tmp_path / "knowledge"
    skill.mkdir(), knowledge.mkdir()
    pushes, exports = [], []
    monkeypatch.setattr(script, "export_common_skill", lambda skill_root, knowledge_root: exports.append(1) or {"status": "SKIPPED"})
    monkeypatch.setattr(knowledge_push, "push_product", lambda product, url: pushes.append(product) or {"status": "SUCCESS", "detail": "ok"})
    request = {"value": ""}
    monkeypatch.setattr(knowledge_push, "fetch_collect_request", lambda product, url: request["value"])
    monkeypatch.setattr(config_module, "get_settings", lambda: SimpleNamespace(root=tmp_path))
    argv = ["sync_bellalun_shared.py", "--skill-root", str(skill), "--knowledge-root", str(knowledge),
            "--upload-to", "http://server", "--only-when-requested"]
    monkeypatch.setattr(sys, "argv", argv)
    assert script.main() == 0
    assert pushes == [] and exports == []
    request["value"] = "2026-11-04T05:00:00+00:00"
    assert script.main() == 0
    assert pushes == ["Bellalun Viewer"] and exports == [1]
    assert script.main() == 0
    assert pushes == ["Bellalun Viewer"]
    assert (tmp_path / "data" / "knowledge_retry").is_dir()


def test_bellalun_installer_registers_a_minute_request_task():
    text = (ROOT / "scripts" / "install_bellalun_shared_schedule.ps1").read_text(encoding="utf-8-sig")
    assert "$requestName = 'Bellalun_SharedKnowledge_Request'" in text
    assert "$requestArguments = $arguments + ' --only-when-requested'" in text
    assert "-RepetitionInterval (New-TimeSpan -Minutes 5)" in text
    assert "-MultipleInstances IgnoreNew" in text


# -- 화면에서 지운 문서는 AI 사본에서도 빠진다 (REQ-KNOW-022, REQ-KNOW-024) ------------------------

def _commit_two(env, keep_name, deleted_name):
    from app.core.knowledge_upload import commit, store_asset

    records = {name: store_asset("VXvue", "manual", name, f"{name} 본문".encode("utf-8"), root=env.root)
               for name in (keep_name, deleted_name)}
    manifest = {"assets": [{"kind": "manual", "file_name": name, "sha256": record["sha256"]} for name, record in records.items()]}
    return commit("VXvue", manifest, uploaded=records, storage=env.storage, root=env.root), records


def test_commit_leaves_screen_deleted_documents_out_of_the_ai_copy(env):
    from app.core.knowledge_upload import server_state

    keep, gone = "(매뉴얼) Keep Guide.txt", "(매뉴얼) Gone Guide.txt"
    env.storage.remember_deleted("VXvue", "manual", gone.upper())   # 대소문자만 다른 이름도 같은 기록
    result, records = _commit_two(env, keep, gone)
    manifest = product_knowledge.load_manifest("VXvue", env.root)
    assert [asset["file_name"] for asset in manifest["assets"]] == [keep]
    excluded = [item for item in manifest["excluded"] if item.get("screen_deleted")]
    assert [item["file_name"] for item in excluded] == [gone]
    assert "화면에서 지운 문서" in excluded[0]["exclude_reason"]
    assert "화면에서 지운 문서 AI 제외 1건" in result["detail"]
    # 파일은 남겨 PC 가 매번 다시 올리지 않게 한다. 서버 상태에도 그 해시가 있다.
    assert (product_knowledge.product_dir("VXvue", env.root) / "original" / "manual" / gone).is_file()
    state = {asset["file_name"]: asset["sha256"] for asset in server_state("VXvue", env.root)["assets"]}
    assert state[gone] == records[gone]["sha256"]


def test_re_registered_name_returns_to_the_ai_copy(env):
    keep, gone = "(매뉴얼) Keep Guide.txt", "(매뉴얼) Gone Guide.txt"
    env.storage.remember_deleted("VXvue", "manual", gone)
    _commit_two(env, keep, gone)
    env.storage.forget_deleted("VXvue", "manual", gone)
    _commit_two(env, keep, gone)
    names = [asset["file_name"] for asset in product_knowledge.load_manifest("VXvue", env.root)["assets"]]
    assert sorted(names) == sorted([keep, gone])


def test_screen_delete_leaves_an_ai_refresh_request(env):
    body = _upload(env, "vxvue", GUIDE, _docx("안내")).json()
    sync = env.storage.sync_start("VXvue", "product_knowledge", "upload")
    env.storage.sync_finish(sync, "SUCCESS", "업로드 완료")
    assert knowledge_collect_request.pending_request("VXvue", env.storage, env.root) == ""
    response = env.client.post(f"/knowledge/delete/{body['document_id']}", data={"next": "/knowledge/products/vxvue"},
                               follow_redirects=False)
    assert response.status_code == 303
    assert knowledge_collect_request.pending_request("VXvue", env.storage, env.root)


# -- 열어 둔 화면에서 교체: 동기화로 문서 번호가 바뀌어도 교체된다 (REQ-KNOW-024) --------------------

def _replace(env, replace_id, replace_name, name, data):
    form = {"kind": "manual", "replace_id": str(replace_id)}
    if replace_name is not None:
        form["replace_name"] = replace_name
    return env.client.post("/knowledge/products/vxvue/documents", data=form, files={"file": (name, data)},
                           headers={"Accept": "application/json"}, follow_redirects=False)


def _resync(env, document_id):
    """담당자 PC 가 화면 등록본을 다시 올린 것처럼, 같은 이름의 새 번호 문서로 바꾼다."""
    return _replace(env, document_id, None, "synced.docx", _docx("동기화된 판")).json()["document_id"]


def test_stale_replace_id_falls_back_to_the_current_document_of_the_same_name(env):
    first = _upload(env, "vxvue", GUIDE, _docx("처음 판")).json()
    current = _resync(env, first["document_id"])
    assert env.storage.get_document(first["document_id"]) is None
    response = _replace(env, first["document_id"], GUIDE, "새 안내.docx", _docx("새 판"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["replaced"] == [GUIDE]
    assert env.storage.get_document(current) is None
    [active] = env.storage.active_documents("manual", "VXvue")
    assert active["id"] == body["document_id"]


def test_stale_replace_id_without_a_match_asks_for_a_reload(env):
    first = _upload(env, "vxvue", GUIDE, _docx("처음 판")).json()
    _resync(env, first["document_id"])
    for name in (None, "(매뉴얼) Other Guide V1.0_KO.docx"):
        response = _replace(env, first["document_id"], name, "새 안내.docx", _docx("새 판"))
        assert response.status_code == 400
        assert "화면을 새로 고친 뒤 다시 교체하세요" in response.json()["detail"]


def test_replace_form_sends_the_document_name(env):
    _upload(env, "vxvue", GUIDE, _docx("안내"))
    page = env.client.get("/knowledge/products/vxvue").text
    assert f'name="replace_name" value="{GUIDE}"' in page
