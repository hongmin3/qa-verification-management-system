"""Request / response models.

Nothing here ever carries ``password_hash`` -- the out-models list their fields
explicitly so a future column addition cannot leak by accident.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

# --------------------------------------------------------------------------- #
# shared
# --------------------------------------------------------------------------- #
ORM = ConfigDict(from_attributes=True)


def _blank_to_none(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip()
    return v or None


class Message(BaseModel):
    detail: str


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


# --------------------------------------------------------------------------- #
# auth
# --------------------------------------------------------------------------- #
class LoginRequest(BaseModel):
    login_id: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class UserOut(BaseModel):
    model_config = ORM

    id: uuid.UUID
    login_id: str
    display_name: str
    role: str
    is_active: bool
    must_change_password: bool
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MeOut(UserOut):
    is_admin: bool


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


# --------------------------------------------------------------------------- #
# users (admin)
# --------------------------------------------------------------------------- #
class UserCreate(BaseModel):
    login_id: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    display_name: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)
    role: str = Field(default="user", pattern=r"^(admin|user)$")
    must_change_password: bool = True

    @field_validator("display_name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("표시 이름을 입력하세요.")
        return v


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=128)
    role: str | None = Field(default=None, pattern=r"^(admin|user)$")
    is_active: bool | None = None
    must_change_password: bool | None = None


class PasswordResetRequest(BaseModel):
    new_password: str = Field(min_length=1, max_length=256)
    must_change_password: bool = True


# --------------------------------------------------------------------------- #
# product
# --------------------------------------------------------------------------- #
class ProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    code: str | None = Field(default=None, max_length=64)
    description: str | None = None
    sort_order: int = 100

    _n = field_validator("code", "description", mode="before")(_blank_to_none)

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("제품 이름을 입력하세요.")
        return v


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    code: str | None = Field(default=None, max_length=64)
    description: str | None = None
    is_active: bool | None = None
    sort_order: int | None = None


class ProductOut(BaseModel):
    model_config = ORM

    id: uuid.UUID
    name: str
    code: str | None
    description: str | None
    is_active: bool
    sort_order: int
    created_at: datetime
    updated_at: datetime


class ProductWithStats(ProductOut):
    document_count: int = 0
    version_count: int = 0
    created_by_display_name: str | None = None
    last_upload_at: datetime | None = None


# --------------------------------------------------------------------------- #
# category
# --------------------------------------------------------------------------- #
class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None
    sort_order: int = 100

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("분류 이름을 입력하세요.")
        return v


class CategoryUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    description: str | None = None
    is_active: bool | None = None
    sort_order: int | None = None


class CategoryOut(BaseModel):
    model_config = ORM

    id: uuid.UUID
    name: str
    description: str | None
    is_active: bool
    sort_order: int
    created_at: datetime
    updated_at: datetime


class CategoryWithStats(CategoryOut):
    document_count: int = 0


# --------------------------------------------------------------------------- #
# stored file / version
# --------------------------------------------------------------------------- #
class StoredFileOut(BaseModel):
    model_config = ORM

    id: uuid.UUID
    sha256: str
    byte_size: int
    original_file_name: str
    file_extension: str
    mime_type: str | None
    storage_backend: str


class VersionOut(BaseModel):
    model_config = ORM

    id: uuid.UUID
    document_id: uuid.UUID
    revision: str | None
    version: str | None
    document_number: str | None
    language: str | None
    revision_date: date | None
    revision_description: str | None
    comment: str | None
    uploaded_by_user_id: uuid.UUID | None
    uploaded_by_login_id: str
    uploaded_by_display_name: str
    upload_date: datetime
    status: str
    created_at: datetime
    stored_file: StoredFileOut
    is_current: bool = False
    can_preview: bool = False


class VersionUpdate(BaseModel):
    revision: str | None = Field(default=None, max_length=64)
    version: str | None = Field(default=None, max_length=64)
    document_number: str | None = Field(default=None, max_length=128)
    language: str | None = Field(default=None, max_length=32)
    revision_date: date | None = None
    revision_description: str | None = None
    comment: str | None = None


class DuplicateFileInfo(BaseModel):
    sha256: str
    product_name: str | None
    document_name: str | None
    version_label: str | None
    original_file_name: str | None
    upload_date: datetime | None
    uploaded_by_display_name: str | None


class VersionUploadResult(BaseModel):
    version: VersionOut
    became_current: bool
    duplicate_of: list[DuplicateFileInfo] = []
    warning: str | None = None


# --------------------------------------------------------------------------- #
# document
# --------------------------------------------------------------------------- #
class DocumentCreate(BaseModel):
    product_id: uuid.UUID
    category_id: uuid.UUID
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("문서 이름을 입력하세요.")
        return v


class DocumentUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    description: str | None = None
    category_id: uuid.UUID | None = None


class DocumentOut(BaseModel):
    model_config = ORM

    id: uuid.UUID
    product_id: uuid.UUID
    category_id: uuid.UUID
    name: str
    description: str | None
    status: str
    created_at: datetime
    updated_at: datetime


class DocumentRow(DocumentOut):
    """Row shape for the product detail table (spec section 17)."""

    product_name: str
    category_name: str
    # The curated ordering from DocumentCategory.sort_order, so the client can
    # reproduce the intended sequence (Operation, Service, QC, ...) instead of
    # falling back to alphabetical.
    category_sort_order: int = 100
    current_version_id: uuid.UUID | None = None
    current_revision: str | None = None
    current_version_label: str | None = None
    current_document_number: str | None = None
    current_language: str | None = None
    revision_date: date | None = None
    uploaded_by_display_name: str | None = None
    upload_date: datetime | None = None
    version_count: int = 0
    created_by_display_name: str | None = None
    updated_by_display_name: str | None = None


class DocumentDetail(DocumentRow):
    versions: list[VersionOut] = []


class SetCurrentRequest(BaseModel):
    version_id: uuid.UUID


# --------------------------------------------------------------------------- #
# search
# --------------------------------------------------------------------------- #
class SearchHit(BaseModel):
    snippet: str | None = None
    body_status: str = "pending"
    body_detail: str | None = None
    document_id: uuid.UUID
    document_name: str
    product_id: uuid.UUID
    product_name: str
    category_name: str
    document_status: str
    version_id: uuid.UUID | None
    revision: str | None
    version: str | None
    document_number: str | None
    language: str | None
    revision_date: date | None
    upload_date: datetime | None
    uploaded_by_display_name: str | None
    original_file_name: str | None
    version_status: str | None
    is_current: bool


# --------------------------------------------------------------------------- #
# dashboard
# --------------------------------------------------------------------------- #
class DashboardCounts(BaseModel):
    products: int
    products_active: int
    documents: int
    documents_active: int
    documents_archived: int
    documents_with_current: int
    versions: int
    users_active: int
    storage_bytes: int


class RecentUpload(BaseModel):
    version_id: uuid.UUID
    document_id: uuid.UUID
    document_name: str
    product_id: uuid.UUID
    product_name: str
    version_label: str | None
    revision: str | None
    version: str | None
    uploaded_by_display_name: str
    upload_date: datetime
    is_current: bool


class ActivityEntry(BaseModel):
    id: int
    created_at: datetime
    action: str
    action_label: str
    actor_display_name: str | None
    actor_login_id: str | None
    product_name: str | None
    document_name: str | None
    version_label: str | None
    target_label: str | None
    detail: str | None


class DashboardOut(BaseModel):
    counts: DashboardCounts
    recent_uploads: list[RecentUpload]
    recent_current_changes: list[ActivityEntry]
    recent_documents: list[DocumentRow]
    recent_activity: list[ActivityEntry]


# --------------------------------------------------------------------------- #
# audit
# --------------------------------------------------------------------------- #
class AuditOut(BaseModel):
    model_config = ORM

    id: int
    created_at: datetime
    action: str
    action_label: str = ""
    actor_login_id: str | None
    actor_display_name: str | None
    product_name: str | None
    document_name: str | None
    version_label: str | None
    target_label: str | None
    ip_address: str | None
    before_value: dict | None
    after_value: dict | None
    detail: str | None


# --------------------------------------------------------------------------- #
# settings
# --------------------------------------------------------------------------- #
class SettingsOut(BaseModel):
    max_upload_mb: int
    allowed_extensions: list[str]
    session_lifetime_hours: int
    password_min_length: int
    storage_root: str
    storage_backend: str
    app_version: str
