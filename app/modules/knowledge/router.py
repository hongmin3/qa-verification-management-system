from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core import document_cache
from app.core.config import get_settings
from app.core.knowledge_upload import UploadRejected
from app.core.knowledge_upload import commit as commit_upload
from app.core.knowledge_upload import server_state, store_asset
from app.core.product_knowledge import KIND_MANUAL, load_manifest, resolve_config, scan_source, source_available, sync_and_register
from app.core.qa_rules import load_rule_set
from app.core.storage import Storage
from app.core.uploads import save_upload
from app.parsers.document_parser import extract_document_text, parse_document
from app.parsers.excel_parser import parse_testcases, preview_workbook, suggest_columns

router = APIRouter()
templates = Jinja2Templates(directory=[Path(__file__).parent / "templates", get_settings().root / "app" / "web" / "templates"])
storage = Storage()

TC_FIELD_LABELS = {
    "tc_id": "TC ID (필수)", "category": "분류", "feature": "기능명", "precondition": "사전조건",
    "step": "시험절차", "expected_result": "예상결과", "result": "결과", "remark": "비고",
}


@router.get("/knowledge/guide", response_class=HTMLResponse)
def knowledge_guide(request: Request):
    return templates.TemplateResponse(request, "guide.html", {})


@router.post("/knowledge/products")
def register_product(product: str = Form(...)):
    product = product.strip()
    if not product:
        raise HTTPException(400, "제품명을 입력하세요.")
    storage.ensure_product(product)
    return RedirectResponse("/knowledge", status_code=303)


def _versions_by_product() -> dict[str, list[str]]:
    return {product: storage.list_versions(product) for product in storage.list_products()}


def _grouped_by_product_version(kind: str) -> list[dict]:
    groups: dict[tuple[str, str], list[dict]] = {}
    for document in storage.list_documents(kind):
        groups.setdefault((document["product"], document["version"]), []).append(document)
    return [{"product": product, "version": version, "documents": documents} for (product, version), documents in groups.items()]


def _knowledge_source_status() -> list[dict]:
    """제품별 지식 폴더 상태. 이 호스트에서 폴더에 접근 가능한지와 마지막 수집 결과를 보여준다."""
    rows: list[dict] = []
    for product in storage.list_products():
        config = resolve_config(product)
        manifest = load_manifest(product)
        rule_set = load_rule_set(product)
        available = bool(config and source_available(config))
        row = {
            "product": product,
            "configured": bool(config and config.knowledge_source.dir),
            "source_dir": config.knowledge_source.dir if config else "",
            "available": available,
            "synced_at": manifest.get("synced_at", ""),
            "counts": manifest.get("counts", {}),
            "excluded": len(manifest.get("excluded", [])),
            "rule_revision": rule_set.revision,
            "rules_available": rule_set.available,
            "last_sync": storage.latest_sync(product, "product_knowledge"),
        }
        if available and config is not None:
            scan = scan_source(config)
            row["pending"] = scan.counts()
        rows.append(row)
    return rows


@router.get("/knowledge", response_class=HTMLResponse)
def knowledge(request: Request):
    return templates.TemplateResponse(
        request,
        "knowledge.html",
        {
            "spec_groups": _grouped_by_product_version("specification"),
            "testcase_groups": _grouped_by_product_version("testcase"),
            "manual_groups": _grouped_by_product_version(KIND_MANUAL),
            "products": storage.list_products(),
            "versions_by_product": _versions_by_product(),
            "spec_sync": storage.latest_sync("VXvue", "specification"),
            "knowledge_sources": _knowledge_source_status(),
        },
    )


@router.post("/knowledge/sync/product-knowledge")
def trigger_product_knowledge_sync(product: str = Form(...)):
    """제품 지식 폴더의 최신본을 수집하고 분석 대상으로 등록한다.

    복사만으로는 분석에 쓰이지 않으므로 등록까지 한 번에 수행한다
    (`app/core/product_knowledge.sync_and_register`).
    """
    product = product.strip()
    if storage.is_sync_running(product, "product_knowledge"):
        raise HTTPException(409, f"'{product}' 지식 폴더 수집이 이미 진행 중입니다.")
    config = resolve_config(product)
    if config is None or not config.knowledge_source.dir:
        raise HTTPException(400, f"config/products/ 에 '{product}' 의 knowledge_source.dir 설정이 없습니다.")
    if not source_available(config):
        raise HTTPException(
            400,
            f"이 서버에서 지식 폴더에 접근할 수 없습니다: {config.knowledge_source.dir}. "
            "폴더가 있는 PC에서 scripts/sync_product_knowledge.py 를 실행하세요.",
        )
    sync_id = storage.sync_start(product, "product_knowledge", "knowledge_folder")
    try:
        result = sync_and_register(product, storage=storage)
        storage.sync_finish(sync_id, result["status"], result["detail"])
        return result
    except Exception as exc:
        storage.sync_finish(sync_id, "FAILED", str(exc))
        raise HTTPException(500, f"수집 실패: {exc}")


