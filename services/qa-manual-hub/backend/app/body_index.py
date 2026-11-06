"""Derived file-body index, owned entirely by Manual Hub's DB/storage."""
from datetime import UTC, datetime
import logging
import uuid

from sqlalchemy import select

from .body_extract import INDEX_VERSION, extract_file, result
from .models import StoredFile
from .storage import get_storage, StorageError


def index_stored_file(db, stored, force=False):
    completed = ('ready','truncated','no_text','encrypted','unsupported','too_large')
    if not force and stored.body_sha256 == stored.sha256 and stored.body_index_version == INDEX_VERSION and stored.body_status in completed:
        return stored.body_status
    # Identical bytes may reuse derived text; each upload still owns its original.
    cached = db.scalar(select(StoredFile).where(
        StoredFile.id != stored.id,
        StoredFile.file_extension == stored.file_extension,
        StoredFile.body_sha256 == stored.sha256,
        StoredFile.body_index_version == INDEX_VERSION,
        StoredFile.body_status.in_(('ready','truncated','no_text','encrypted','unsupported')),
    ).limit(1)) if not force else None
    if cached:
        value = result(cached.body_status, cached.body_text or '', cached.body_detail)
    else:
        try:
            path = get_storage().path(stored.storage_key)
            value = extract_file(path, stored.file_extension, stored.sha256) if path else result('unsupported', detail='현재 저장소는 로컬 본문 추출을 지원하지 않습니다.')
        except (OSError, ValueError, StorageError):
            value = result('failed', detail='저장된 파일을 읽을 수 없습니다.')
    stored.body_text = value['text'] or None
    stored.body_status = value['status']
    stored.body_detail = value['detail']
    stored.body_sha256 = stored.sha256
    stored.body_index_version = INDEX_VERSION
    stored.body_indexed_at = datetime.now(UTC)
    db.flush()
    return stored.body_status


def index_uploaded_file(file_id):
    # Called only after successful upload commit. A failed parser is recorded,
    # never turned into an upload failure or allowed to rewrite its original.
    from .db import SessionLocal
    try:
        with SessionLocal() as db:
            stored = db.get(StoredFile, uuid.UUID(str(file_id)))
            if stored is not None:
                index_stored_file(db, stored)
                db.commit()
    except Exception:
        # Original upload is already committed. Leave the index pending so an
        # explicit reindex can recover after a transient DB/storage failure.
        logging.getLogger('qa_manual_hub').warning('파일 %s의 본문 색인 작업을 완료하지 못했습니다.', file_id)
