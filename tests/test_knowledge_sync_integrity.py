"""지식 수집·업로드가 문서를 조용히 잃거나 낡게 두지 않는지 (docs/SPEC_CODE_MISMATCH.md 5절 2·3·9·10·12번).

모두 격리된 `root`/`storage` 로 돌린다. 기본값을 쓰면 개발용 `data/app.db` 에 문서가 등록된다
(tests/test_knowledge_upload.py 의 `server` fixture 설명 참고).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core import document_cache
from app.core.knowledge_upload import commit, store_asset
from app.core.product_config import KnowledgeSourceConfig, ProductConfig
from app.core.product_knowledge import collected_assets, product_dir, sync_product

PRODUCT = "VXvue"


@pytest.fixture
def server(tmp_path, monkeypatch):
    from app.core.storage import Storage

    index_dir = tmp_path / "indexes"
    index_dir.mkdir()
    # 파싱 저장본 경로가 실제 설정(storage.index_dir)을 가리키지 않게 한다.
    monkeypatch.setattr(document_cache, "_cache_path", lambda document_id: index_dir / f"{document_id}.json")
    monkeypatch.setattr(document_cache, "_text_cache_path", lambda document_id: index_dir / f"{document_id}.text")
    return SimpleNamespace(root=tmp_path, storage=Storage(db_path=tmp_path / "app.db"), index_dir=index_dir)


def _pdf(text: str) -> bytes:
    import fitz

    document = fitz.open()
    document.new_page().insert_text((72, 72), text)
    body = document.tobytes()
    document.close()
    return body


def _entry(record: dict, **extra) -> dict:
    return {"file_name": record["file_name"], "kind": record["kind"], "sha256": record["sha256"], **extra}


def _push(server, records: list[dict], entries: list[dict] | None = None) -> dict:
    return commit(
        PRODUCT,
        {"assets": entries if entries is not None else [_entry(record) for record in records]},
        uploaded={record["file_name"]: record for record in records},
        storage=server.storage,
        root=server.root,
    )


# --- 5-2: 리비전 표시 없는 이름의 내용이 바뀌면 파싱 저장본을 버린다 -------------------


def test_changed_content_under_the_same_name_drops_the_parsed_copy(server) -> None:
    # Validates: REQ-KNOW-007, REQ-SYNC-002
    name = "(사양서) VXvue 부록.txt"
    first = store_asset(PRODUCT, "specification", name, "옛 내용".encode("utf-8"), root=server.root)
    _push(server, [first])
    [document] = server.storage.active_documents("specification", PRODUCT)
    cache_file = server.index_dir / f"{document['id']}.json"
    cache_file.write_text("[]", encoding="utf-8")
    text_cache = server.index_dir / f"{document['id']}.text"
    text_cache.write_text("옛 내용", encoding="utf-8")

    second = store_asset(PRODUCT, "specification", name, "새 내용".encode("utf-8"), root=server.root)
    _push(server, [second])

    [after] = server.storage.active_documents("specification", PRODUCT)
    assert after["id"] == document["id"]
    assert not cache_file.exists(), "옛 TC·사양 목록(파싱 저장본)이 남았습니다"
    assert not text_cache.exists(), "옛 텍스트 저장본이 남았습니다"
    assert json.loads(after["metadata_json"])["sha256"] == second["sha256"]


def test_unchanged_content_keeps_the_parsed_copy(server) -> None:
    # Validates: REQ-KNOW-007
    record = store_asset(PRODUCT, "specification", "(사양서) VXvue 부록.txt", "내용".encode("utf-8"), root=server.root)
    _push(server, [record])
    [document] = server.storage.active_documents("specification", PRODUCT)
    cache_file = server.index_dir / f"{document['id']}.json"
    cache_file.write_text("[]", encoding="utf-8")

    _push(server, [], entries=[_entry(record)])

    assert cache_file.exists()


# --- 5-9: 지식 폴더에서 통째로 빠진 문서의 등록도 지운다 ------------------------------


def test_registration_of_a_file_pruned_by_commit_is_removed(server) -> None:
    # Validates: REQ-KNOW-012, REQ-SYNC-002
    keep = store_asset(PRODUCT, "specification", "(사양서) VXvue 사양서1(260928).txt", "하나".encode("utf-8"), root=server.root)
    gone = store_asset(PRODUCT, "specification", "(사양서) VXvue 사양서2(260928).txt", "둘".encode("utf-8"), root=server.root)
    _push(server, [keep, gone])
    assert len(server.storage.active_documents("specification", PRODUCT)) == 2

    result = _push(server, [], entries=[_entry(keep)])

    names = [document["name"] for document in server.storage.active_documents("specification", PRODUCT)]
    assert names == [keep["file_name"]]
    assert "specification/" + gone["file_name"] in result["removed"]


# --- 5-3: PC 가 읽지 못한 새 판도 옛 판을 밀어내지 않는다 ------------------------------


def test_new_revision_missing_on_the_server_keeps_the_previous_one(server) -> None:
    # Validates: REQ-SYNC-002
    good = store_asset(PRODUCT, "specification", "(사양서) VXvue 사양서1(260928).pdf", _pdf("good 0928"), root=server.root)
    _push(server, [good], entries=[_entry(good, base_name="VXvue 사양서1", revision="260928", revision_kind="date")])

    # 새 판을 PC 가 올리지 못했다(업로드 실패·수집본 없음). manifest 에는 새 판만 있다.
    result = _push(server, [], entries=[{"file_name": "(사양서) VXvue 사양서1(260929).pdf", "kind": "specification", "sha256": "x",
                                         "base_name": "VXvue 사양서1", "revision": "260929", "revision_kind": "date"}])

    usable = [Path(document["path"]).name for document in server.storage.active_documents("specification", PRODUCT) if Path(document["path"]).is_file()]
    assert usable == ["(사양서) VXvue 사양서1(260928).pdf"]
    assert result["status"] == "PARTIAL"
    assert result["kept_previous"] and "260929" in result["kept_previous"][0]


def _config(source_dir: Path) -> ProductConfig:
    return ProductConfig(product="Acme Viewer", version="1.0", knowledge_source=KnowledgeSourceConfig(dir=str(source_dir)))


def test_pc_keeps_reporting_an_unreadable_file_on_the_next_day(tmp_path) -> None:
    # Validates: REQ-SYNC-002
    source = tmp_path / "지식"
    source.mkdir()
    (source / "(사양서) Acme 사양서1(260907).md").write_text("정상", encoding="utf-8")
    (source / "(사양서) Acme 사양서2(260907).pdf").write_bytes("PDF 가 아닌 내용".encode("utf-8"))
    root = tmp_path / "project"

    first = sync_product(_config(source), root=root)
    second = sync_product(_config(source), root=root)

    assert first.failed == second.failed == ["(사양서) Acme 사양서2(260907).pdf"]
    # 읽은 파일이 그대로여도 PARTIAL 이다. FAILED 면 업로드가 멈춰 서버가 새 판을 모른다.
    assert second.unchanged == ["(사양서) Acme 사양서1(260907).md"]
    assert second.status == "PARTIAL"
    names = [asset["file_name"] for asset in collected_assets("Acme Viewer", root=root)]
    assert names == ["(사양서) Acme 사양서1(260907).md"], "이튿날 읽지 못한 판이 읽힌 판으로 둔갑했습니다"


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def _fake_push(monkeypatch, tmp_path, assets, collected_status, commit_payload):
    from app.core import knowledge_push

    sent = {"posted": [], "manifest": None}

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def get(self, url, params=None):
            return _FakeResponse({"assets": []})

        def post(self, url, data=None, files=None):
            if url.endswith("commit"):
                sent["manifest"] = json.loads(data["manifest"])
                return _FakeResponse(commit_payload)
            sent["posted"].append(files["file"][0])
            return _FakeResponse({"file_name": files["file"][0]})

    for asset in assets:
        (tmp_path / asset["file_name"]).write_bytes(b"x")
    monkeypatch.setattr(knowledge_push, "resolve_config", lambda product: SimpleNamespace(product=PRODUCT, knowledge_source=SimpleNamespace(dir="x")))
    monkeypatch.setattr(knowledge_push, "source_available", lambda config: True)
    monkeypatch.setattr(knowledge_push, "sync_product", lambda config, dry_run=False: SimpleNamespace(status=collected_status, detail="수집"))
    monkeypatch.setattr(knowledge_push, "load_manifest", lambda product: {"assets": assets})
    monkeypatch.setattr(knowledge_push, "collected_path", lambda product, asset: tmp_path / asset["file_name"])
    monkeypatch.setattr(knowledge_push, "_client", lambda timeout: FakeClient())
    return knowledge_push.push_product(PRODUCT, "http://server"), sent


def test_push_sends_unreadable_assets_and_reports_pc_partial(tmp_path, monkeypatch) -> None:
    # Validates: REQ-SYNC-002
    assets = [
        {"kind": "specification", "file_name": "ok.pdf", "sha256": "a"},
        {"kind": "specification", "file_name": "broken.pdf", "sha256": "b", "error": "추출 실패"},
    ]
    result, sent = _fake_push(monkeypatch, tmp_path, assets, "PARTIAL", {"status": "SUCCESS", "detail": "ok"})

    assert sorted(sent["posted"]) == ["broken.pdf", "ok.pdf"], "읽지 못한 파일도 서버에 보내야 서버가 옛 판을 지킵니다"
    assert {asset["file_name"] for asset in sent["manifest"]["assets"]} == {"ok.pdf", "broken.pdf"}
    assert all("error" not in asset for asset in sent["manifest"]["assets"])
    assert result["status"] == "PARTIAL", "PC 수집이 PARTIAL 이면 최종 결과도 PARTIAL 이어야 합니다"


# --- 5-12: 업로드 결과에 서버의 이전 판 유지·읽지 못함 목록을 싣는다 ----------------------


def test_push_result_carries_kept_previous_and_server_failures(tmp_path, monkeypatch) -> None:
    # Validates: REQ-SYNC-002
    assets = [{"kind": "specification", "file_name": "new.pdf", "sha256": "a"}]
    commit_payload = {"status": "PARTIAL", "detail": "d", "kept_previous": ["specification/old.pdf (새 판 new.pdf 을 읽지 못함)"],
                      "failures": ["정규화 실패 ValueError: x"]}
    result, _ = _fake_push(monkeypatch, tmp_path, assets, "SUCCESS", commit_payload)

    assert result["status"] == "PARTIAL"
    assert result["kept_previous"] == commit_payload["kept_previous"]
    assert result["unreadable"] == commit_payload["failures"]


# --- 5-10: 재시작에 끊긴 동기화 기록(RUNNING)을 시작할 때 닫는다 --------------------------


def test_startup_closes_sync_rows_left_running(tmp_path, monkeypatch) -> None:
    # Validates: REQ-KNOW-016
    import app.main as main
    from app.core.storage import Storage

    storage = Storage(db_path=tmp_path / "app.db")
    stuck = storage.sync_start(PRODUCT, "product_knowledge", "upload")
    assert storage.is_sync_running(PRODUCT, "product_knowledge")
    monkeypatch.setattr(main, "storage", storage)
    for name in ("resume_queued_impact_jobs", "resume_queued_manual_jobs", "resume_queued_qa_agent_jobs", "stop_scheduler"):
        monkeypatch.setattr(main, name, lambda: None)
    monkeypatch.setattr(main, "start_scheduler", lambda registrars: None)

    async def run_lifespan():
        async with main.lifespan(main.app):
            pass

    asyncio.run(run_lifespan())

    assert not storage.is_sync_running(PRODUCT, "product_knowledge")
    with storage.connect() as db:
        status, detail = db.execute("SELECT status, detail FROM sync_log WHERE id=?", (stuck,)).fetchone()
    assert status == "FAILED" and "재시작" in detail
