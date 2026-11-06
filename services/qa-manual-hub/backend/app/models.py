"""Database entities for QA Manual Hub.

Design notes
------------
* ``Document.current_version_id`` is the single source of truth for "which
  revision is current".  Because it is one column on one row, two concurrent
  uploads can never leave two versions marked current -- the row is locked with
  ``SELECT ... FOR UPDATE`` before it is changed.
* ``DocumentVersion.uploaded_by_display_name`` is a *snapshot*.  If the user
  later renames themselves, historical uploader attribution stays intact.
* ``role`` is a plain string, not a PG enum, so future roles (viewer / editor /
  manager) can be added without a migration that rewrites the type.
* ``StoredFile`` isolates physical storage behind ``storage_backend`` +
  ``storage_key`` so the local-disk backend can later be swapped for NAS / S3 /
  MinIO without touching business logic.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

# --------------------------------------------------------------------------- #
# constants
# --------------------------------------------------------------------------- #
ROLE_ADMIN = "admin"
ROLE_USER = "user"
ROLES = (ROLE_ADMIN, ROLE_USER)

STATUS_ACTIVE = "active"
STATUS_ARCHIVED = "archived"
STATUSES = (STATUS_ACTIVE, STATUS_ARCHIVED)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# --------------------------------------------------------------------------- #
# User / session
# --------------------------------------------------------------------------- #
class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    login_id: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'user'")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # login_id is compared case-insensitively everywhere, so the uniqueness
    # guarantee has to be case-insensitive too.
    __table_args__ = (
        Index("uq_users_login_id_lower", func.lower(login_id), unique=True),
    )

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN


class Session(Base):
    """Server-side session.

    The cookie carries an opaque random token; only its SHA-256 is stored, so a
    database leak does not hand out live sessions.
    """

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)

    user: Mapped[User] = relationship(lazy="joined")


class LoginHistory(Base):
    """Kept separate from AuditLog so security review of sign-in attempts does
    not have to wade through document activity."""

    __tablename__ = "login_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    login_id_attempted: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)


# --------------------------------------------------------------------------- #
# Product / category
# --------------------------------------------------------------------------- #
class Product(TimestampMixin, Base):
    __tablename__ = "products"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("100")
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        Index("uq_products_name_lower", func.lower(name), unique=True),
        Index("uq_products_code_lower", func.lower(code), unique=True),
    )


class DocumentCategory(TimestampMixin, Base):
    __tablename__ = "document_categories"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("100")
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        Index("uq_document_categories_name_lower", func.lower(name), unique=True),
    )


# --------------------------------------------------------------------------- #
# Stored file
# --------------------------------------------------------------------------- #
class StoredFile(TimestampMixin, Base):
    __tablename__ = "stored_files"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    original_file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    file_extension: Mapped[str] = mapped_column(String(32), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    storage_backend: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'local'")
    )
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Derived text only; original file identity/storage and revision ownership stay unchanged.
    body_status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'pending'"))
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_detail: Mapped[str | None] = mapped_column(String(512), nullable=True)
    body_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    body_index_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    body_indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# --------------------------------------------------------------------------- #
# Document / version
# --------------------------------------------------------------------------- #
class Document(TimestampMixin, Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False,
    )
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_categories.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "document_versions.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_documents_current_version",
        ),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'active'")
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    archived_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    product: Mapped[Product] = relationship(lazy="joined")
    category: Mapped[DocumentCategory] = relationship(lazy="joined")
    current_version: Mapped["DocumentVersion | None"] = relationship(
        lazy="joined", foreign_keys=[current_version_id], post_update=True
    )

    __table_args__ = (
        Index(
            "uq_documents_product_name_lower",
            "product_id",
            func.lower(name),
            unique=True,
        ),
        Index("ix_documents_product_status", "product_id", "status"),
    )


class DocumentVersion(Base):
    __tablename__ = "document_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    revision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    document_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    revision_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    revision_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    uploaded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    uploaded_by_login_id: Mapped[str] = mapped_column(String(64), nullable=False)
    uploaded_by_display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    upload_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    stored_file_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("stored_files.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'active'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    archived_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    document: Mapped[Document] = relationship(
        lazy="joined", foreign_keys=[document_id]
    )
    stored_file: Mapped[StoredFile] = relationship(lazy="joined")

    __table_args__ = (
        Index("ix_document_versions_document_upload", "document_id", "upload_date"),
        Index("ix_document_versions_revision", "revision"),
        Index("ix_document_versions_version", "version"),
        Index("ix_document_versions_document_number", "document_number"),
    )

    @property
    def label(self) -> str:
        """Human-facing identifier used in tables and audit entries."""
        for candidate in (self.version, self.revision):
            if candidate:
                return candidate
        return str(self.id)[:8]


# --------------------------------------------------------------------------- #
# Audit / settings
# --------------------------------------------------------------------------- #
class AuditLog(Base):
    """Append-only.  No API exposes update or delete for this table."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_login_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)

    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    product_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    document_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    version_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    target_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    target_label: Mapped[str | None] = mapped_column(String(255), nullable=True)

    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    before_value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    after_value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
