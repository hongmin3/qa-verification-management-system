"""Document search (spec section 24).

Straight PostgreSQL ``ILIKE`` / range filters.  At 20 users and a few thousand
versions this stays instant, and it keeps the deployment free of a search
cluster.  A free-text ``q`` sweeps every text column the spec lists; the named
parameters narrow further.
"""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query, HTTPException, Request
from pydantic import BaseModel, Field
from threading import Lock
from sqlalchemy import and_, case, func, or_, select, text
from sqlalchemy.orm import Session as DbSession

from ..db import get_db
from ..deps import get_current_user
from ..body_extract import INDEX_VERSION, snippet
from ..body_index import index_stored_file
from ..models import (
    Document,
    DocumentCategory,
    DocumentVersion,
    Product,
    StoredFile,
    User,
)
from ..schemas import SearchHit

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("", response_model=list[SearchHit])
def search(
    q: str | None = Query(default=None, max_length=255, description="통합 부분 검색"),
    product_id: uuid.UUID | None = None,
    category_id: uuid.UUID | None = None,
    document_name: str | None = Query(default=None, max_length=255),
    document_number: str | None = Query(default=None, max_length=128),
    revision: str | None = Query(default=None, max_length=64),
    version: str | None = Query(default=None, max_length=64),
    language: str | None = Query(default=None, max_length=32),
    file_name: str | None = Query(default=None, max_length=512),
    uploaded_by: str | None = Query(default=None, max_length=128),
    revision_date_from: date | None = None,
    revision_date_to: date | None = None,
    upload_date_from: date | None = None,
    upload_date_to: date | None = None,
    document_status: str = Query(default="active", pattern="^(active|archived|all)$"),
    version_status: str = Query(default="all", pattern="^(active|archived|all)$"),
    current_only: bool = False,
    limit: int = Query(default=200, ge=1, le=1000),
    db: DbSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[SearchHit]:
    index_current = and_(
        StoredFile.body_sha256 == StoredFile.sha256,
        StoredFile.body_index_version == INDEX_VERSION,
    )
    searchable_body = case(
        (and_(index_current, StoredFile.body_status.in_(('ready', 'truncated'))), StoredFile.body_text),
        else_=None,
    )
    stmt = (
        select(
            Document.id,
            Document.name,
            Product.id,
            Product.name,
            DocumentCategory.name,
            Document.status,
            DocumentVersion.id,
            DocumentVersion.revision,
            DocumentVersion.version,
            DocumentVersion.document_number,
            DocumentVersion.language,
            DocumentVersion.revision_date,
            DocumentVersion.upload_date,
            DocumentVersion.uploaded_by_display_name,
            StoredFile.original_file_name,
            DocumentVersion.status,
            Document.current_version_id,
            searchable_body,
            case((index_current, StoredFile.body_status), else_='pending'),
            case((StoredFile.id.is_(None), None), (index_current, StoredFile.body_detail), else_='원본 해시 또는 추출기 판이 달라 재색인이 필요합니다.'),
        )
        .join(Product, Product.id == Document.product_id)
        .join(DocumentCategory, DocumentCategory.id == Document.category_id)
        # Outer join: a document with no versions yet must still be findable.
        .outerjoin(DocumentVersion, DocumentVersion.document_id == Document.id)
        .outerjoin(StoredFile, StoredFile.id == DocumentVersion.stored_file_id)
    )

    if q:
        needle = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Document.name).like(needle),
                func.lower(Product.name).like(needle),
                func.lower(func.coalesce(Product.code, "")).like(needle),
                func.lower(DocumentCategory.name).like(needle),
                func.lower(func.coalesce(Document.description, "")).like(needle),
                func.lower(func.coalesce(DocumentVersion.revision, "")).like(needle),
                func.lower(func.coalesce(DocumentVersion.version, "")).like(needle),
                func.lower(func.coalesce(DocumentVersion.document_number, "")).like(
                    needle
                ),
                func.lower(func.coalesce(DocumentVersion.language, "")).like(needle),
                func.lower(
                    func.coalesce(DocumentVersion.revision_description, "")
                ).like(needle),
                func.lower(func.coalesce(DocumentVersion.comment, "")).like(needle),
                func.lower(
                    func.coalesce(DocumentVersion.uploaded_by_display_name, "")
                ).like(needle),
                func.lower(
                    func.coalesce(DocumentVersion.uploaded_by_login_id, "")
                ).like(needle),
                func.lower(func.coalesce(StoredFile.original_file_name, "")).like(
                    needle
                ),
                func.lower(func.coalesce(searchable_body, "")).like(needle),
            )
        )

    def like(column, value: str):
        return func.lower(func.coalesce(column, "")).like(f"%{value.strip().lower()}%")

    if product_id:
        stmt = stmt.where(Document.product_id == product_id)
    if category_id:
        stmt = stmt.where(Document.category_id == category_id)
    if document_name:
        stmt = stmt.where(like(Document.name, document_name))
    if document_number:
        stmt = stmt.where(like(DocumentVersion.document_number, document_number))
    if revision:
        stmt = stmt.where(like(DocumentVersion.revision, revision))
    if version:
        stmt = stmt.where(like(DocumentVersion.version, version))
    if language:
        stmt = stmt.where(like(DocumentVersion.language, language))
    if file_name:
        stmt = stmt.where(like(StoredFile.original_file_name, file_name))
    if uploaded_by:
        needle = f"%{uploaded_by.strip().lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(
                    func.coalesce(DocumentVersion.uploaded_by_display_name, "")
                ).like(needle),
                func.lower(
                    func.coalesce(DocumentVersion.uploaded_by_login_id, "")
                ).like(needle),
            )
        )
    if revision_date_from:
        stmt = stmt.where(DocumentVersion.revision_date >= revision_date_from)
    if revision_date_to:
        stmt = stmt.where(DocumentVersion.revision_date <= revision_date_to)
    if upload_date_from:
        stmt = stmt.where(func.date(DocumentVersion.upload_date) >= upload_date_from)
    if upload_date_to:
        stmt = stmt.where(func.date(DocumentVersion.upload_date) <= upload_date_to)
    if document_status != "all":
        stmt = stmt.where(Document.status == document_status)
    if version_status != "all":
        stmt = stmt.where(DocumentVersion.status == version_status)
    if current_only:
        stmt = stmt.where(DocumentVersion.id == Document.current_version_id)

    stmt = stmt.order_by(
        Product.sort_order,
        func.lower(Product.name),
        func.lower(Document.name),
        DocumentVersion.upload_date.desc().nullslast(),
    ).limit(limit)

    hits: list[SearchHit] = []
    for row in db.execute(stmt):
        (
            doc_id,
            doc_name,
            prod_id,
            prod_name,
            cat_name,
            doc_status,
            ver_id,
            rev,
            ver,
            doc_number,
            lang,
            rev_date,
            up_date,
            uploader,
            file_name_value,
            ver_status,
            current_id,
            body_text,
            body_status,
            body_detail,
        ) = row
        hits.append(
            SearchHit(
                document_id=doc_id,
                document_name=doc_name,
                product_id=prod_id,
                product_name=prod_name,
                category_name=cat_name,
                document_status=doc_status,
                version_id=ver_id,
                revision=rev,
                version=ver,
                document_number=doc_number,
                language=lang,
                revision_date=rev_date,
                upload_date=up_date,
                uploaded_by_display_name=uploader,
                original_file_name=file_name_value,
                version_status=ver_status,
                is_current=bool(ver_id and current_id and ver_id == current_id),
                snippet=snippet(body_text, q),
                body_status=body_status or 'pending',
                body_detail=body_detail,
            )
        )
    return hits