@router.get("/knowledge/product-knowledge/state")
def product_knowledge_state(product: str):
    """서버가 이미 가진 자산 목록 (file_name + sha256).

    담당자 PC 가 이것과 비교해 **바뀐 파일만** 올린다. 이 한 번의 조회가 없으면 매주 약
    192MB 를 통째로 다시 보내게 된다.
    """
    try:
        return server_state(product.strip())
    except UploadRejected as exc:
        raise HTTPException(400, str(exc))


@router.post("/knowledge/product-knowledge/asset")
async def upload_product_knowledge_asset(
    product: str = Form(...),
    kind: str = Form(...),
    sha256: str = Form(""),
    file: UploadFile = File(...),
):
    """지식 자산 원본 하나를 받는다. 정규화 텍스트는 서버가 직접 뽑는다.

    파일 이름은 그대로 디스크 경로가 되므로 `safe_file_name` 이 다시 검증한다 — 보내는
    쪽을 신뢰하지 않는다 (`app/core/knowledge_upload.py`).
    """
    data = await file.read()
    try:
        return store_asset(product.strip(), kind.strip(), file.filename or "", data, sha256.strip())
    except UploadRejected as exc:
        raise HTTPException(400, str(exc))


@router.post("/knowledge/product-knowledge/commit")
def commit_product_knowledge(product: str = Form(...), manifest: str = Form(...), uploaded: str = Form("{}")):
    """업로드된 자산을 서버 상태로 확정하고 문서로 등록한다.

    sync_log 에 남겨 `/knowledge` 화면의 "마지막 동기화"가 서버 자체 수집과 똑같이 보이게
    한다 — 담당자가 보는 것은 어느 경로로 들어왔는지가 아니라 언제 최신화됐는지다.
    """
    product = product.strip()
    if storage.is_sync_running(product, "product_knowledge"):
        raise HTTPException(409, f"'{product}' 지식 폴더 수집이 이미 진행 중입니다.")
    try:
        manifest_payload = json.loads(manifest)
        uploaded_payload = json.loads(uploaded or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"manifest 를 읽을 수 없습니다: {exc}")
    if not isinstance(manifest_payload, dict) or not isinstance(uploaded_payload, dict):
        raise HTTPException(400, "manifest 와 uploaded 는 JSON 객체여야 합니다.")

    sync_id = storage.sync_start(product, "product_knowledge", "upload")
    try:
        result = commit_upload(product, manifest_payload, uploaded=uploaded_payload, storage=storage)
    except UploadRejected as exc:
        storage.sync_finish(sync_id, "FAILED", str(exc))
        raise HTTPException(400, str(exc))
    except Exception as exc:
        storage.sync_finish(sync_id, "FAILED", str(exc))
        raise HTTPException(500, f"확정 실패: {exc}")
    storage.sync_finish(sync_id, result["status"], result["detail"])
    return result


@router.post("/knowledge/cleanup/missing")
def cleanup_missing_documents():
    """원본 파일이 사라진 등록을 정리한다.

    파일이 없는 등록은 검색에 아무것도 기여하지 못하면서 "사양 없음" 오판만 만든다
    (실제로 이 등록 하나 때문에 분석 전체가 실패한 적이 있다). 파일이 이미 없으므로
    지워도 잃는 것이 없다 — 무엇을 지웠는지는 반환값에 남긴다.
    """
    removed: list[dict] = []
    for kind in ("specification", "testcase", KIND_MANUAL):
        for document in storage.list_documents(kind):
            if Path(document["path"]).is_file():
                continue
            storage.delete_document(document["id"])
            document_cache.delete(document["id"])
            removed.append({"kind": kind, "id": document["id"], "name": document["name"], "product": document["product"]})
    return {"removed": len(removed), "documents": removed}


