"""사람이 화면에서만 올리는 참고 자료 구역.

Validates: REQ-KNOW-025, REQ-KNOW-018

설정·DB·업로드 폴더·AI 사본은 모두 tmp 에 둔다. 운영 서버를 부르지 않는다.
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
from app.core import document_cache, knowledge_collect_request
from app.core.knowledge_registry import person_documents
from app.core.knowledge_upload import UploadRejected, store_asset
from app.core.storage import Storage
from app.main import app

ROOT = Path(__file__).resolve().parents[1]
PLAIN_YAML = """
product: Plain
version: "1.0"
qa_rules:
  required: false
"""
FOLDER_SOURCE_YAML = """
product: Folder Ref
version: "1.0"
qa_rules:
  required: false
reference:
  source: knowledge_folder
  hidden: false
"""
SPEC_MD = "# VSS.Vieworks 스펙문서\n\n| 항목 | 설명 |\n|---|---|\n| Service Name | VSS service name |\n"


@pytest.fixture
def env(tmp_path, monkeypatch):
    products = tmp_path / "config" / "products"
    products.mkdir(parents=True)
    shutil.copy(ROOT / "config" / "products" / "vxvue.yaml", products / "vxvue.yaml")
    (products / "plain.yaml").write_text(PLAIN_YAML, encoding="utf-8")
    (products / "folder-ref.yaml").write_text(FOLDER_SOURCE_YAML, encoding="utf-8")
    monkeypatch.setattr(product_config, "_products_dir", lambda root=None: products)
    monkeypatch.setattr(product_knowledge, "get_settings", lambda: SimpleNamespace(root=tmp_path))
    monkeypatch.setattr(document_cache, "_cache_path", lambda document_id: tmp_path / "indexes" / f"{document_id}.json")
    monkeypatch.setattr(document_cache, "_text_cache_path", lambda document_id: tmp_path / "indexes" / f"{document_id}.text")
    uploads = {"storage.specification_dir": tmp_path / "specs", "storage.testcase_dir": tmp_path / "testcases"}
    for directory in uploads.values():
        directory.mkdir()
    monkeypatch.setattr(knowledge_router, "get_settings", lambda: SimpleNamespace(path=lambda key: uploads[key], root=tmp_path))
    monkeypatch.setattr(knowledge_router, "source_available", lambda config: False)
    storage = Storage(tmp_path / "data" / "app.db")
    monkeypatch.setattr(knowledge_router, "storage", storage)
    return SimpleNamespace(root=tmp_path, storage=storage, client=TestClient(app))


def _docx(text: str) -> bytes:
    document = Document()
    document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _upload(env, slug, name, data, **extra):
    form = {"kind": "reference", **extra}
    return env.client.post(f"/knowledge/products/{slug}/documents", data=form, files={"file": (name, data)},
                           headers={"Accept": "application/json"}, follow_redirects=False)


def _write_ai_copy(env, product, kind, name, data):
    base = product_knowledge.product_dir(product, env.root)
    path = base / "original" / kind / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    now = datetime.now(timezone.utc).isoformat()
    manifest = {"product": product, "synced_at": now,
                "assets": [{"kind": kind, "file_name": name, "sha256": hashlib.sha256(data).hexdigest(),
                            "base_name": Path(name).stem, "collected_at": now}]}
    (base / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")


def _section_order(page: str) -> list[str]:
    import re

    return re.findall(r'<section id="kind-([a-z_]+)"', page)


def test_section_sits_between_release_note_and_qa_rules(env):
    page = env.client.get("/knowledge/products/vxvue").text
    order = _section_order(page)
    assert "reference" in order
    assert order.index("release_note") + 1 == order.index("reference") == order.index("qa_rules") - 1
    section = page.split('<section id="kind-reference">', 1)[1].split("</section>", 1)[0]
    assert "<h2>참고 자료</h2>" in section
    assert "새 문서 등록" in section
    assert ".md" in section
    assert "reference" not in _section_order(env.client.get("/knowledge/products/plain").text)


def test_free_named_markdown_is_registered_without_collect_request(env):
    response = _upload(env, "vxvue", "VSS.Vieworks_Spec.md", SPEC_MD.encode("utf-8"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "DONE"
    assert body["ai_refresh"]["status"] == "DIRECT"
    assert "바로 읽습니다" in body["ai_refresh"]["detail"]
    assert not (product_knowledge.product_dir("VXvue", env.root) / knowledge_collect_request.REQUEST_FILE).exists()
    documents = env.storage.active_documents("reference", "VXvue")
    assert [d["name"] for d in documents] == ["VSS.Vieworks_Spec.md"]
    assert "Service Name" in document_cache.load_text(documents[0]["id"])


def test_other_kind_names_are_accepted_and_not_flagged(env):
    response = _upload(env, "vxvue", "(사양서) VSS 메모.docx", _docx("메모"))
    assert response.status_code == 200, response.text
    page = env.client.get("/knowledge/products/vxvue").text
    section = page.split('<section id="kind-reference">', 1)[1].split("</section>", 1)[0]
    assert "(사양서) VSS 메모.docx" in section
    assert "종류 확인 요청" not in section


def test_only_person_uploads_are_accepted(env):
    from app.core.knowledge_registry import RegistrationError, check_request

    vxvue = product_config.load_product_config("vxvue")
    with pytest.raises(RegistrationError) as refused:
        check_request(env.storage, vxvue, "reference", "alm_crawler", file_name="memo.md")
    assert refused.value.status == 409
    # 설정에 다른 출처를 적어도 사람이 올리는 구역이다.
    folder = product_config.load_product_config("folder-ref")
    assert folder.source_of("reference") == product_config.SOURCE_MANUAL
    assert _upload(env, "folder-ref", "memo.md", b"# memo").status_code == 200
    for declared in ("knowledge_folder", "external_sync"):
        assert product_config.registration_refusal(folder, "reference", declared)


def test_empty_text_file_is_refused(env):
    response = _upload(env, "vxvue", "blank.txt", b"   \n")
    assert response.status_code == 422
    assert env.storage.active_documents("reference", "VXvue") == []


def test_knowledge_folder_never_creates_or_removes_references(env):
    with pytest.raises(UploadRejected):
        store_asset("VXvue", "reference", "memo.md", b"# memo", root=env.root)
    data = _docx("같은 바이트")
    assert _upload(env, "vxvue", "Guide.docx", data).status_code == 200
    _write_ai_copy(env, "VXvue", "manual", "Guide.docx", data)
    product_knowledge.register_collected("VXvue", storage=env.storage, root=env.root)
    assert [d["name"] for d in env.storage.active_documents("reference", "VXvue")] == ["Guide.docx"]


def test_references_are_not_sent_down_to_the_knowledge_folder(env):
    assert _upload(env, "vxvue", "memo.md", b"# memo").status_code == 200
    assert all(d["kind"] != "reference" for d in person_documents(env.storage, "VXvue"))


def test_ai_connections_read_registered_references(env):
    from app.modules.vxvue_chatbot.desktop import ManifestDocuments
    from app.modules.vxvue_chatbot.knowledge import KINDS

    assert "reference" in KINDS
    _write_ai_copy(env, "VXvue", "manual", "(매뉴얼) Guide V1.0_KO.docx", _docx("매뉴얼"))
    before = ManifestDocuments(env.root)
    assert _upload(env, "vxvue", "VSS.Vieworks_Spec.md", SPEC_MD.encode("utf-8")).status_code == 200
    after = ManifestDocuments(env.root)
    references = after.active_documents("reference", "VXvue")
    assert [d["name"] for d in references] == ["VSS.Vieworks_Spec.md"]
    assert Path(references[0]["path"]).read_text(encoding="utf-8") == SPEC_MD
    assert after.fingerprint != before.fingerprint
    page = env.client.get("/knowledge/products/vxvue").text
    section = page.split('<section id="kind-reference">', 1)[1].split("</section>", 1)[0]
    assert "반영됨" in section and "반영 대기" not in section
