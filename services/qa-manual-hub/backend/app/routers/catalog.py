"""Product and document-category management.

Both are reference data that documents point at, so neither is ever hard
deleted -- ``is_active`` toggles visibility instead (spec sections 5, 12).
Creation/modification is admin-only; every logged-in user can read.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from .. import audit
from ..db import get_db
from ..deps import get_current_user, require_admin_password_current
from ..models import (
    STATUS_ACTIVE,
    Document,
    DocumentCategory,
    DocumentVersion,
    Product,
    User,
)
from ..schemas import (
    CategoryCreate,
    CategoryOut,
    CategoryUpdate,
    CategoryWithStats,
    ProductCreate,
    ProductOut,
    ProductUpdate,
    ProductWithStats,
)

products_router = APIRouter(prefix="/api/products", tags=["products"])
categories_router = APIRouter(prefix="/api/categories", tags=["categories"])


# --------------------------------------------------------------------------- #
# products
# --------------------------------------------------------------------------- #
@products_router.get("", response_model=list[ProductWithStats])
def list_products(
    include_inactive: bool = Query(default=False),
    q: str | None = Query(default=None, max_length=128),
    db: DbSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[ProductWithStats]:
    doc_counts = (
        select(
            Document.product_id.label("pid"),
            func.count(Document.id).label("doc_count"),
        )
        .where(Document.status == STATUS_ACTIVE)
        .group_by(Document.product_id)
        .subquery()
    )
    ver_stats = (
        select(
            Document.product_id.label("pid"),
            func.count(DocumentVersion.id).label("ver_count"),
            func.max(DocumentVersion.upload_date).label("last_upload"),
        )
        .join(DocumentVersion, DocumentVersion.document_id == Document.id)
        .group_by(Document.product_id)
        .subquery()
    )
    creator = select(User.id, User.display_name).subquery()

    stmt = (
        select(
            Product,
            func.coalesce(doc_counts.c.doc_count, 0),
            func.coalesce(ver_stats.c.ver_count, 0),
            ver_stats.c.last_upload,
            creator.c.display_name,
        )
        .outerjoin(doc_counts, doc_counts.c.pid == Product.id)
        .outerjoin(ver_stats, ver_stats.c.pid == Product.id)
        .outerjoin(creator, creator.c.id == Product.created_by)
        .order_by(Product.sort_order, func.lower(Product.name))
    )
    if not include_inactive:
        stmt = stmt.where(Product.is_active.is_(True))
    if q:
        needle = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            func.lower(Product.name).like(needle)
            | func.lower(func.coalesce(Product.code, "")).like(needle)
        )

    out: list[ProductWithStats] = []
    for product, doc_count, ver_count, last_upload, creator_name in db.execute(stmt):
        row = ProductWithStats.model_validate(product)
        row.document_count = doc_count
        row.version_count = ver_count
        row.last_upload_at = last_upload
        row.created_by_display_name = creator_name
        out.append(row)
    return out


@products_router.post(
    "", response_model=ProductOut, status_code=status.HTTP_201_CREATED
)
def create_product(
    payload: ProductCreate,
    request: Request,
    db: DbSession = Depends(get_db),
    admin: User = Depends(require_admin_password_current),
) -> ProductOut:
    product = Product(
        name=payload.name,
        code=payload.code,
        description=payload.description,
        sort_order=payload.sort_order,
        created_by=admin.id,
        updated_by=admin.id,
    )
    db.add(product)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"이미 등록된 제품입니다: {payload.name}",
        ) from None

    audit.record(
        db,
        action=audit.PRODUCT_CREATE,
        actor=admin,
        request=request,
        product=product,
        after={
            "name": product.name,
            "code": product.code,
            "description": product.description,
        },
    )
    db.commit()
    return ProductOut.model_validate(product)


@products_router.get("/{product_id}", response_model=ProductOut)
def get_product(
    product_id: uuid.UUID,
    db: DbSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> ProductOut:
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="제품을 찾을 수 없습니다."
        )
    return ProductOut.model_validate(product)


@products_router.patch("/{product_id}", response_model=ProductOut)
def update_product(
    product_id: uuid.UUID,
    payload: ProductUpdate,
    request: Request,
    db: DbSession = Depends(get_db),
    admin: User = Depends(require_admin_password_current),
) -> ProductOut:
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="제품을 찾을 수 없습니다."
        )

    before = {
        "name": product.name,
        "code": product.code,
        "description": product.description,
        "is_active": product.is_active,
        "sort_order": product.sort_order,
    }
    data = payload.model_dump(exclude_unset=True)
    if "name" in data and data["name"] is not None:
        name = data["name"].strip()
        if not name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="제품 이름을 입력하세요."
            )
        product.name = name
    if "code" in data:
        product.code = (data["code"] or "").strip() or None
    if "description" in data:
        product.description = data["description"]
    if data.get("is_active") is not None:
        product.is_active = data["is_active"]
    if data.get("sort_order") is not None:
        product.sort_order = data["sort_order"]
    product.updated_by = admin.id

    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="이미 등록된 제품 이름/코드입니다."
        ) from None

    audit.record(
        db,
        action=audit.PRODUCT_UPDATE,
        actor=admin,
        request=request,
        product=product,
        before=before,
        after={
            "name": product.name,
            "code": product.code,
            "description": product.description,
            "is_active": product.is_active,
            "sort_order": product.sort_order,
        },
    )
    db.commit()
    return ProductOut.model_validate(product)


# --------------------------------------------------------------------------- #
# categories
# --------------------------------------------------------------------------- #
@categories_router.get("", response_model=list[CategoryWithStats])
def list_categories(
    include_inactive: bool = Query(default=False),
    db: DbSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[CategoryWithStats]:
    counts = (
        select(
            Document.category_id.label("cid"),
            func.count(Document.id).label("doc_count"),
        )
        .where(Document.status == STATUS_ACTIVE)
        .group_by(Document.category_id)
        .subquery()
    )
    stmt = (
        select(DocumentCategory, func.coalesce(counts.c.doc_count, 0))
        .outerjoin(counts, counts.c.cid == DocumentCategory.id)
        .order_by(DocumentCategory.sort_order, func.lower(DocumentCategory.name))
    )
    if not include_inactive:
        stmt = stmt.where(DocumentCategory.is_active.is_(True))

    out: list[CategoryWithStats] = []
    for category, doc_count in db.execute(stmt):
        row = CategoryWithStats.model_validate(category)
        row.document_count = doc_count
        out.append(row)
    return out


@categories_router.post(
    "", response_model=CategoryOut, status_code=status.HTTP_201_CREATED
)
def create_category(
    payload: CategoryCreate,
    request: Request,
    db: DbSession = Depends(get_db),
    admin: User = Depends(require_admin_password_current),
) -> CategoryOut:
    category = DocumentCategory(
        name=payload.name,
        description=payload.description,
        sort_order=payload.sort_order,
        created_by=admin.id,
        updated_by=admin.id,
    )
    db.add(category)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"이미 등록된 분류입니다: {payload.name}",
        ) from None

    audit.record(
        db,
        action=audit.CATEGORY_CREATE,
        actor=admin,
        request=request,
        target_label=category.name,
        after={"name": category.name, "description": category.description},
    )
    db.commit()
    return CategoryOut.model_validate(category)


@categories_router.patch("/{category_id}", response_model=CategoryOut)
def update_category(
    category_id: uuid.UUID,
    payload: CategoryUpdate,
    request: Request,
    db: DbSession = Depends(get_db),
    admin: User = Depends(require_admin_password_current),
) -> CategoryOut:
    category = db.get(DocumentCategory, category_id)
    if category is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="분류를 찾을 수 없습니다."
        )

    before = {
        "name": category.name,
        "description": category.description,
        "is_active": category.is_active,
        "sort_order": category.sort_order,
    }
    data = payload.model_dump(exclude_unset=True)
    if "name" in data and data["name"] is not None:
        name = data["name"].strip()
        if not name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="분류 이름을 입력하세요."
            )
        category.name = name
    if "description" in data:
        category.description = data["description"]
    if data.get("is_active") is not None:
        # Deactivating a category in use would leave those documents pointing at
        # something the UI hides, so block it while documents reference it.
        if data["is_active"] is False:
            in_use = db.scalar(
                select(func.count(Document.id)).where(
                    Document.category_id == category.id,
                    Document.status == STATUS_ACTIVE,
                )
            )
            if in_use:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"이 분류를 사용하는 활성 문서가 {in_use}건 있어 "
                        "비활성화할 수 없습니다."
                    ),
                )
        category.is_active = data["is_active"]
    if data.get("sort_order") is not None:
        category.sort_order = data["sort_order"]
    category.updated_by = admin.id

    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="이미 등록된 분류 이름입니다."
        ) from None

    audit.record(
        db,
        action=audit.CATEGORY_UPDATE,
        actor=admin,
        request=request,
        target_label=category.name,
        before=before,
        after={
            "name": category.name,
            "description": category.description,
            "is_active": category.is_active,
            "sort_order": category.sort_order,
        },
    )
    db.commit()
    return CategoryOut.model_validate(category)