@router.get("/knowledge/source/{product}")
def knowledge_source_detail(product: str):
    """지식 폴더 스캔 결과(선택된 자산과 제외 이유). 무엇이 왜 빠졌는지 확인용."""
    config = resolve_config(product)
    if config is None:
        raise HTTPException(404, f"'{product}' 제품 설정이 없습니다.")
    if not source_available(config):
        return {"product": product, "available": False, "source_dir": config.knowledge_source.dir, "manifest": load_manifest(product)}
    scan = scan_source(config)
    return {
        "product": product,
        "available": True,
        "source_dir": scan.source_dir,
        "counts": scan.counts(),
        "selected": [{"kind": asset.kind, "file_name": asset.file_name, "revision": asset.revision, "language": asset.language} for asset in scan.selected],
        "excluded": [{"file_name": asset.file_name, "reason": asset.exclude_reason, "superseded_by": asset.superseded_by} for asset in scan.assets if not asset.selected],
    }


@router.post("/knowledge/specification")
def register_specification(file: UploadFile = File(...), product: str = Form(...), version: str = Form("")):
    settings = get_settings()
    storage.ensure_product(product)
    storage.ensure_version(product, version)
    path = save_upload(file, settings.path("storage.specification_dir"), {".pdf", ".docx"})
    chunks = parse_document(path, path.stem)
    document_id = storage.add_document("specification", product, version, "", file.filename or path.name, path, {"chunk_count": len(chunks)})
    # 분석/매뉴얼 검증이 매번 원본을 다시 파싱하지 않도록 지금 파싱한 결과를 바로 캐시해둔다
    # (Rule 기반 diff가 쓰는 전체 원문도 함께 — 둘 다 원본 파일을 다시 여는 비용이 크다).
    document_cache.save(document_id, chunks)
    document_cache.save_text(document_id, extract_document_text(path))
    return RedirectResponse("/knowledge", status_code=303)


@router.post("/knowledge/testcase")
def register_testcase(file: UploadFile = File(...), product: str = Form(...), version: str = Form("")):
    storage.ensure_product(product)
    storage.ensure_version(product, version)
    path = save_upload(file, get_settings().path("storage.testcase_dir"), {".xlsx"})
    original_name = file.filename or path.name
    try:
        cases = parse_testcases(path)
    except ValueError:
        # 자동 탐지 실패 — 파일은 이미 storage.testcase_dir에 저장돼 있으니 삭제하지 않고
        # 수동 매핑 화면으로 보낸다. 사용자가 매핑을 확정해야 documents 테이블에 등록된다.
        params = urlencode({"filename": path.name, "product": product, "version": version, "original_name": original_name})
        return RedirectResponse(f"/knowledge/testcase/map?{params}", status_code=303)
    document_id = storage.add_document("testcase", product, version, "", original_name, path)
    document_cache.save(document_id, cases)
    return RedirectResponse("/knowledge", status_code=303)


def _testcase_upload_path(filename: str) -> Path:
    if filename != Path(filename).name:
        raise HTTPException(400, "잘못된 파일명입니다.")
    path = get_settings().path("storage.testcase_dir") / filename
    if not path.exists():
        raise HTTPException(404, "업로드된 파일을 찾을 수 없습니다. TC 파일을 다시 첨부하세요.")
    return path


@router.get("/knowledge/testcase/map", response_class=HTMLResponse)
def testcase_mapping_form(
    request: Request, filename: str, product: str, version: str = "", original_name: str = "",
    sheet: str = "", header_row: int = 0, error: str = "",
):
    """`register_testcase`가 TC ID 컬럼을 자동으로 못 찾았을 때 QA가 시트/헤더 행/컬럼을
    직접 지정하는 화면. 시트 선택·헤더 행 입력까지는 GET으로 미리보기만 갱신하고, 실제
    등록은 아래 POST에서 확정한다."""
    path = _testcase_upload_path(filename)
    preview = preview_workbook(path)
    sheet_names = list(preview.keys())
    selected_sheet = sheet if sheet in preview else (sheet_names[0] if sheet_names else "")
    preview_rows = preview.get(selected_sheet, [])
    suggested: dict[str, str] = {}
    if header_row and 1 <= header_row <= len(preview_rows):
        header_cells = preview_rows[header_row - 1]
        suggested = {field: header_cells[index] for index, field in suggest_columns(header_cells).items() if header_cells[index]}
    return templates.TemplateResponse(
        request,
        "testcase_mapping.html",
        {
            "filename": filename, "product": product, "version": version, "original_name": original_name,
            "sheets": sheet_names, "selected_sheet": selected_sheet, "preview_rows": preview_rows,
            "header_row": header_row, "fields": TC_FIELD_LABELS, "suggested": suggested, "error": error,
        },
    )


