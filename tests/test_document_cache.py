from types import SimpleNamespace

from app.core import document_cache
from app.core.document_schemas import SpecificationChunk, TestCase as SchemaTestCase
from app.core.knowledge_documents import load_for_product


def _use_cache_dir(monkeypatch, tmp_path):
    settings = SimpleNamespace(path=lambda dotted: tmp_path)
    monkeypatch.setattr(document_cache, "get_settings", lambda: settings)


def test_cache_round_trip_and_text(monkeypatch, tmp_path):
    _use_cache_dir(monkeypatch, tmp_path)
    chunks = [SpecificationChunk(chunk_id="s-p1-0", document_id="s", page=1, text="로그인 사양")]

    assert document_cache.save(10, chunks) is True
    assert document_cache.save_text(10, "전체 사양 원문") is True

    assert document_cache.load(10, SpecificationChunk) == chunks
    assert document_cache.load_text(10) == "전체 사양 원문"


def test_corrupt_cache_is_treated_as_miss(monkeypatch, tmp_path):
    _use_cache_dir(monkeypatch, tmp_path)
    (tmp_path / "11.json").write_text("{broken", encoding="utf-8")

    assert document_cache.load(11, SpecificationChunk) is None


def test_delete_removes_both_cache_files(monkeypatch, tmp_path):
    _use_cache_dir(monkeypatch, tmp_path)
    document_cache.save(12, [SchemaTestCase(tc_id="TC-1")])
    document_cache.save_text(12, "text")

    document_cache.delete(12)

    assert not (tmp_path / "12.json").exists()
    assert not (tmp_path / "12.text").exists()


def test_product_analysis_uses_registered_document_cache(monkeypatch, tmp_path):
    _use_cache_dir(monkeypatch, tmp_path)
    document_cache.save(1, [SpecificationChunk(chunk_id="spec-p1-0", document_id="spec", page=1, text="사양")])
    document_cache.save_text(1, "사양 전체")
    document_cache.save(2, [SchemaTestCase(tc_id="TC-1", feature="로그인")])

    class FakeStorage:
        def active_documents(self, kind, product):
            if kind == "specification":
                return [{"id": 1, "path": str(tmp_path / "missing.pdf"), "name": "사양서.pdf", "kind": kind,
                         "product": product, "version": "1", "revision": "", "created_at": "now"}]
            return [{"id": 2, "path": str(tmp_path / "missing.xlsx"), "name": "TC.xlsx", "kind": kind,
                     "product": product, "version": "1", "revision": "", "created_at": "now", "metadata_json": "{}"}]

    loaded = load_for_product("VXvue", storage=FakeStorage(), with_text=True)

    # 원본 파일이 없어도 등록 문서의 캐시로 읽는다 (REQ-PARSE-009).
    assert [chunk.text for chunk in loaded.chunks] == ["사양"]
    assert [case.tc_id for case in loaded.cases] == ["TC-1"]
    assert loaded.baseline_texts == ["사양 전체"]
    assert loaded.failures == []
