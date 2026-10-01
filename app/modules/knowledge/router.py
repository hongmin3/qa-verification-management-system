from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core import document_cache, knowledge_status
from app.core.config import app_self_url, get_settings
from app.core.knowledge_registry import NeedsColumnMapping, RegistrationError, check_request, extensions_for, register_uploaded
from app.core.knowledge_upload import UploadRejected
from app.core.knowledge_upload import commit as commit_upload
from app.core.knowledge_upload import server_state, store_asset
from app.core.product_config import SOURCE_LABELS, SOURCE_MANUAL, normalize_source
from app.core.product_knowledge import REGISTERABLE_KINDS, load_manifest, resolve_config, scan_source, source_available, sync_and_register
from app.core.storage import Storage
from app.core.uploads import save_upload
from app.parsers.excel_parser import preview_workbook, suggest_columns

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
    """호환용. 설정 파일이 있는 제품만 제품 표에 더한다 (REQ-KNOW-002)."""
    product = product.strip()
    if not product:
        raise HTTPException(400, "제품명을 입력하세요.")
    config = resolve_config(product)
    if config is None:
        raise HTTPException(400, f"'{product}' 제품 설정(config/products/)이 없습니다. docs/PRODUCT_ONBOARDING.md 순서로 먼저 설정 파일을 추가하세요.")
    storage.ensure_product(config.product)
    return RedirectResponse("/knowledge", status_code=303)


@router.get("/knowledge", response_class=HTMLResponse)
def knowledge(request: Request):
    """제품 카드 현황판 (REQ-KNOW-001). 카드 내용은 모두 상태 판정 결과(REQ-KNOW-019)에서 온다."""
    return templates.TemplateResponse(request, "knowledge.html", knowledge_status.overview(storage))


def _config_or_404(slug: str):
    config = resolve_config(slug)
    if config is None:
        raise HTTPException(404, f"'{slug}' 제품 설정이 없습니다.")
    return config


def _config_or_400(product: str):
    config = resolve_config(product.strip())
    if config is None:
        raise HTTPException(400, f"'{product.strip()}' 제품 설정(config/products/)이 없습니다.")
    return config


def _detail_url(config, notice: str = "") -> str:
    url = f"/knowledge/products/{config.slug}"
    return f"{url}?{urlencode({'notice': notice})}" if notice else url


@router.get("/knowledge/products/{slug}", response_class=HTMLResponse)
def product_detail(request: Request, slug: str, notice: str = ""):
    """제품 Knowledge 상세 (REQ-KNOW-020). 제품마다 다른 템플릿을 두지 않는다."""
    config = _config_or_404(slug)
    return templates.TemplateResponse(request, "product.html", {"health": knowledge_status.product_health(config, storage), "notice": notice})


def _owned_dirs() -> list[Path]:
    settings = get_settings()
    return [settings.path("storage.specification_dir"), settings.path("storage.testcase_dir")]


def _optional_id(value: str) -> int | None:
    value = (value or "").strip()
    if not value:
        return None
    if not value.isdigit():
        raise HTTPException(400, "교체할 문서를 찾을 수 없습니다.")
    return int(value)


def _register(config, kind: str, file: UploadFile, replace_id: int | None, source: str = "", version: str = ""):
    """등록·교체 공통 처리 (REQ-KNOW-003·004). 출처 확인은 파일을 저장하기 전에 한다. 성공하면 제품 상세로 303."""
    if kind not in REGISTERABLE_KINDS:
        raise HTTPException(400, f"등록할 수 없는 종류입니다: {kind}")
    try:
        target = check_request(storage, config, kind, source, replace_id)
    except RegistrationError as exc:
        raise HTTPException(exc.status, exc.message)
    directory = get_settings().path("storage.testcase_dir" if kind == "testcase" else "storage.specification_dir")
    path = save_upload(file, directory, extensions_for(kind))
    original_name = file.filename or path.name
    try:
        outcome = register_uploaded(storage, config, kind, path, original_name, replace_target=target, declared_source=source,
                                    version=version, owned_dirs=_owned_dirs())
    except NeedsColumnMapping:
        # 자동 탐지 실패 — 파일은 남겨 두고 열 지정 화면으로 보낸다. 기존 등록은 건드리지 않는다.
        params = {"filename": path.name, "product": config.product, "version": version, "original_name": original_name}
        if replace_id is not None:
            params["replace_id"] = replace_id
        return RedirectResponse(f"/knowledge/testcase/map?{urlencode(params)}", status_code=303)
    except RegistrationError as exc:
        raise HTTPException(exc.status, exc.message)
    return RedirectResponse(_detail_url(config, outcome.summary()), status_code=303)


@router.post("/knowledge/products/{slug}/documents")
def upload_product_document(slug: str, kind: str = Form(...), file: UploadFile = File(...), replace_id: str = Form("")):
    config = _config_or_404(slug)
    return _register(config, kind.strip(), file, _optional_id(replace_id))


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
    sync_id = storage.sync_start(config.product, "product_knowledge", "knowledge_folder")
    try:
        result = sync_and_register(config.product, storage=storage)
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
    for kind in REGISTERABLE_KINDS:
        for document in storage.list_documents(kind):
            if Path(document["path"]).is_file():
                continue
            storage.delete_document(document["id"])
            document_cache.delete(document["id"])
            removed.append({"kind": kind, "id": document["id"], "name": document["name"], "product": document["product"]})
    return {"removed": len(removed), "documents": removed}