@router.post("/knowledge/testcase/map")
def register_testcase_with_mapping(
    filename: str = Form(...), product: str = Form(...), version: str = Form(""), original_name: str = Form(""),
    sheet: str = Form(...), header_row: int = Form(...),
    tc_id: str = Form(""), category: str = Form(""), feature: str = Form(""), precondition: str = Form(""),
    step: str = Form(""), expected_result: str = Form(""), result: str = Form(""), remark: str = Form(""),
):
    path = _testcase_upload_path(filename)
    mapping = {
        key: value for key, value in {
            "tc_id": tc_id, "category": category, "feature": feature, "precondition": precondition,
            "step": step, "expected_result": expected_result, "result": result, "remark": remark,
        }.items() if value
    }
    try:
        cases = parse_testcases(path, mapping=mapping, sheet_name=sheet, header_row=header_row)
    except ValueError as exc:
        params = urlencode({
            "filename": filename, "product": product, "version": version, "original_name": original_name,
            "sheet": sheet, "header_row": header_row, "error": str(exc),
        })
        return RedirectResponse(f"/knowledge/testcase/map?{params}", status_code=303)
    storage.ensure_product(product)
    storage.ensure_version(product, version)
    document_id = storage.add_document("testcase", product, version, "", original_name or filename, path)
    storage.update_document_metadata(document_id, {"column_mapping": mapping, "sheet_name": sheet, "header_row": header_row})
    document_cache.save(document_id, cases)
    return RedirectResponse("/knowledge", status_code=303)


@router.post("/knowledge/delete/{document_id}")
def delete_document(document_id: int):
    document = storage.get_document(document_id)
    if not document:
        raise HTTPException(404, "문서를 찾을 수 없습니다.")
    storage.delete_document(document_id)
    document_cache.delete(document_id)
    path = Path(document["path"])
    if path.exists():
        path.unlink()
    return RedirectResponse("/knowledge", status_code=303)


@router.get("/knowledge/documents")
def list_documents_json(kind: str, product: str):
    return [{"id": doc["id"], "name": doc["name"]} for doc in storage.active_documents(kind, product)]


@router.post("/knowledge/sync-log")
def record_sync_log(product: str = Form(...), kind: str = Form(...), source: str = Form(...), status: str = Form(...), detail: str = Form("")):
    sync_id = storage.sync_start(product, kind, source)
    storage.sync_finish(sync_id, status, detail)
    return {"ok": True}


@router.post("/knowledge/sync/specification")
def trigger_specification_sync():
    from app.modules.impact_analyzer.vxvue_spec_sync import is_available_on_this_host
    from app.modules.impact_analyzer.vxvue_spec_sync import run as run_spec_sync

    product = "VXvue"
    if storage.is_sync_running(product, "specification"):
        raise HTTPException(409, "이미 사양서 동기화가 진행 중입니다.")
    if not is_available_on_this_host():
        raise HTTPException(400, "이 서버에서는 ALM 크롤러 output 폴더에 접근할 수 없습니다. 크롤러가 있는 Windows PC에서 scripts/sync_vxvue_spec.py를 실행하세요.")
    sync_id = storage.sync_start(product, "specification", "alm_crawler")
    try:
        port = get_settings().get("app.port", 12000)
        result = run_spec_sync(f"http://127.0.0.1:{port}")
        storage.sync_finish(sync_id, result["status"], result["detail"])
        return result
    except Exception as exc:
        storage.sync_finish(sync_id, "FAILED", str(exc))
        raise HTTPException(500, f"동기화 실패: {exc}")


@router.get("/knowledge/download/{document_id}")
def download_document(document_id: int):
    document = storage.get_document(document_id)
    if not document:
        raise HTTPException(404, "문서를 찾을 수 없습니다.")
    path = Path(document["path"])
    if not path.exists():
        raise HTTPException(404, "원본 파일을 찾을 수 없습니다.")
    return FileResponse(path, filename=document["name"])
