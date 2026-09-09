"""지식 폴더 업로드 (담당자 PC → 운영 서버) 테스트.

서버가 폴더를 마운트하지 못하는 환경이라 이 경로가 유일한 반영 수단이다. 그래서 다음 세
가지를 고정한다.

  - 보내는 쪽을 신뢰하지 않는다 (파일 이름이 그대로 디스크 경로가 된다)
  - 서버가 이미 가진 파일은 다시 받지 않는다 (전송량)
  - manifest 에서 빠진 파일은 서버에서도 사라진다 (옛 리비전이 남는 것이 가장 나쁜 실패)
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.core.knowledge_upload import (
    MAX_ASSET_BYTES,
    UploadRejected,
    commit,
    safe_file_name,
    server_state,
    store_asset,
)
from app.core.product_knowledge import product_dir


PRODUCT = "VXvue"


@pytest.fixture
def server(tmp_path):
    """서버의 수집 폴더와 DB 를 tmp 로 격리한다.

    `root` 를 넘기지 않으면 `commit` 안의 `register_collected` 가 진짜 `data/app.db` 에
    문서를 등록한다 — 처음 이 테스트를 쓸 때 실제로 개발 DB 에 5건이 들어갔다. 모듈이
    `root`/`storage` 를 전부 인자로 받도록 만들어 둔 이유가 이것이다.
    """
    from app.core.storage import Storage

    return SimpleNamespace(root=tmp_path, storage=Storage(db_path=tmp_path / "app.db"))


# --- 파일 이름: 보내는 쪽을 신뢰하지 않는다 ----------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "../../../etc/passwd.pdf",
        "..\\..\\Windows\\system32\\evil.pdf",
        "C:\\Users\\other\\(사양서) x.pdf",
        "/absolute/(매뉴얼) y.pdf",
        "sub/dir/(TC) z.xlsx",
    ],
)
def test_names_containing_a_path_are_rejected(raw: str) -> None:
    """경로 조각을 떼어내 저장하지 않고 **거절한다.**

    떼어내면 `../../../evil.pdf` 가 조용히 `evil.pdf` 로 수집 폴더에 생긴다 — 보낸 적 없는
    파일인데 운영자는 왜 거기 있는지 설명할 수 없다. 정상 클라이언트는 순수 파일 이름만
    보내므로, 경로가 섞였다면 공격이거나 보내는 쪽 버그다.
    """
    with pytest.raises(UploadRejected):
        safe_file_name(raw)


def test_windows_paths_are_caught_on_a_posix_server() -> None:
    """서버는 Linux 지만 보내는 쪽은 Windows 다. PurePosixPath 만으로는 역슬래시가 안 걸린다."""
    with pytest.raises(UploadRejected):
        safe_file_name("C:\\knowledge\\(사양서) VXvue 사양서1(260907).pdf")


def test_a_plain_file_name_passes() -> None:
    assert safe_file_name("(사양서) VXvue 사양서1(260907).pdf") == "(사양서) VXvue 사양서1(260907).pdf"


def test_hidden_and_empty_names_are_rejected() -> None:
    with pytest.raises(UploadRejected):
        safe_file_name("")
    with pytest.raises(UploadRejected):
        safe_file_name("   ")
    with pytest.raises(UploadRejected):
        safe_file_name(".hidden.pdf")


def test_unsupported_extension_is_rejected() -> None:
    """텍스트를 뽑을 수 없는 형식은 받아도 검색에 기여하지 못한다."""
    with pytest.raises(UploadRejected) as exc:
        safe_file_name("(사양서) x.exe")
    assert ".pdf" in str(exc.value)


def test_korean_name_is_nfc_normalised() -> None:
    """macOS·일부 SMB 는 자모 분해(NFD)로 이름을 준다. 합쳐 두지 않으면 같은 파일이 둘로 보인다."""
    decomposed = "(사양서) VXvue 사양서1(260907).pdf"
    import unicodedata

    assert safe_file_name(unicodedata.normalize("NFD", decomposed)) == decomposed


# --- 자산 저장 ----------------------------------------------------------------


def _store(server, name="(사양서) VXvue 사양서1(260907).txt", kind="specification", body="사양 본문"):
    return store_asset(PRODUCT, kind, name, body.encode("utf-8"), root=server.root)


def test_stored_asset_lands_under_the_product_folder(server) -> None:
    record = _store(server)
    path = product_dir(PRODUCT, server.root) / "original" / "specification" / record["file_name"]
    assert path.is_file()
    assert record["sha256"]
    assert record["bytes"] > 0


def test_normalised_text_is_extracted_by_the_server(server) -> None:
    """PC 가 만든 텍스트를 함께 올리지 않는다 — 파서가 바뀌면 서버 텍스트도 바뀌어야 한다."""
    record = _store(server, body="사양 본문 내용")
    assert record["normalized_chars"] > 0
    normalized = product_dir(PRODUCT, server.root) / record["normalized_path"]
    assert "사양 본문 내용" in normalized.read_text(encoding="utf-8")


def test_corrupted_transfer_is_rejected_and_not_left_on_disk(server) -> None:
    """잘린 파일이 디스크에 남으면 다음 실행이 '이미 있음'으로 건너뛴다 — 조용히 옛 내용이 굳는다."""
    name = "(사양서) VXvue 사양서2(260907).txt"
    with pytest.raises(UploadRejected):
        store_asset(PRODUCT, "specification", name, b"content", sha256="0" * 64, root=server.root)
    assert not (product_dir(PRODUCT, server.root) / "original" / "specification" / name).exists()


def test_unknown_kind_is_rejected(server) -> None:
    with pytest.raises(UploadRejected):
        store_asset(PRODUCT, "../secrets", "(사양서) x.txt", b"x")
    with pytest.raises(UploadRejected):
        store_asset(PRODUCT, "unknown", "(사양서) x.txt", b"x")


def test_unknown_product_is_rejected(server) -> None:
    with pytest.raises(UploadRejected) as exc:
        store_asset("없는제품", "specification", "(사양서) x.txt", b"x")
    assert "config/products" in str(exc.value)


def test_empty_and_oversized_files_are_rejected(server) -> None:
    with pytest.raises(UploadRejected):
        store_asset(PRODUCT, "specification", "(사양서) x.txt", b"")
    assert MAX_ASSET_BYTES > 100 * 1024 * 1024  # 실측 최대 PDF 약 30MB — 상한이 그보다 훨씬 커야 한다


# --- 서버 상태 조회: 전송량을 줄이는 근거 -------------------------------------


def test_state_lists_what_the_server_already_has(server) -> None:
    record = _store(server)
    state = server_state(PRODUCT, server.root)
    assert {"file_name": record["file_name"], "kind": "specification", "sha256": record["sha256"]} not in state["assets"]
    # manifest 가 없으면 파일이 있어도 보고하지 않는다 — 확정되지 않은 것은 가진 것이 아니다.
    assert state["assets"] == []


def test_state_reports_assets_after_commit(server) -> None:
    record = _store(server)
    commit(PRODUCT, {"assets": [{"file_name": record["file_name"], "kind": "specification", "sha256": record["sha256"], "base_name": "VXvue 사양서1"}]}, uploaded={record["file_name"]: record}, storage=server.storage, root=server.root)
    state = server_state(PRODUCT, server.root)
    assert [asset["file_name"] for asset in state["assets"]] == [record["file_name"]]
    assert state["assets"][0]["sha256"] == record["sha256"]


def test_state_hides_assets_whose_file_vanished(server) -> None:
    """manifest 에는 있는데 파일이 없으면 다시 받아야 한다 (전송 중 끊김)."""
    record = _store(server)
    commit(PRODUCT, {"assets": [{"file_name": record["file_name"], "kind": "specification", "sha256": record["sha256"]}]}, uploaded={record["file_name"]: record}, storage=server.storage, root=server.root)
    (product_dir(PRODUCT, server.root) / "original" / "specification" / record["file_name"]).unlink()
    assert server_state(PRODUCT, server.root)["assets"] == []


# --- commit: 옛 리비전이 남지 않는다 ------------------------------------------


def test_commit_removes_assets_missing_from_the_manifest(server) -> None:
    """사양서(260831)가 서버에 남으면 옛 사양이 계속 검색된다 — 이 시스템이 가장 피해야 할 실패다."""
    old = _store(server, name="(사양서) VXvue 사양서1(260831).txt")
    new = _store(server, name="(사양서) VXvue 사양서1(260907).txt")
    result = commit(
        PRODUCT,
        {"assets": [{"file_name": new["file_name"], "kind": "specification", "sha256": new["sha256"]}]},
        uploaded={new["file_name"]: new},
        storage=server.storage,
        root=server.root,
    )
    assert any("260831" in entry for entry in result["removed"])
    assert not (product_dir(PRODUCT, server.root) / "original" / "specification" / old["file_name"]).exists()
    assert (product_dir(PRODUCT, server.root) / "original" / "specification" / new["file_name"]).is_file()


def test_commit_also_prunes_the_normalised_copy(server) -> None:
    """원본만 지우고 정규화본을 남기면 검색에는 옛 내용이 계속 걸린다."""
    old = _store(server, name="(사양서) VXvue 사양서1(260831).txt")
    stale_normalized = product_dir(PRODUCT, server.root) / old["normalized_path"]
    assert stale_normalized.is_file()
    commit(PRODUCT, {"assets": []}, uploaded={}, storage=server.storage, root=server.root)
    assert not stale_normalized.exists()


def test_commit_reports_assets_that_never_arrived(server) -> None:
    """업로드가 실패한 파일을 manifest 에 남기면 열리지 않는 경로가 등록된다."""
    result = commit(PRODUCT, {"assets": [{"file_name": "(사양서) 안올라온것.pdf", "kind": "specification", "sha256": "x"}]}, uploaded={}, storage=server.storage, root=server.root)
    assert result["status"] == "PARTIAL"
    assert "(사양서) 안올라온것.pdf" in result["missing"]


def test_commit_drops_pc_local_paths(server) -> None:
    """PC 경로가 manifest 에 실려 오면 서버에서 열 수 없는 경로가 등록된다."""
    record = _store(server)
    commit(
        PRODUCT,
        {"assets": [{"file_name": record["file_name"], "kind": "specification", "sha256": record["sha256"], "source_path": "C:\\Users\\2024980\\x.pdf"}]},
        uploaded={record["file_name"]: record},
        storage=server.storage,
        root=server.root,
    )
    manifest = json.loads((product_dir(PRODUCT, server.root) / "manifest.json").read_text(encoding="utf-8"))
    assert all("source_path" not in asset for asset in manifest["assets"])


def test_commit_rejects_traversal_in_the_manifest(server) -> None:
    """업로드 엔드포인트만 막고 manifest 를 믿으면 우회된다."""
    result = commit(PRODUCT, {"assets": [{"file_name": "../../evil.pdf", "kind": "specification", "sha256": "x"}]}, uploaded={}, storage=server.storage, root=server.root)
    assert result["assets"] == 0


def test_commit_keeps_normalised_path_for_files_not_re_uploaded(server) -> None:
    """서버가 이미 가진 파일은 이번에 안 올라온다. 그때 정규화 경로가 비면 검색에서 사라진다."""
    record = _store(server)
    manifest_asset = {"file_name": record["file_name"], "kind": "specification", "sha256": record["sha256"]}
    commit(PRODUCT, {"assets": [manifest_asset]}, uploaded={record["file_name"]: record}, storage=server.storage, root=server.root)
    commit(PRODUCT, {"assets": [manifest_asset]}, uploaded={}, storage=server.storage, root=server.root)  # 두 번째 주 — 바뀐 게 없어 업로드 없음
    manifest = json.loads((product_dir(PRODUCT, server.root) / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["assets"][0]["normalized_path"] == record["normalized_path"]
    assert manifest["assets"][0]["normalized_chars"] > 0


# --- HTTP 계층: 거부 경로 -----------------------------------------------------
#
# 성공 경로는 위에서 격리된 root/storage 로 검증한다. 여기서는 **아무것도 쓰지 않고
# 거절되는지**만 본다 — 라우터의 `storage`/`product_dir` 은 모듈 로드 시 실제 경로에
# 묶이므로, 여기서 성공 경로를 돌리면 개발 DB 와 data/product_knowledge/ 를 오염시킨다.


def _client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def test_http_rejects_unknown_product() -> None:
    response = _client().get("/knowledge/product-knowledge/state", params={"product": "없는제품"})
    assert response.status_code == 400
    assert "config/products" in response.json()["detail"]


def test_http_rejects_path_traversal_in_the_uploaded_name() -> None:
    """엔드포인트가 파일 이름을 그대로 디스크에 쓰지 않는지 — 가장 중요한 거부다."""
    response = _client().post(
        "/knowledge/product-knowledge/asset",
        data={"product": PRODUCT, "kind": "specification"},
        files={"file": ("../../../evil.pdf", b"x", "application/octet-stream")},
    )
    assert response.status_code == 400


def test_http_rejects_unknown_kind() -> None:
    response = _client().post(
        "/knowledge/product-knowledge/asset",
        data={"product": PRODUCT, "kind": "../etc"},
        files={"file": ("(사양서) x.txt", b"x", "application/octet-stream")},
    )
    assert response.status_code == 400


def test_http_rejects_unsupported_extension() -> None:
    response = _client().post(
        "/knowledge/product-knowledge/asset",
        data={"product": PRODUCT, "kind": "specification"},
        files={"file": ("(사양서) x.exe", b"x", "application/octet-stream")},
    )
    assert response.status_code == 400


def test_http_rejects_a_manifest_that_is_not_json() -> None:
    response = _client().post(
        "/knowledge/product-knowledge/commit",
        data={"product": PRODUCT, "manifest": "이건 JSON 이 아니다"},
    )
    assert response.status_code == 400
    assert "manifest" in response.json()["detail"]


def test_http_rejects_a_manifest_that_is_not_an_object() -> None:
    response = _client().post(
        "/knowledge/product-knowledge/commit",
        data={"product": PRODUCT, "manifest": "[1, 2, 3]"},
    )
    assert response.status_code == 400


# --- 종류가 어긋나게 등록된 중복 -----------------------------------------------


def test_the_same_bytes_registered_under_another_kind_are_removed(server) -> None:
    """한 파일이 사양서로도 매뉴얼로도 등록돼 있으면 지운다.

    실서버 첫 업로드에서 실제로 나왔다 — 손으로 올려 둔 매뉴얼 4건이 `specification` 으로
    등록돼 있었고, 수집본이 같은 파일을 `manual` 로 등록하면서 같은 PDF 가 두 번 남았다.
    검색 후보에 두 번 올라 진짜 사양서를 밀어내고, 근거 등급(규칙 §7)까지 어긋난다.
    """
    body = "매뉴얼 본문"
    record = store_asset(PRODUCT, "manual", "(매뉴얼) VXvue Service Manual.V1.0.11_KO.txt", body.encode("utf-8"), root=server.root)

    # 손으로 올린 옛 등록을 흉내낸다 — 같은 내용, 다른 경로, 잘못된 kind.
    hand_uploaded = server.root / "data" / "specifications" / "8f2c.txt"
    hand_uploaded.parent.mkdir(parents=True, exist_ok=True)
    hand_uploaded.write_text(body, encoding="utf-8")
    server.storage.add_document(
        kind="specification",
        product=PRODUCT,
        version="",
        revision="",
        name="(매뉴얼) VXvue Service Manual.V1.0.11_KO.txt",
        path=hand_uploaded,
        metadata={},
    )

    commit(
        PRODUCT,
        {"assets": [{"file_name": record["file_name"], "kind": "manual", "sha256": record["sha256"], "base_name": "VXvue Service Manual"}]},
        uploaded={record["file_name"]: record},
        storage=server.storage,
        root=server.root,
    )

    assert not server.storage.active_documents("specification", PRODUCT), "종류가 어긋난 중복이 남았습니다"
    assert len(server.storage.active_documents("manual", PRODUCT)) == 1
    # 원본 PDF 는 지우지 않는다 — 등록만 정리한다. 잘못 지웠을 때 되돌릴 수 있어야 한다.
    assert hand_uploaded.is_file()


def test_a_different_version_under_another_kind_is_reported_not_deleted(server) -> None:
    """바이트가 다르면 사람이 판단할 몫이다 — 옛 API 매뉴얼을 지울지는 자동으로 정할 수 없다."""
    record = store_asset(PRODUCT, "manual", "(매뉴얼) VXvue API Manual v1.4.4_EN.txt", b"new version", root=server.root)
    old = server.root / "data" / "specifications" / "old.txt"
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_text("older content", encoding="utf-8")
    server.storage.add_document(
        kind="specification", product=PRODUCT, version="", revision="",
        name="(매뉴얼) VXvue API Manual v1.2.0_EN.txt", path=old, metadata={},
    )

    result = commit(
        PRODUCT,
        {"assets": [{"file_name": record["file_name"], "kind": "manual", "sha256": record["sha256"], "base_name": "VXvue API Manual"}]},
        uploaded={record["file_name"]: record},
        storage=server.storage,
        root=server.root,
    )
    assert server.storage.active_documents("specification", PRODUCT), "내용이 다른 문서를 지웠습니다"
    assert result["duplicates"]