@router.get("/knowledge/source/{product}")
def knowledge_source_detail(product: str):
    """지식 폴더 스캔 결과(선택된 자산과 제외 이유). 감사·다른 프로그램용 (REQ-KNOW-014)."""
    config = resolve_config(product)
    if config is None:
        raise HTTPException(404, f"'{product}' 제품 설정이 없습니다.")
    if not source_available(config):
        return {"product": config.product, "available": False, "source_dir": config.knowledge_source.dir, "manifest": load_manifest(config.product)}
    scan = scan_source(config)
    return {
        "product": config.product,
        "available": True,
        "source_dir": scan.source_dir,
        "counts": scan.counts(),
        "selected": [{"kind": asset.kind, "file_name": asset.file_name, "revision": asset.revision, "language": asset.language} for asset in scan.selected],
        "excluded": [{"file_name": asset.file_name, "reason": asset.exclude_reason, "superseded_by": asset.superseded_by} for asset in scan.assets if not asset.selected],
    }


@router.post("/knowledge/specification")
def register_specification(file: UploadFile = File(...), product: str = Form(...), version: str = Form(""),
                           source: str = Form(""), replace_id: str = Form("")):
    """예전 주소. ALM 사양서 동기화(`source=alm_crawler`)도 이 주소로 등록한다 (REQ-SYNC-001)."""
    return _register(_config_or_400(product), "specification", file, _optional_id(replace_id), source, version)


@router.post("/knowledge/testcase")
def register_testcase(file: UploadFile = File(...), product: str = Form(...), version: str = Form(""),
                      source: str = Form(""), replace_id: str = Form("")):
    return _register(_config_or_400(product), "testcase", file, _optional_id(replace_id), source, version)


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
    replace_id: str = "", sheet: str = "", header_row: int = 0, error: str = "",
):
    """TC ID 열을 자동으로 못 찾았을 때 QA 가 시트/제목 행/열을 직접 고르는 화면 (REQ-KNOW-004).
    시트 선택·제목 행 입력까지는 GET 으로 미리보기만 갱신하고, 등록은 아래 POST 에서 확정한다."""
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
            "replace_id": replace_id, "sheets": sheet_names, "selected_sheet": selected_sheet, "preview_rows": preview_rows,
            "header_row": header_row, "fields": TC_FIELD_LABELS, "suggested": suggested, "error": error,
        },
    )


@router.post("/knowledge/testcase/map")
def register_testcase_with_mapping(
    filename: str = Form(...), product: str = Form(...), version: str = Form(""), original_name: str = Form(""),
    replace_id: str = Form(""), sheet: str = Form(...), header_row: int = Form(...),
    tc_id: str = Form(""), category: str = Form(""), feature: str = Form(""), precondition: str = Form(""),
    step: str = Form(""), expected_result: str = Form(""), result: str = Form(""), remark: str = Form(""),
):
    path = _testcase_upload_path(filename)
    config = _config_or_400(product)
    mapping = {
        key: value for key, value in {
            "tc_id": tc_id, "category": category, "feature": feature, "precondition": precondition,
            "step": step, "expected_result": expected_result, "result": result, "remark": remark,
        }.items() if value
    }
    try:
        target = check_request(storage, config, "testcase", "", _optional_id(replace_id))
        outcome = register_uploaded(storage, config, "testcase", path, original_name or filename, replace_target=target,
                                    version=version, owned_dirs=_owned_dirs(),
                                    tc_mapping={"mapping": mapping, "sheet": sheet, "header_row": header_row})
    except RegistrationError as exc:
        raise HTTPException(exc.status, exc.message)
    except ValueError as exc:
        params = urlencode({
            "filename": filename, "product": product, "version": version, "original_name": original_name,
            "replace_id": replace_id, "sheet": sheet, "header_row": header_row, "error": str(exc),
        })
        return RedirectResponse(f"/knowledge/testcase/map?{params}", status_code=303)
    return RedirectResponse(_detail_url(config, outcome.summary()), status_code=303)


@router.post("/knowledge/delete/{document_id}")
def delete_document(document_id: int, source: str = Form(""), next: str = Form("")):
    """지우기 (REQ-KNOW-005). 자동으로 관리하는 자료는 같은 출처를 밝힌 요청만 지운다."""
    document = storage.get_document(document_id)
    if not document:
        raise HTTPException(404, "문서를 찾을 수 없습니다.")
    config = resolve_config(document["product"])
    if config is not None and document["kind"] in REGISTERABLE_KINDS:
        configured = config.source_of(document["kind"])
        declared = normalize_source(source) if source.strip() else SOURCE_MANUAL
        if configured != SOURCE_MANUAL and declared != configured:
            raise HTTPException(409, f"자동으로 관리하는 자료는 여기서 지울 수 없습니다. {SOURCE_LABELS.get(configured, configured)}에서 관리하세요.")
    storage.delete_document(document_id)
    document_cache.delete(document_id)
    path = Path(document["path"])
    if path.exists():
        path.unlink()
    target = next if next.startswith("/knowledge") and "//" not in next and "\\" not in next else "/knowledge"
    return RedirectResponse(target, status_code=303)


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
    from app.modules.knowledge.vxvue_spec_sync import adapter_product, is_available_on_this_host
    from app.modules.knowledge.vxvue_spec_sync import run as run_spec_sync

    product = adapter_product()
    if not product:
        raise HTTPException(400, "ALM 사양서 수집 어댑터의 제품 설정이 없습니다.")
    if storage.is_sync_running(product, "specification"):
        raise HTTPException(409, "이미 사양서 동기화가 진행 중입니다.")
    if not is_available_on_this_host():
        raise HTTPException(400, "이 서버에서는 ALM 크롤러 output 폴더에 접근할 수 없습니다. 크롤러가 있는 Windows PC에서 scripts/sync_vxvue_spec.py를 실행하세요.")
    sync_id = storage.sync_start(product, "specification", "alm_crawler")
    try:
        result = run_spec_sync(app_self_url())
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
