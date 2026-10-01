"""제품별 Knowledge 상태 판정 (SPEC REQ-KNOW-019).

현황판(`/knowledge`)과 제품 상세(`/knowledge/products/<slug>`)가 이 결과만 읽어 그린다. 제품마다 판정
함수를 따로 두지 않는다 — 출처 프로필(REQ-KNOW-018)과 문서 표·수집 기록·동기화 기록만 본다.

원본 파일은 다시 파싱하지 않는다. 등록 때 적은 부가 정보, 수집 기록, 파일이 있는지만 본다.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.product_config import (
    ASSET_KINDS,
    AUTOMATIC_SOURCES,
    KIND_LABELS,
    SOURCE_ALM,
    SOURCE_EXTERNAL,
    SOURCE_FOLDER,
    SOURCE_LABELS,
    SOURCE_MANUAL,
    SOURCE_UNKNOWN,
    ProductConfig,
    can_manual_upload,
    list_product_configs,
)
from app.core.product_knowledge import REGISTERABLE_KINDS, collected_path, document_logical_key, load_manifest, source_available

READY, ATTENTION, ERROR = "READY", "ATTENTION", "ERROR"
LEVEL_LABELS = {READY: "정상", ATTENTION: "주의", ERROR: "오류"}
_LEVEL_ORDER = {READY: 0, ATTENTION: 1, ERROR: 2}

#: 지식 폴더 수집·업로드 확정이 남기는 동기화 기록 종류 (REQ-KNOW-016).
FOLDER_SYNC_KIND = "product_knowledge"

MANAGED_LABELS = {
    SOURCE_ALM: "자동 관리",
    SOURCE_EXTERNAL: "자동 관리",
    SOURCE_FOLDER: "수동 관리(담당자가 폴더에 넣음)",
    SOURCE_MANUAL: "수동 관리(이 화면에서 등록·교체)",
    SOURCE_UNKNOWN: "확인 필요",
}
SOURCE_MESSAGES = {
    SOURCE_ALM: "ALM 에서 자동으로 수집합니다. 여기서 올릴 수 없습니다.",
    SOURCE_EXTERNAL: "다른 자동화가 보내는 자료입니다.",
    SOURCE_FOLDER: "담당자 PC 의 지식 폴더에서 수집합니다.",
    SOURCE_MANUAL: "이 화면에서 새 문서를 등록하거나 현재 문서를 교체합니다.",
    SOURCE_UNKNOWN: "제품 설정의 출처 값을 알 수 없습니다. 설정 파일을 확인하세요.",
}
#: 등록 부가 정보의 출처 → 문서 행의 출처 이름 (REQ-KNOW-020).
DOCUMENT_SOURCE_LABELS = {"product_knowledge": "지식 폴더", SOURCE_ALM: "ALM 자동", SOURCE_EXTERNAL: "외부 자동화"}
UPLOAD_ACCEPT = {"specification": ".pdf,.docx", "manual": ".pdf,.docx", "testcase": ".xlsx"}


@dataclass
class DocumentRow:
    name: str
    kind: str
    kind_label: str
    source_label: str
    status: str            # 정상 | 파일 없음 | 읽지 못함
    updated_at: str
    id: int | None = None  # 문서 표 번호. 규칙 자산(수집 기록에만 있음)은 없다.
    usable: bool = True


@dataclass
class AssetStatus:
    kind: str
    label: str
    source: str
    source_label: str
    managed: str
    message: str
    required: bool
    count: int
    status: str
    level: str
    reason: str
    last_updated: str = ""
    last_sync: dict | None = None
    can_upload: bool = False
    accept: str = ""
    documents: list[DocumentRow] = field(default_factory=list)


@dataclass
class ProductHealth:
    product: str
    slug: str
    level: str
    label: str
    last_updated: str
    assets: list[AssetStatus]
    excluded: list[dict] = field(default_factory=list)
    advanced: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _latest(values: list[str]) -> str:
    dated = [(stamp, value) for value in values if (stamp := _parse_time(value))]
    return max(dated)[1] if dated else ""


def _newest_sync(records: list[dict | None]) -> dict | None:
    """여러 동기화 기록 가운데 가장 최근 하나. 종류가 여럿이면 이 하나로 판단한다 (REQ-KNOW-019)."""
    dated = [(stamp, index, record) for index, record in enumerate(records)
             if record and (stamp := _parse_time(record.get("synced_at", "")))]
    return max(dated)[2] if dated else None


def _metadata(document: dict) -> dict:
    try:
        return json.loads(document.get("metadata_json") or "{}")
    except ValueError:
        return {}


class _Context:
    """한 제품의 판정 재료. 문서·수집 기록·동기화 기록을 모두 그 제품 이름으로 거른다 (REQ-KNOW-019)."""

    def __init__(self, config: ProductConfig, storage, root: Path | None):
        self.config, self.storage, self.root = config, storage, root
        self.product = config.product
        self.manifest = load_manifest(self.product, root)
        self.manifest_assets = list(self.manifest.get("assets") or [])
        self.manifest_by_name = {(asset.get("kind"), asset.get("file_name")): asset for asset in self.manifest_assets}
        self.folder_sync = storage.latest_finished_sync(self.product, FOLDER_SYNC_KIND)
        self.folder_success = storage.latest_successful_sync(self.product, FOLDER_SYNC_KIND)

    def in_manifest(self, kind: str) -> bool:
        return any(asset.get("kind") == kind for asset in self.manifest_assets)

    def related_syncs(self, kind: str, source: str) -> tuple[list[dict | None], list[dict | None]]:
        """(끝난 기록, 성공 기록) — REQ-KNOW-018 표의 마지막 열."""
        finished: list[dict | None] = []
        success: list[dict | None] = []
        if source in AUTOMATIC_SOURCES:
            finished.append(self.storage.latest_finished_sync(self.product, kind))
            success.append(self.storage.latest_successful_sync(self.product, kind))
        if source == SOURCE_FOLDER or self.in_manifest(kind):
            finished.append(self.folder_sync)
            success.append(self.folder_success)
        return finished, success


def _registered_rows(ctx: _Context, kind: str) -> tuple[list[DocumentRow], list[dict]]:
    rows, documents = [], ctx.storage.active_documents(kind, ctx.product)
    for document in documents:
        metadata = _metadata(document)
        source = str(metadata.get("source") or "")
        exists = Path(document["path"]).is_file()
        usable, status = exists, "정상" if exists else "파일 없음"
        if exists and source == "product_knowledge":
            asset = ctx.manifest_by_name.get((kind, document["name"]))
            if asset and asset.get("error"):
                usable, status = False, "읽지 못함"
        elif exists and kind != "testcase" and "chunk_count" in metadata and not metadata.get("chunk_count"):
            usable, status = False, "읽지 못함"
        updated = document.get("created_at", "")
        if source == "product_knowledge":
            asset = ctx.manifest_by_name.get((kind, document["name"])) or {}
            updated = asset.get("collected_at") or updated
        rows.append(DocumentRow(
            name=document["name"], kind=kind, kind_label=KIND_LABELS[kind],
            source_label=DOCUMENT_SOURCE_LABELS.get(source, SOURCE_LABELS[SOURCE_MANUAL]),
            status=status, updated_at=updated, id=document["id"], usable=usable,
        ))
    return rows, documents


def _rule_rows(ctx: _Context, kind: str) -> list[DocumentRow]:
    rows = []
    for asset in ctx.manifest_assets:
        if asset.get("kind") != kind or asset.get("kept_because"):
            continue
        exists = collected_path(ctx.product, asset, ctx.root).is_file()
        usable = exists and not asset.get("error")
        rows.append(DocumentRow(
            name=asset.get("file_name", ""), kind=kind, kind_label=KIND_LABELS[kind], source_label="지식 폴더",
            status="정상" if usable else ("파일 없음" if not exists else "읽지 못함"),
            updated_at=asset.get("collected_at", ""), usable=usable,
        ))
    return rows


def _judge(ctx: _Context, kind: str) -> AssetStatus:
    config, source = ctx.config, ctx.config.source_of(kind)
    required = config.is_required(kind)
    if kind in REGISTERABLE_KINDS:
        rows, documents = _registered_rows(ctx, kind)
    else:
        rows, documents = _rule_rows(ctx, kind), []
    finished, success = ctx.related_syncs(kind, source)
    last_sync = _newest_sync(finished)
    last_success = _newest_sync(success)
    usable = [row for row in rows if row.usable]
    kept = [asset for asset in ctx.manifest_assets if asset.get("kind") == kind and asset.get("kept_because")]

    def verdict() -> tuple[str, str, str]:
        if not rows and last_sync and last_sync.get("status") == "FAILED":
            return "수집 실패", ERROR, f"마지막 수집이 실패했고 등록된 자료가 없습니다: {last_sync.get('detail') or '이유 없음'}"
        if not rows and required:
            return "자료 없음", ATTENTION, "꼭 있어야 하는 자료가 등록되지 않았습니다."
        if not rows:
            return "없음", READY, "없어도 되는 자료입니다."
        if last_sync and last_sync.get("status") == "FAILED":
            return "수집 실패", ERROR, f"마지막 수집이 실패했습니다: {last_sync.get('detail') or '이유 없음'}"
        if not usable:
            return "파싱 실패", ERROR if required else ATTENTION, "등록된 자료를 모두 쓸 수 없습니다(파일 없음 또는 읽지 못함)."
        if len(usable) < len(rows) or kept:
            note = kept[0]["kept_because"] if kept else f"{len(rows) - len(usable)}개를 쓸 수 없습니다."
            return "파싱 실패", ATTENTION, f"일부 자료를 쓸 수 없습니다: {note}"
        if source in AUTOMATIC_SOURCES:
            auto = ctx.storage.latest_finished_sync(ctx.product, kind)
            if auto and auto.get("status") == "PARTIAL":
                return "수집 실패(일부)", ATTENTION, f"마지막 자동 수집이 일부만 끝났습니다: {auto.get('detail') or ''}".rstrip(": ")
        keys = [document_logical_key(kind, document["name"], _metadata(document)) for document in documents]
        if len(keys) != len(set(keys)):
            return "중복 확인 요청", ATTENTION, "같은 문서의 판이 두 개 이상 등록돼 있습니다. 하나만 남기세요."
        if source == SOURCE_UNKNOWN:
            return "출처 확인 요청", ATTENTION, "제품 설정의 출처 값을 알 수 없습니다."
        # 사람이 등록하는 자료는 지식 폴더 기록이 없으면 언제 바꿔야 하는지 알 수 없어 보지 않는다.
        if not (source == SOURCE_MANUAL and not ctx.in_manifest(kind)):
            if last_success is None:
                if source in AUTOMATIC_SOURCES:
                    return "업데이트 필요", ATTENTION, "자동 수집에 성공한 기록이 없습니다."
            else:
                stamp = _parse_time(last_success.get("synced_at", ""))
                limit = timedelta(days=config.sync.stale_after_days)
                if stamp and datetime.now(timezone.utc) - stamp > limit:
                    return "업데이트 필요", ATTENTION, f"마지막 성공 수집이 {config.sync.stale_after_days}일보다 오래됐습니다."
        return "정상", READY, ""

    status, level, reason = verdict()
    times = [row.updated_at for row in rows] + [last_success.get("synced_at", "") if last_success else ""]
    return AssetStatus(
        kind=kind, label=KIND_LABELS[kind], source=source, source_label=SOURCE_LABELS[source],
        managed=MANAGED_LABELS[source], message=SOURCE_MESSAGES[source], required=required,
        count=len(rows), status=status, level=level, reason=reason, last_updated=_latest(times),
        last_sync=last_sync, can_upload=can_manual_upload(config, kind), accept=UPLOAD_ACCEPT.get(kind, ""),
        documents=rows,
    )


def product_health(config: ProductConfig, storage, root: Path | None = None) -> ProductHealth:
    ctx = _Context(config, storage, root)
    assets = [_judge(ctx, kind) for kind in ASSET_KINDS]
    level = max((asset.level for asset in assets), key=_LEVEL_ORDER.__getitem__, default=READY)
    from app.core.qa_rules import load_rule_set

    rule_set = load_rule_set(config.product)
    return ProductHealth(
        product=config.product, slug=config.slug, level=level, label=LEVEL_LABELS[level],
        last_updated=_latest([asset.last_updated for asset in assets]),
        assets=assets,
        excluded=[{"file_name": item.get("file_name", ""), "kind": KIND_LABELS.get(item.get("kind", ""), item.get("kind", "")),
                   "reason": item.get("exclude_reason", ""), "superseded_by": item.get("superseded_by", "")}
                  for item in ctx.manifest.get("excluded") or []],
        advanced={
            "source_dir": config.knowledge_source.dir,
            "folder_accessible": source_available(config),
            "uploaded_from": ctx.manifest.get("uploaded_from", ""),
            "synced_at": ctx.manifest.get("synced_at", ""),
            "last_sync_detail": (ctx.folder_sync or {}).get("detail", ""),
            "rule_revision": rule_set.revision if rule_set.available else "",
        },
    )


def overview(storage, root: Path | None = None) -> dict:
    """현황판 재료: 설정된 제품 카드 목록과, 문서는 있지만 설정 파일이 없는 제품 (REQ-KNOW-001)."""
    configs = list_product_configs(root)
    products = [product_health(config, storage, root) for config in configs]
    configured = {config.product.casefold() for config in configs}
    unconfigured = []
    for name in storage.list_products():
        if name.casefold() in configured:
            continue
        count = sum(len(storage.active_documents(kind, name)) for kind in REGISTERABLE_KINDS)
        if count:
            unconfigured.append({"name": name, "documents": count})
    return {"products": products, "unconfigured": unconfigured}