class ReindexRequest(BaseModel):
    cursor: uuid.UUID | None = None
    limit: int = Field(default=2, ge=1, le=5)
    current_only: bool = True
    force: bool = False


_reindex_lock = Lock()


def _require_reindex_admin(user: User = Depends(get_current_user)) -> User:
    """Keep the maintenance gate compatible with installed Hub auth modules."""
    if not user.is_admin:
        raise HTTPException(403, '관리자 권한이 필요합니다.')
    if user.must_change_password:
        raise HTTPException(428, '비밀번호를 먼저 변경해야 합니다.')
    return user


@router.post('/reindex')
def reindex(payload: ReindexRequest, request: Request,
            db: DbSession = Depends(get_db),
            user: User = Depends(_require_reindex_admin)):
    """Explicit bounded batches, current revisions first; no startup backfill."""
    origin = request.headers.get('origin')
    if origin:
        from urllib.parse import urlsplit
        if urlsplit(origin).netloc != request.headers.get('host'):
            raise HTTPException(403, '다른 사이트에서 색인을 갱신할 수 없습니다.')
    if request.headers.get('sec-fetch-site') == 'cross-site':
        raise HTTPException(403, '다른 사이트에서 색인을 갱신할 수 없습니다.')
    if not _reindex_lock.acquire(blocking=False):
        raise HTTPException(409, '다른 색인 갱신이 진행 중입니다.')
    try:
        if db.get_bind().dialect.name == 'postgresql' and not db.scalar(text('SELECT pg_try_advisory_xact_lock(748392015)')):
            raise HTTPException(409, '다른 색인 갱신이 진행 중입니다.')
        stmt = select(StoredFile).where(StoredFile.id.in_(select(DocumentVersion.stored_file_id)))
        if payload.current_only:
            stmt = stmt.where(StoredFile.id.in_(select(DocumentVersion.stored_file_id).join(Document, Document.current_version_id == DocumentVersion.id)))
        if payload.cursor:
            stmt = stmt.where(StoredFile.id > payload.cursor)
        remaining = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = list(db.scalars(stmt.order_by(StoredFile.id).limit(payload.limit)))
        values = []
        for stored in rows:
            index_stored_file(db, stored, payload.force)
            values.append({'id': str(stored.id), 'status': stored.body_status, 'detail': stored.body_detail})
        db.commit()
        done = remaining <= len(rows)
        return {'processed': len(rows), 'results': values, 'next_cursor': None if done or not rows else str(rows[-1].id),
                'done': done, 'current_only': payload.current_only, 'remaining': max(0, remaining - len(rows))}
    finally:
        _reindex_lock.release()
