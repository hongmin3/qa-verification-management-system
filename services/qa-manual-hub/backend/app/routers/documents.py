"""Documents, versions, current-version switching, download and preview.

Current-version rules implemented here (spec sections 15, 16, 42):

* a freshly uploaded version becomes current automatically;
* any version can be promoted afterwards via ``POST /set-current``, which is how
  a late-discovered legacy revision gets demoted back out of "current";
* nothing is ever overwritten or deleted -- archive is a status change;
* the document row is locked with ``SELECT ... FOR UPDATE`` before
  ``current_version_id`` moves, so two concurrent uploads cannot interleave.
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession, lazyload

from .. import audit
from ..body_index import index_uploaded_file
from ..db import get_db
from ..deps import get_current_user, require_password_current
from ..models import (
    STATUS_ACTIVE,
    STATUS_ARCHIVED,
    Document,
    DocumentCategory,
    DocumentVersion,
    Product,
    StoredFile,
    User,
)
from ..schemas import (
    DocumentCreate,
    DocumentDetail,
    DocumentRow,
    DocumentUpdate,
    DuplicateFileInfo,
    Message,
    SetCurrentRequest,
    VersionOut,
    VersionUpdate,
    VersionUploadResult,
)
from ..services import (
    build_document_row,
    document_row_select,
    find_duplicates,
    rows_from,
    version_out,
    versions_out,
)
from ..storage import (
    ContentTypeMismatchError,
    ConversionError,
    ExtensionNotAllowedError,
    FileTooLargeError,
    StorageError,
    get_storage,
    is_inline_previewable,
    needs_conversion,
)

router = APIRouter(prefix="/api/documents", tags=["documents"])

log = logging.getLogger("qa_manual_hub")


def _warm_preview_cache(storage_key: str, extension: str) -> None:
    """Convert an office document to PDF ahead of time, off the request path.

    Scheduled as a BackgroundTask after upload / Set-as-Current so a viewer's
    *first* open of a version is a cache hit instead of waiting out a live
    LibreOffice conversion. Best-effort: failures just mean the first real
    preview request converts it instead, same as before this existed.
    """
    if not needs_conversion(extension):
        return
    try:
        get_storage().ensure_preview_pdf(storage_key)
    except ConversionError as exc:
        log.warning("preview pre-conversion failed for %s: %s", storage_key, exc)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _load_document(db: DbSession, document_id: uuid.UUID) -> Document:
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="문서를 찾을 수 없습니다."
        )
    return document


def _load_row(db: DbSession, document_id: uuid.UUID) -> DocumentRow:
    record = db.execute(
        document_row_select().where(Document.id == document_id)
    ).first()
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="문서를 찾을 수 없습니다."
        )
    return build_document_row(*record)


def _lock_document(db: DbSession, document_id: uuid.UUID) -> Document:
    """Row-level lock; every current-version mutation goes through this.

    The eager ``lazy="joined"`` relationships are switched off for this one
    query: PostgreSQL refuses ``FOR UPDATE`` when the statement contains an
    outer join.  The related objects still load on first access, inside the same
    transaction.
    """
    document = db.scalar(
        select(Document)
        .where(Document.id == document_id)
        .options(
            lazyload(Document.product),
            lazyload(Document.category),
            lazyload(Document.current_version),
        )
        .with_for_update(of=Document)
    )
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="문서를 찾을 수 없습니다."
        )
    return document


def _duplicate_infos(
    db: DbSession, sha256: str, exclude_file_id: uuid.UUID | None
) -> list[DuplicateFileInfo]:
    out: list[DuplicateFileInfo] = []
    for stored, version, document, product in find_duplicates(
        db, sha256, exclude_file_id=exclude_file_id
    ):
        out.append(
            DuplicateFileInfo(
                sha256=stored.sha256,
                product_name=product.name if product else None,
                document_name=document.name if document else None,
                version_label=version.label if version else None,
                original_file_name=stored.original_file_name,
                upload_date=version.upload_date if version else stored.created_at,
                uploaded_by_display_name=(
                    version.uploaded_by_display_name if version else None
                ),
            )
        )
    return out


def _parse_optional_date(raw: str | None, field: str) -> date | None:
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field} 형식이 올바르지 않습니다. YYYY-MM-DD 형태로 입력하세요.",
        ) from None


def _clean(raw: str | None, limit: int) -> str | None:
    if raw is None:
        return None
    raw = raw.strip()
    return raw[:limit] if raw else None


# --------------------------------------------------------------------------- #
# documents
# --------------------------------------------------------------------------- #
@router.get("", response_model=list[DocumentRow])
def list_documents(
    product_id: uuid.UUID | None = Query(default=None),
    category_id: uuid.UUID | None = Query(default=None),
    doc_status: str = Query(default=STATUS_ACTIVE, alias="status"),
    q: str | None = Query(default=None, max_length=255),
    db: DbSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[DocumentRow]:
    stmt = document_row_select()
    if product_id:
        stmt = stmt.where(Document.product_id == product_id)
    if category_id:
        stmt = stmt.where(Document.category_id == category_id)
    if doc_status != "all":
        stmt = stmt.where(Document.status == doc_status)
    if q:
        needle = f"%{q.strip().lower()}%"
        stmt = stmt.where(func.lower(Document.name).like(needle))
    stmt = stmt.order_by(
        DocumentCategory.sort_order, func.lower(Document.name)
    )
    return rows_from(db, stmt)


@router.post("", response_model=DocumentRow, status_code=status.HTTP_201_CREATED)
def create_document(
    payload: DocumentCreate,
    request: Request,
    db: DbSession = Depends(get_db),
    user: User = Depends(require_password_current),
) -> DocumentRow:
    product = db.get(Product, payload.product_id)
    if product is None or not product.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="유효한 제품을 선택하세요.",
        )
    category = db.get(DocumentCategory, payload.category_id)
    if category is None or not category.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="유효한 문서 분류를 선택하세요.",
        )

    document = Document(
        product_id=product.id,
        category_id=category.id,
        name=payload.name,
        description=payload.description,
        status=STATUS_ACTIVE,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(document)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"'{product.name}'에 같은 이름의 문서가 이미 있습니다: {payload.name}",
        ) from None

    audit.record(
        db,
        action=audit.DOCUMENT_CREATE,
        actor=user,
        request=request,
        product=product,
        document=document,
        after={
            "name": document.name,
            "category": category.name,
            "description": document.description,
        },
    )
    db.commit()
    return _load_row(db, document.id)


@router.get("/{document_id}", response_model=DocumentDetail)
def get_document(
    document_id: uuid.UUID,
    include_archived_versions: bool = Query(default=True),
    db: DbSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> DocumentDetail:
    row = _load_row(db, document_id)
    stmt = (
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.upload_date.desc(), DocumentVersion.created_at.desc())
    )
    if not include_archived_versions:
        stmt = stmt.where(DocumentVersion.status == STATUS_ACTIVE)

    detail = DocumentDetail(**row.model_dump())
    detail.versions = versions_out(
        db.scalars(stmt).unique().all(), row.current_version_id
    )
    return detail


@router.patch("/{document_id}", response_model=DocumentRow)
def update_document(
    document_id: uuid.UUID,
    payload: DocumentUpdate,
    request: Request,
    db: DbSession = Depends(get_db),
    user: User = Depends(require_password_current),
) -> DocumentRow:
    document = _load_document(db, document_id)
    before = {
        "name": document.name,
        "description": document.description,
        "category": document.category.name if document.category else None,
    }

    data = payload.model_dump(exclude_unset=True)
    if "name" in data and data["name"] is not None:
        name = data["name"].strip()
        if not name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="문서 이름을 입력하세요."
            )
        document.name = name
    if "description" in data:
        document.description = data["description"]
    if data.get("category_id") is not None:
        category = db.get(DocumentCategory, data["category_id"])
        if category is None or not category.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="유효한 문서 분류를 선택하세요.",
            )
        document.category_id = category.id
    document.updated_by = user.id

    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="같은 제품에 동일한 이름의 문서가 이미 있습니다.",
        ) from None

    db.refresh(document)
    audit.record(
        db,
        action=audit.DOCUMENT_UPDATE,
        actor=user,
        request=request,
        document=document,
        before=before,
        after={
            "name": document.name,
            "description": document.description,
            "category": document.category.name if document.category else None,
        },
    )
    db.commit()
    return _load_row(db, document.id)


@router.post("/{document_id}/archive", response_model=DocumentRow)
def archive_document(
    document_id: uuid.UUID,
    request: Request,
    db: DbSession = Depends(get_db),
    user: User = Depends(require_password_current),
) -> DocumentRow:
    document = _load_document(db, document_id)
    if document.status == STATUS_ARCHIVED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="이미 보관된 문서입니다."
        )
    document.status = STATUS_ARCHIVED
    document.archived_at = datetime.now(UTC)
    document.archived_by = user.id
    document.updated_by = user.id
    audit.record(
        db,
        action=audit.DOCUMENT_ARCHIVE,
        actor=user,
        request=request,
        document=document,
        before={"status": STATUS_ACTIVE},
        after={"status": STATUS_ARCHIVED},
        detail="Soft delete. 파일과 버전 이력은 그대로 유지됩니다.",
    )
    db.commit()
    return _load_row(db, document.id)


@router.post("/{document_id}/restore", response_model=DocumentRow)
def restore_document(
    document_id: uuid.UUID,
    request: Request,
    db: DbSession = Depends(get_db),
    user: User = Depends(require_password_current),
) -> DocumentRow:
    document = _load_document(db, document_id)
    if document.status == STATUS_ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="이미 활성 상태인 문서입니다."
        )
    document.status = STATUS_ACTIVE
    document.archived_at = None
    document.archived_by = None
    document.updated_by = user.id
    audit.record(
        db,
        action=audit.DOCUMENT_RESTORE,
        actor=user,
        request=request,
        document=document,
        before={"status": STATUS_ARCHIVED},
        after={"status": STATUS_ACTIVE},
    )
    db.commit()
    return _load_row(db, document.id)


# --------------------------------------------------------------------------- #
# versions
# --------------------------------------------------------------------------- #
@router.get("/{document_id}/versions", response_model=list[VersionOut])
def list_versions(
    document_id: uuid.UUID,
    db: DbSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[VersionOut]:
    document = _load_document(db, document_id)
    versions = db.scalars(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.upload_date.desc(), DocumentVersion.created_at.desc())
    ).unique().all()
    return versions_out(versions, document.current_version_id)


@router.post(
    "/{document_id}/versions",
    response_model=VersionUploadResult,
    status_code=status.HTTP_201_CREATED,
)
def upload_version(
    document_id: uuid.UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    revision: str | None = Form(default=None),
    version: str | None = Form(default=None),
    document_number: str | None = Form(default=None),
    language: str | None = Form(default=None),
    revision_date: str | None = Form(default=None),
    revision_description: str | None = Form(default=None),
    comment: str | None = Form(default=None),
    set_as_current: bool = Form(default=True),
    db: DbSession = Depends(get_db),
    user: User = Depends(require_password_current),
) -> VersionUploadResult:
    document = _lock_document(db, document_id)
    if document.status != STATUS_ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="보관된 문서에는 새 버전을 업로드할 수 없습니다. 먼저 복원하세요.",
        )

    rev = _clean(revision, 64)
    ver = _clean(version, 64)
    if not rev and not ver:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Revision 또는 Version 중 하나는 반드시 입력해야 합니다.",
        )

    rev_date = _parse_optional_date(revision_date, "Revision Date")
    storage = get_storage()
    version_id = uuid.uuid4()

    try:
        saved = storage.save(
            file.file,
            product_id=document.product_id,
            document_id=document.id,
            version_id=version_id,
            original_name=file.filename or "",
        )
    except FileTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)
        ) from exc
    except (ExtensionNotAllowedError, ContentTypeMismatchError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except StorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    try:
        duplicates = _duplicate_infos(db, saved.sha256, exclude_file_id=None)

        stored = StoredFile(
            id=saved.file_id,
            sha256=saved.sha256,
            byte_size=saved.byte_size,
            original_file_name=(file.filename or "unnamed")[:512],
            file_extension=saved.extension,
            mime_type=saved.mime_type,
            storage_backend=storage.backend_name,
            storage_key=saved.storage_key,
            created_by=user.id,
        )
        db.add(stored)

        new_version = DocumentVersion(
            id=version_id,
            document_id=document.id,
            revision=rev,
            version=ver,
            document_number=_clean(document_number, 128),
            language=_clean(language, 32),
            revision_date=rev_date,
            revision_description=(revision_description or "").strip() or None,
            comment=(comment or "").strip() or None,
            uploaded_by_user_id=user.id,
            uploaded_by_login_id=user.login_id,
            uploaded_by_display_name=user.display_name,
            upload_date=datetime.now(UTC),
            stored_file_id=stored.id,
            status=STATUS_ACTIVE,
            updated_by=user.id,
        )
        db.add(new_version)
        db.flush()

        previous = document.current_version
        became_current = False
        if set_as_current:
            document.current_version_id = new_version.id
            became_current = True
        document.updated_by = user.id

        audit.record(
            db,
            action=audit.VERSION_UPLOAD,
            actor=user,
            request=request,
            document=document,
            version=new_version,
            after={
                "revision": new_version.revision,
                "version": new_version.version,
                "document_number": new_version.document_number,
                "language": new_version.language,
                "revision_date": new_version.revision_date,
                "original_file_name": stored.original_file_name,
                "byte_size": stored.byte_size,
                "sha256": stored.sha256,
                "uploaded_by": user.display_name,
                "uploaded_by_login_id": user.login_id,
            },
            detail=(
                f"동일 SHA-256 파일 {len(duplicates)}건 존재"
                if duplicates
                else None
            ),
        )
        if became_current:
            audit.record(
                db,
                action=audit.CURRENT_VERSION_CHANGE,
                actor=user,
                request=request,
                document=document,
                version=new_version,
                before={"current_version": previous.label if previous else None},
                after={"current_version": new_version.label},
                detail="신규 업로드에 따른 자동 Current 지정",
            )

        db.commit()
    except BaseException:
        db.rollback()
        # The physical file has no committed row pointing at it; remove it so a
        # failed upload does not leave orphans in storage.
        storage.discard(saved.storage_key)
        raise

    db.refresh(new_version)
    background_tasks.add_task(index_uploaded_file, stored.id)
    background_tasks.add_task(_warm_preview_cache, saved.storage_key, saved.extension)
    warning = None
    if duplicates:
        first = duplicates[0]
        where = " / ".join(
            part
            for part in (first.product_name, first.document_name, first.version_label)
            if part
        )
        warning = (
            "동일한 내용의 파일이 이미 등록되어 있습니다"
            + (f" ({where})" if where else "")
            + ". 업로드는 정상 완료되었습니다."
        )

    return VersionUploadResult(
        version=version_out(new_version, document.current_version_id),
        became_current=became_current,
        duplicate_of=duplicates,
        warning=warning,
    )


@router.post("/{document_id}/set-current", response_model=DocumentDetail)
def set_current_version(
    document_id: uuid.UUID,
    payload: SetCurrentRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: DbSession = Depends(get_db),
    user: User = Depends(require_password_current),
) -> DocumentDetail:
    document = _lock_document(db, document_id)
    target = db.get(DocumentVersion, payload.version_id)
    if target is None or target.document_id != document.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="이 문서에 속한 버전이 아닙니다.",
        )
    if target.status != STATUS_ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="보관된 버전은 Current로 지정할 수 없습니다. 먼저 복원하세요.",
        )
    if document.current_version_id == target.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="이미 Current 버전입니다."
        )

    previous = document.current_version
    document.current_version_id = target.id
    document.updated_by = user.id

    audit.record(
        db,
        action=audit.CURRENT_VERSION_CHANGE,
        actor=user,
        request=request,
        document=document,
        version=target,
        before={"current_version": previous.label if previous else None},
        after={"current_version": target.label},
        detail="사용자가 수동으로 Current 버전 변경",
    )
    db.commit()
    background_tasks.add_task(
        _warm_preview_cache, target.stored_file.storage_key, target.stored_file.file_extension
    )
    return get_document(document_id, True, db, user)


@router.patch("/{document_id}/versions/{version_id}", response_model=VersionOut)
def update_version(
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    payload: VersionUpdate,
    request: Request,
    db: DbSession = Depends(get_db),
    user: User = Depends(require_password_current),
) -> VersionOut:
    document = _load_document(db, document_id)
    target = db.get(DocumentVersion, version_id)
    if target is None or target.document_id != document.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="버전을 찾을 수 없습니다."
        )

    before = {
        "revision": target.revision,
        "version": target.version,
        "document_number": target.document_number,
        "language": target.language,
        "revision_date": target.revision_date,
        "revision_description": target.revision_description,
        "comment": target.comment,
    }
    data = payload.model_dump(exclude_unset=True)
    for field in (
        "revision",
        "version",
        "document_number",
        "language",
        "revision_description",
        "comment",
    ):
        if field in data:
            value = data[field]
            setattr(target, field, (value or "").strip() or None)
    if "revision_date" in data:
        target.revision_date = data["revision_date"]

    if not target.revision and not target.version:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Revision 또는 Version 중 하나는 반드시 있어야 합니다.",
        )
    target.updated_by = user.id

    audit.record(
        db,
        action=audit.VERSION_UPDATE,
        actor=user,
        request=request,
        document=document,
        version=target,
        before=before,
        after={
            "revision": target.revision,
            "version": target.version,
            "document_number": target.document_number,
            "language": target.language,
            "revision_date": target.revision_date,
            "revision_description": target.revision_description,
            "comment": target.comment,
        },
    )
    db.commit()
    db.refresh(target)
    return version_out(target, document.current_version_id)


@router.post("/{document_id}/versions/{version_id}/archive", response_model=VersionOut)
def archive_version(
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    request: Request,
    db: DbSession = Depends(get_db),
    user: User = Depends(require_password_current),
) -> VersionOut:
    document = _lock_document(db, document_id)
    target = db.get(DocumentVersion, version_id)
    if target is None or target.document_id != document.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="버전을 찾을 수 없습니다."
        )
    if target.status == STATUS_ARCHIVED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="이미 보관된 버전입니다."
        )
    if document.current_version_id == target.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Current 버전은 보관할 수 없습니다. 다른 버전을 Current로 지정한 뒤 "
                "다시 시도하세요."
            ),
        )

    target.status = STATUS_ARCHIVED
    target.archived_at = datetime.now(UTC)
    target.archived_by = user.id
    target.updated_by = user.id
    audit.record(
        db,
        action=audit.VERSION_ARCHIVE,
        actor=user,
        request=request,
        document=document,
        version=target,
        before={"status": STATUS_ACTIVE},
        after={"status": STATUS_ARCHIVED},
        detail="파일은 삭제되지 않고 저장소에 그대로 남습니다.",
    )
    db.commit()
    db.refresh(target)
    return version_out(target, document.current_version_id)


@router.post("/{document_id}/versions/{version_id}/restore", response_model=VersionOut)
def restore_version(
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    request: Request,
    db: DbSession = Depends(get_db),
    user: User = Depends(require_password_current),
) -> VersionOut:
    document = _load_document(db, document_id)
    target = db.get(DocumentVersion, version_id)
    if target is None or target.document_id != document.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="버전을 찾을 수 없습니다."
        )
    if target.status == STATUS_ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="이미 활성 상태인 버전입니다."
        )
    target.status = STATUS_ACTIVE
    target.archived_at = None
    target.archived_by = None
    target.updated_by = user.id
    audit.record(
        db,
        action=audit.VERSION_RESTORE,
        actor=user,
        request=request,
        document=document,
        version=target,
        before={"status": STATUS_ARCHIVED},
        after={"status": STATUS_ACTIVE},
    )
    db.commit()
    db.refresh(target)
    return version_out(target, document.current_version_id)


# --------------------------------------------------------------------------- #
# download / preview
# --------------------------------------------------------------------------- #
def _content_disposition(original_name: str, inline: bool) -> str:
    """RFC 6266 header that survives Korean file names in every browser."""
    disposition = "inline" if inline else "attachment"
    ascii_fallback = (
        original_name.encode("ascii", "ignore").decode("ascii").replace('"', "")
        or "document"
    )
    return (
        f"{disposition}; filename=\"{ascii_fallback}\"; "
        f"filename*=UTF-8''{quote(original_name, safe='')}"
    )


def _serve(
    db: DbSession,
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    user: User,
    request: Request,
    *,
    inline: bool,
) -> FileResponse | StreamingResponse:
    document = _load_document(db, document_id)
    target = db.get(DocumentVersion, version_id)
    if target is None or target.document_id != document.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="버전을 찾을 수 없습니다."
        )

    stored = target.stored_file
    storage = get_storage()
    if not storage.exists(stored.storage_key):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=(
                "저장된 파일을 찾을 수 없습니다. 서버 저장소를 확인하세요 "
                f"(storage_key={stored.storage_key})."
            ),
        )

    serve_inline = inline and is_inline_previewable(stored.file_extension)
    convert = serve_inline and needs_conversion(stored.file_extension)
    audit.record(
        db,
        action=audit.DOCUMENT_DOWNLOAD,
        actor=user,
        request=request,
        document=document,
        version=target,
        detail=("미리보기" if serve_inline else "다운로드")
        + f" / {stored.original_file_name}",
    )
    db.commit()

    if convert:
        try:
            pdf_path = storage.ensure_preview_pdf(stored.storage_key)
        except ConversionError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
            ) from exc
        preview_name = f"{Path(stored.original_file_name).stem}.pdf"
        return FileResponse(
            path=pdf_path,
            media_type="application/pdf",
            headers={
                "Content-Disposition": _content_disposition(preview_name, True),
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "sandbox; default-src 'none'",
            },
        )

    return FileResponse(
        path=storage.path(stored.storage_key),  # type: ignore[arg-type]
        media_type=stored.mime_type or "application/octet-stream",
        headers={
            "Content-Disposition": _content_disposition(
                stored.original_file_name, serve_inline
            ),
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'",
        },
    )


@router.get("/{document_id}/versions/{version_id}/download")
def download_version(
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    request: Request,
    db: DbSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return _serve(db, document_id, version_id, user, request, inline=False)


@router.get("/{document_id}/versions/{version_id}/preview")
def preview_version(
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    request: Request,
    db: DbSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return _serve(db, document_id, version_id, user, request, inline=True)


@router.get("/{document_id}/current/download")
def download_current(
    document_id: uuid.UUID,
    request: Request,
    db: DbSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    document = _load_document(db, document_id)
    if document.current_version_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="이 문서에는 아직 등록된 버전이 없습니다.",
        )
    return _serve(
        db, document_id, document.current_version_id, user, request, inline=False
    )


# --------------------------------------------------------------------------- #
# duplicate pre-check (optional client-side convenience)
# --------------------------------------------------------------------------- #
@router.get("/duplicate-check/{sha256}", response_model=list[DuplicateFileInfo])
def duplicate_check(
    sha256: str,
    db: DbSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[DuplicateFileInfo]:
    digest = sha256.strip().lower()
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="SHA-256 형식이 아닙니다."
        )
    return _duplicate_infos(db, digest, exclude_file_id=None)


@router.post("/{document_id}/archive-check", response_model=Message)
def archive_check(
    document_id: uuid.UUID,
    db: DbSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Message:
    """Tell the user what archiving will and will not touch, before they do it."""
    document = _load_document(db, document_id)
    count = db.scalar(
        select(func.count(DocumentVersion.id)).where(
            DocumentVersion.document_id == document.id
        )
    )
    return Message(
        detail=(
            f"'{document.name}' 문서를 보관 상태로 전환합니다. "
            f"버전 {count}건과 저장된 파일은 삭제되지 않으며 언제든 복원할 수 있습니다."
        )
    )
