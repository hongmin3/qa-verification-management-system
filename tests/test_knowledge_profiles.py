"""공용 Knowledge 재설계: 출처 프로필 · 상태 판정 · 안전한 교체 · 제품 상세 화면.

Validates: REQ-KNOW-001, REQ-KNOW-002, REQ-KNOW-003, REQ-KNOW-005, REQ-KNOW-018, REQ-KNOW-019, REQ-KNOW-020

설정 파일만 더한 가짜 제품(FakeProduct)으로 "새 제품은 설정만으로 붙는다"를 확인한다. DB·파싱 저장본·
수집 기록·업로드 폴더는 모두 tmp 에 둔다. 실제 data/ 를 건드리지 않는다.
"""
from __future__ import annotations

import io
import json
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from docx import Document
from fastapi.testclient import TestClient

import app.core.product_config as product_config
import app.core.product_knowledge as product_knowledge
import app.modules.knowledge.router as knowledge_router
from app.core import document_cache, knowledge_status
from app.core.knowledge_documents import load_for_product
from app.core.knowledge_registry import RegistrationError, check_request, register_uploaded
from app.core.storage import Storage
from app.main import app

ROOT = Path(__file__).resolve().parents[1]
FAKE_YAML = """
product: Fake Product
version: "2.0"
specification:
  source: manual
testcase:
  source: knowledge_folder
manual:
  source: manual
  required: false
qa_rules:
  required: true
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    products = tmp_path / "config" / "products"
    products.mkdir(parents=True)
    for name in ("vxvue.yaml", "bellalun-viewer.yaml"):
        shutil.copy(ROOT / "config" / "products" / name, products / name)
    (products / "fake-product.yaml").write_text(FAKE_YAML, encoding="utf-8")
    monkeypatch.setattr(product_config, "_products_dir", lambda root=None: products)
    monkeypatch.setattr(product_knowledge, "get_settings", lambda: SimpleNamespace(root=tmp_path))
    monkeypatch.setattr(document_cache, "_cache_path", lambda document_id: tmp_path / "indexes" / f"{document_id}.json")
    monkeypatch.setattr(document_cache, "_text_cache_path", lambda document_id: tmp_path / "indexes" / f"{document_id}.text")
    uploads = {"storage.specification_dir": tmp_path / "specs", "storage.testcase_dir": tmp_path / "testcases"}
    for directory in uploads.values():
        directory.mkdir()
    monkeypatch.setattr(knowledge_router, "get_settings", lambda: SimpleNamespace(path=lambda key: uploads[key], root=tmp_path))
    storage = Storage(tmp_path / "app.db")
    monkeypatch.setattr(knowledge_router, "storage", storage)
    return SimpleNamespace(root=tmp_path, storage=storage, uploads=uploads, client=TestClient(app))


def _docx(text: str) -> bytes:
    document = Document()
    for line in text.split("\n"):
        document.add_paragraph(line)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _upload(env, slug: str, kind: str, name: str, data: bytes, replace_id: int | None = None):
    form = {"kind": kind}
    if replace_id is not None:
        form["replace_id"] = str(replace_id)
    return env.client.post(f"/knowledge/products/{slug}/documents", data=form, files={"file": (name, data)}, follow_redirects=False)


def _health(env, slug: str):
    return knowledge_status.product_health(product_config.load_product_config(slug), env.storage, env.root)


def _asset(health, kind: str):
    return next(asset for asset in health.assets if asset.kind == kind)


def _write_manifest(env, product: str, assets: list[dict], excluded: list[dict] | None = None):
    base = product_knowledge.product_dir(product, env.root)
    base.mkdir(parents=True, exist_ok=True)
    for asset in assets:
        path = base / "original" / asset["kind"] / asset["file_name"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("규칙", encoding="utf-8")
    (base / "manifest.json").write_text(json.dumps({"product": product, "assets": assets, "excluded": excluded or []}, ensure_ascii=False), encoding="utf-8")


# -- 출처 프로필과 등록 허용 (REQ-KNOW-018) ---------------------------------------------


def test_alm_specification_cannot_be_uploaded_and_nothing_is_saved(env):
    response = _upload(env, "vxvue", "specification", "(사양서) VXvue 사양서1(260930).docx", _docx("본문"))
    assert response.status_code == 409
    assert "ALM 자동" in response.json()["detail"]
    assert not any(env.uploads["storage.specification_dir"].iterdir())        # 파일을 저장하기 전에 거절
    assert env.storage.active_documents("specification", "VXvue") == []


def test_alm_sync_may_register_when_it_declares_its_source(env):
    response = env.client.post("/knowledge/specification", data={"product": "VXvue", "source": "alm_crawler"},
                               files={"file": ("(사양서) VXvue 사양서1(260930).docx", _docx("SRS 본문"))}, follow_redirects=False)
    assert response.status_code == 303
    [document] = env.storage.active_documents("specification", "VXvue")
    assert json.loads(document["metadata_json"])["source"] == "alm_crawler"
    assert document["version"] == product_config.load_product_config("vxvue").version   # 버전을 비우면 제품 설정 값


def test_manual_specification_upload_registers_and_redirects_to_detail(env):
    response = _upload(env, "bellalun-viewer", "specification", "(사양서) Bellalun Viewer 사양서1(260710).docx", _docx("뷰어 본문"))
    assert response.status_code == 303
    assert response.headers["location"].startswith("/knowledge/products/bellalun-viewer?notice=")
    [document] = env.storage.active_documents("specification", "Bellalun Viewer")
    metadata = json.loads(document["metadata_json"])
    assert metadata["source"] == "upload" and metadata["chunk_count"] >= 1 and len(metadata["sha256"]) == 64
    assert document_cache.load_text(document["id"])                            # 파싱 저장본이 생겼다


def test_rule_assets_and_folder_kinds_are_never_uploadable(env):
    config = product_config.load_product_config("fake-product")
    assert not product_config.can_manual_upload(config, "testcase")             # 지식 폴더 출처
    assert not product_config.can_manual_upload(config, "qa_rules")
    assert product_config.can_manual_upload(config, "manual")
    response = _upload(env, "fake-product", "testcase", "(TC) Fake.xlsx", b"PK")
    assert response.status_code == 409


# -- 안전한 교체 (REQ-KNOW-003) --------------------------------------------------------


def test_same_logical_document_keeps_only_new_file_and_other_documents_stay(env):
    old = _upload(env, "bellalun-viewer", "specification", "(사양서) Bellalun Viewer 사양서1(260710).docx", _docx("옛 판"))
    other = _upload(env, "bellalun-viewer", "specification", "(사양서) Bellalun Viewer 사양서2(260710).docx", _docx("다른 문서"))
    assert old.status_code == other.status_code == 303
    old_doc = next(d for d in env.storage.active_documents("specification", "Bellalun Viewer") if "사양서1" in d["name"])

    new = _upload(env, "bellalun-viewer", "specification", "(사양서) Bellalun Viewer 사양서1(260930).docx", _docx("새 판"))
    assert new.status_code == 303 and "교체 1건" in new.headers["location"].replace("+", " ") or "%EA%B5%90%EC%B2%B4+1" in new.headers["location"]
    names = sorted(d["name"] for d in env.storage.active_documents("specification", "Bellalun Viewer"))
    assert names == ["(사양서) Bellalun Viewer 사양서1(260930).docx", "(사양서) Bellalun Viewer 사양서2(260710).docx"]
    assert not Path(old_doc["path"]).exists()                                  # 사람이 올린 폴더 안의 옛 원본도 지운다
    assert document_cache.load_text(old_doc["id"]) is None


def test_different_manuals_each_keep_their_latest(env):
    for name in ("Fake Operation Manual.V1.0.docx", "Fake Service Manual.V1.0.docx", "Fake Operation Manual.V1.1.docx"):
        assert _upload(env, "fake-product", "manual", name, _docx(name)).status_code == 303
    names = sorted(d["name"] for d in env.storage.active_documents("manual", "Fake Product"))
    assert names == ["Fake Operation Manual.V1.1.docx", "Fake Service Manual.V1.0.docx"]


def test_unreadable_new_file_keeps_existing_document(env):
    assert _upload(env, "bellalun-viewer", "specification", "(사양서) Bellalun Viewer 사양서1(260710).docx", _docx("정상 판")).status_code == 303
    [before] = env.storage.active_documents("specification", "Bellalun Viewer")

    response = _upload(env, "bellalun-viewer", "specification", "(사양서) Bellalun Viewer 사양서1(260930).docx", b"not a docx",
                       replace_id=before["id"])
    assert response.status_code == 422
    assert "기존 문서는 그대로 분석에 쓰입니다" in response.json()["detail"]
    assert [d["id"] for d in env.storage.active_documents("specification", "Bellalun Viewer")] == [before["id"]]
    assert sorted(p.name for p in env.uploads["storage.specification_dir"].iterdir()) == [Path(before["path"]).name]


def test_older_file_without_replace_is_refused_when_newer_is_registered(env):
    assert _upload(env, "bellalun-viewer", "specification", "(사양서) Bellalun Viewer 사양서1(260930).docx", _docx("새 판")).status_code == 303
    response = _upload(env, "bellalun-viewer", "specification", "(사양서) Bellalun Viewer 사양서1(260710).docx", _docx("옛 판"))
    assert response.status_code == 409 and "더 최신 판" in response.json()["detail"]
    assert len(env.storage.active_documents("specification", "Bellalun Viewer")) == 1


def test_replace_id_of_other_product_is_rejected(env):
    assert _upload(env, "fake-product", "specification", "(사양서) Fake 사양서(260101).docx", _docx("가짜")).status_code == 303
    [fake_doc] = env.storage.active_documents("specification", "Fake Product")
    response = _upload(env, "bellalun-viewer", "specification", "(사양서) Bellalun Viewer 사양서1(260930).docx", _docx("본문"),
                       replace_id=fake_doc["id"])
    assert response.status_code == 400 and response.json()["detail"] == "교체할 문서를 찾을 수 없습니다."
    assert env.storage.get_document(fake_doc["id"]) is not None


def test_cache_write_failure_rolls_back_new_registration(env, monkeypatch):
    config = product_config.load_product_config("bellalun-viewer")
    saved = env.uploads["storage.specification_dir"] / "new.docx"
    saved.write_bytes(_docx("본문"))
    monkeypatch.setattr(document_cache, "save", lambda document_id, items: False)
    with pytest.raises(RegistrationError) as caught:
        register_uploaded(env.storage, config, "specification", saved, "(사양서) Bellalun Viewer 사양서1(260930).docx")
    assert caught.value.status == 500 and "되돌렸습니다" in caught.value.message
    assert env.storage.active_documents("specification", "Bellalun Viewer") == [] and not saved.exists()


# -- 상태 판정 (REQ-KNOW-019) ----------------------------------------------------------


def test_health_ready_when_required_assets_present(env):
    _upload(env, "fake-product", "specification", "(사양서) Fake 사양서(260101).docx", _docx("가짜"))
    _write_manifest(env, "Fake Product", [
        {"kind": "testcase", "file_name": "(TC) Fake.xlsx", "collected_at": datetime.now(timezone.utc).isoformat()},
        {"kind": "qa_rules", "file_name": "[QA 작성 규칙] Fake_Rev1.0.md", "collected_at": datetime.now(timezone.utc).isoformat()},
    ])
    tc_file = product_knowledge.product_dir("Fake Product", env.root) / "original" / "testcase" / "(TC) Fake.xlsx"
    env.storage.add_document("testcase", "Fake Product", "2.0", "", "(TC) Fake.xlsx", tc_file, {"source": "product_knowledge"})
    sync = env.storage.sync_start("Fake Product", "product_knowledge", "upload")
    env.storage.sync_finish(sync, "SUCCESS", "등록 1건")

    health = _health(env, "fake-product")
    assert (health.level, health.label) == ("READY", "정상")
    assert _asset(health, "manual").status == "없음"                           # 없어도 되는 자료
    assert _asset(health, "testcase").documents[0].source_label == "지식 폴더"


def test_missing_required_asset_is_attention(env):
    health = _health(env, "fake-product")
    assert health.level == "ATTENTION"
    assert _asset(health, "qa_rules").status == "자료 없음"
    assert _asset(health, "instruction_prompt").status == "없음"


def test_failed_sync_without_documents_is_error(env):
    sync = env.storage.sync_start("VXvue", "specification", "alm_crawler")
    env.storage.sync_finish(sync, "FAILED", "크롤러 폴더 없음")
    health = _health(env, "vxvue")
    spec = _asset(health, "specification")
    assert (spec.status, spec.level, health.level) == ("수집 실패", "ERROR", "ERROR")


def test_later_folder_success_clears_an_old_alm_failure(env):
    _upload_alm = env.client.post("/knowledge/specification", data={"product": "VXvue", "source": "alm_crawler"},
                                  files={"file": ("(사양서) VXvue 사양서1(260930).docx", _docx("SRS"))}, follow_redirects=False)
    assert _upload_alm.status_code == 303
    failed = env.storage.sync_start("VXvue", "specification", "alm_crawler")
    env.storage.sync_finish(failed, "FAILED", "옛 실패")
    _write_manifest(env, "VXvue", [{"kind": "specification", "file_name": "x.pdf", "collected_at": ""}])
    ok = env.storage.sync_start("VXvue", "product_knowledge", "upload")
    env.storage.sync_finish(ok, "SUCCESS", "등록")
    assert _asset(_health(env, "vxvue"), "specification").status == "정상"


def test_unreadable_and_duplicate_and_stale_states(env):
    folder = env.uploads["storage.specification_dir"]
    empty = folder / "a.pdf"
    empty.write_bytes(b"%PDF-")
    env.storage.add_document("specification", "Bellalun Viewer", "1.0", "", "(사양서) Bellalun Viewer 사양서1(260710).pdf", empty, {"chunk_count": 0})
    spec = _asset(_health(env, "bellalun-viewer"), "specification")
    assert (spec.status, spec.level) == ("파싱 실패", "ERROR")               # 꼭 필요한 자료를 모두 쓸 수 없음

    good = folder / "b.pdf"
    good.write_bytes(b"%PDF-")
    env.storage.add_document("specification", "Bellalun Viewer", "1.0", "", "(사양서) Bellalun Viewer 사양서1(260930).pdf", good, {"chunk_count": 3})
    assert _asset(_health(env, "bellalun-viewer"), "specification").status == "파싱 실패"     # 일부만 쓸 수 없음 → 주의
    with env.storage.connect() as db:
        db.execute("UPDATE documents SET metadata_json=? WHERE path=?", (json.dumps({"chunk_count": 2}), str(empty)))
    assert _asset(_health(env, "bellalun-viewer"), "specification").status == "중복 확인 요청"

    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    sync = env.storage.sync_start("VXvue", "specification", "alm_crawler")
    env.storage.sync_finish(sync, "SUCCESS", "")
    with env.storage.connect() as db:
        db.execute("UPDATE sync_log SET synced_at=? WHERE id=?", (old, sync))
    alm_file = folder / "c.pdf"
    alm_file.write_bytes(b"%PDF-")
    env.storage.add_document("specification", "VXvue", "1.0", "", "(사양서) VXvue 사양서1(260801).pdf", alm_file, {"chunk_count": 3, "source": "alm_crawler"})
    assert _asset(_health(env, "vxvue"), "specification").status == "업데이트 필요"


def test_unknown_source_is_attention(env, monkeypatch):
    path = product_config._products_dir() / "fake-product.yaml"
    path.write_text(FAKE_YAML.replace("specification:\n  source: manual", "specification:\n  source: ftp"), encoding="utf-8")
    config = product_config.load_product_config("fake-product")
    assert config.source_of("specification") == "unknown" and not product_config.can_manual_upload(config, "specification")
    spec_file = env.uploads["storage.specification_dir"] / "s.pdf"
    spec_file.write_bytes(b"%PDF-")
    env.storage.add_document("specification", "Fake Product", "2.0", "", "(사양서) Fake(260101).pdf", spec_file, {"chunk_count": 1})
    assert _asset(_health(env, "fake-product"), "specification").status == "출처 확인 요청"


def test_products_do_not_mix(env):
    _upload(env, "fake-product", "manual", "Fake Operation Manual.V1.0.docx", _docx("가짜"))
    assert _asset(_health(env, "bellalun-viewer"), "manual").count == 0
    assert _asset(_health(env, "fake-product"), "manual").count == 1
    assert load_for_product("Bellalun Viewer", storage=env.storage, require_both=False).chunks == []


def test_registered_document_is_what_every_analysis_reads(env):
    """QA Agent · Regression 분석이 같은 함수(load_for_product)로 같은 등록을 읽는다 (REQ-KNOW-006)."""
    _upload(env, "bellalun-viewer", "specification", "(사양서) Bellalun Viewer 사양서1(260930).docx", _docx("검색 대상 문장"))
    loaded = load_for_product("Bellalun Viewer", storage=env.storage, require_both=False)
    assert any("검색 대상 문장" in chunk.text for chunk in loaded.chunks)


# -- 화면 (REQ-KNOW-001, REQ-KNOW-020) --------------------------------------------------


def test_dashboard_draws_card_per_config_including_fake_product(env):
    env.storage.ensure_product("옛 제품")
    stray = env.uploads["storage.specification_dir"] / "old.pdf"
    stray.write_bytes(b"%PDF-")
    env.storage.add_document("specification", "옛 제품", "", "", "old.pdf", stray)
    text = env.client.get("/knowledge").text
    assert text.count('class="kn-card"') == 3
    assert "Fake Product" in text and 'href="/knowledge/products/fake-product"' in text
    assert "설정 파일이 없는 제품" in text and "옛 제품 · 문서 1개" in text
    for raw in ("configured", "source_dir", "available", "pending", "sync_log"):
        assert raw not in text                                                 # 기술 값을 그대로 보이지 않는다


def test_detail_page_actions_follow_source(env):
    _upload(env, "fake-product", "specification", "(사양서) Fake 사양서(260101).docx", _docx("가짜"))
    text = env.client.get("/knowledge/products/fake-product").text
    spec = text[text.index('id="kind-specification"'):text.index('id="kind-testcase"')]
    assert "교체" in spec and "삭제" in spec and "새 문서 등록" in spec
    testcase = text[text.index('id="kind-testcase"'):text.index('id="kind-manual"')]
    assert "담당자 PC 의 지식 폴더에서 수집합니다" in testcase and 'type="file"' not in testcase
    assert env.client.get("/knowledge/products/Fake Product").status_code == 200   # 표시 이름으로도 찾는다
    assert env.client.get("/knowledge/products/no-such").status_code == 404


def test_detail_page_lists_excluded_files_from_manifest(env):
    _write_manifest(env, "Fake Product", [], excluded=[{"kind": "manual", "file_name": "Fake Manual.V1.0.pdf",
                                                         "exclude_reason": "같은 문서의 더 최신 리비전", "superseded_by": "Fake Manual.V1.1.pdf"}])
    text = env.client.get("/knowledge/products/fake-product").text
    assert "수집 제외 1건" in text and "Fake Manual.V1.1.pdf" in text


def test_common_knowledge_code_has_no_product_name_branches():
    files = [*sorted((ROOT / "app" / "core").glob("knowledge_*.py")), ROOT / "app" / "core" / "product_knowledge.py",
             *sorted((ROOT / "app" / "modules" / "knowledge").rglob("*.py")), *sorted((ROOT / "app" / "modules" / "knowledge").rglob("*.html"))]
    offenders = []
    for path in files:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            # 비교·대입으로 제품 이름을 쓰는 곳만 본다. 설명 문장과 예시는 분기가 아니다.
            if re.search(r"""(==|!=|\bin\b|=)\s*["'](VXvue|vxvue|Bellalun Viewer|bellalun-viewer)["']|["'](VXvue|vxvue|Bellalun Viewer|bellalun-viewer)["']\s*(==|!=)""", code):
                offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    assert not offenders, "\n".join(offenders)


def test_check_request_happens_before_saving(env):
    config = product_config.load_product_config("vxvue")
    with pytest.raises(RegistrationError) as caught:
        check_request(env.storage, config, "specification")
    assert caught.value.status == 409
