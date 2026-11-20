"""Polarion에서 SRS Work Item을 수집해 정규화된 레코드로 변환한다."""
from __future__ import annotations

import logging
from html import escape
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .config import Config, ProjectSpec
from .polarion_client import PolarionApiError, PolarionClient
from .richtext import normalize_rich_text
from .util import module_key_from_old_id

logger = logging.getLogger("srs_automation")


def _parse_linked_id(linked_id: str) -> dict[str, str] | None:
    """'VXvue/VP-466/parent/VXvue/VP-458' -> {role, target_project, target_id}"""
    parts = linked_id.split("/")
    if len(parts) != 5:
        return None
    _from_proj, _from_id, role, to_proj, to_id = parts
    return {"role": role, "target_project": to_proj, "target_id": to_id}


def _attachment_filename(attachment_id: str) -> str:
    return attachment_id.split("/")[-1]


@dataclass
class CollectStats:
    project_id: str
    total_items: int = 0
    expected_total: int = 0
    categories: int = 0
    leaves: int = 0
    images_ok: int = 0
    images_failed: int = 0
    links_unresolved: int = 0
    comments_fetched: int = 0
    errors: list[str] = field(default_factory=list)


def fetch_raw_items(client: PolarionClient, project: ProjectSpec, stats: CollectStats) -> list[dict[str, Any]]:
    logger.info("[%s] Work Item 조회 시작 (query=%s)", project.id, project.query)
    stats.expected_total = client.get_total_count(project.id, project.query)
    logger.info("[%s] Polarion 응답 기준 예상 건수: %d", project.id, stats.expected_total)
    scoped = project.document_name or project.target_version
    if scoped:
        items = list(client.iter_workitems(project.id, project.query, fields='@all,description,Description,targetVersion'))
    else:
        items = list(client.iter_workitems(project.id, project.query))
    logger.info("[%s] Work Item %d건 조회 완료", project.id, len(items))
    if len(items) != stats.expected_total:
        msg = f"[{project.id}] 수집 건수 불일치: 예상 {stats.expected_total}건, 실제 수집 {len(items)}건"
        logger.error(msg)
        stats.errors.append(msg)
    if project.document_name:
        ids, source = client.document_scope(project.id, project.document_space, project.document_name)
        items = [item for item in items if item['id'] in ids]
        if {item['id'] for item in items} != ids:
            stats.errors.append('LiveDoc 작업 항목과 수집한 SRS 목록이 일치하지 않습니다.')
        if project.target_version:
            expected = f'{project.id}/{project.target_version}'
            items = [item for item in items if target_version(item) == expected]
            stats.expected_total = client.get_total_count(
                project.id, f'({project.query}) AND targetVersion.KEY:{project.target_version}')
        else:
            stats.expected_total = len(ids)
        for item in items:
            item['_document_source'] = source
    if project.target_version:
        expected = f'{project.id}/{project.target_version}'
        if any(target_version(item) != expected for item in items):
            stats.errors.append('Polarion 응답의 목표 버전이 요청한 Plan과 일치하지 않습니다.')
    return items


def target_version(item):
    return (((item.get('relationships') or {}).get('targetVersion') or {}).get('data') or {}).get('id')


def _build_base_record(project_id: str, raw: dict[str, Any]) -> dict[str, Any]:
    attrs = raw.get("attributes", {})
    rels = raw.get("relationships", {})

    linked: list[dict[str, str]] = []
    for entry in rels.get("linkedWorkItems", {}).get("data", []) or []:
        parsed = _parse_linked_id(entry.get("id", ""))
        if parsed:
            linked.append(parsed)
    parent = next((l["target_id"] for l in linked if l["role"] == "parent"), None)

    attachments = [
        {"attachment_id": a["id"], "filename": _attachment_filename(a["id"])}
        for a in (rels.get("attachments", {}).get("data", []) or [])
    ]

    author = rels.get("author", {}).get("data", {}).get("id")

    def _rich(field_name: str) -> str | None:
        val = attrs.get(field_name)
        if isinstance(val, dict):
            return val.get("value")
        return None

    old_id = attrs.get("oldId")
    return {
        "project_id": project_id,
        "id": attrs.get("id"),
        "uid": f"{project_id}/{attrs.get('id')}",
        "type": attrs.get("type"),
        "title": attrs.get("title"),
        "old_id": old_id,
        "module_key": module_key_from_old_id(old_id),
        "is_category": str(attrs.get("isCategory", "")).lower() == "true",
        "status": attrs.get("status"),
        "severity": attrs.get("severity"),
        "priority": attrs.get("priority"),
        "active": attrs.get("active"),
        "created": attrs.get("created"),
        "updated": attrs.get("updated"),
        "jira_id": attrs.get("jiraId"),
        "author_id": author,
        "description_raw": _rich("description"),
        "description_kr_raw": _rich("descriptionKR"),
        "description_custom_raw": '<br/>'.join(escape(line) for line in attrs['Description'].splitlines())
        if isinstance(attrs.get('Description'), str) else None,
        "parent_id": parent,
        "linked_work_items": linked,
        "attachments_meta": attachments,
        "comments": [],
        "content_field_used": None,
        "content_html": "",
        "image_results": [],
        "link_results": [],
    }


def collect_project(
    client: PolarionClient,
    project: ProjectSpec,
    config: Config,
    images_root: Path,
    stats: CollectStats,
) -> list[dict[str, Any]]:
    raw_items = fetch_raw_items(client, project, stats)
    records = [_build_base_record(project.id, r) for r in raw_items]
    if project.document_name or project.target_version:
        for record, raw in zip(records, raw_items):
            record.update(document_source=raw.get('_document_source'), target_version=target_version(raw),
                          source_url=f'{config.host.rstrip("/")}/#/project/{project.id}/workitem?id={quote(record["id"], safe="")}',
                          collection_scope=config.collection_scope)
    id_title_map = {r["id"]: r["title"] for r in records}

    attachments_index: dict[str, dict[str, Any]] = {}

    for rec in records:
        if rec["is_category"]:
            stats.categories += 1
        else:
            stats.leaves += 1

        if project.fetch_comments:
            try:
                raw_comments = client.get_comments(project.id, rec["id"])
                rec["comments"] = [
                    {
                        "id": c.get("id"),
                        "text": (c.get("attributes", {}).get("text") or {}).get("value")
                        if isinstance(c.get("attributes", {}).get("text"), dict)
                        else c.get("attributes", {}).get("text"),
                        "author": c.get("relationships", {}).get("author", {}).get("data", {}).get("id"),
                        "created": c.get("attributes", {}).get("created"),
                    }
                    for c in raw_comments
                ]
                stats.comments_fetched += len(rec["comments"])
            except PolarionApiError as exc:
                logger.warning("[%s] %s 댓글 조회 실패: %s", project.id, rec["id"], exc)
                stats.errors.append(f"{rec['id']}: comments fetch failed - {exc}")

        # 본문 필드 우선순위 적용 (예: descriptionKR 우선, 없으면 description)
        chosen_field = None
        chosen_html = None
        for field_name in config.content_field_priority:
            key = {"descriptionKR": "description_kr_raw", "Description": "description_custom_raw"}.get(
                field_name, "description_raw")
            if rec.get(key):
                chosen_field = field_name
                chosen_html = rec[key]
                break
        rec["content_field_used"] = chosen_field

        item_images_dir = images_root / project.id / rec["id"]

        def _resolve_image(filename: str, _rec=rec, _dir=item_images_dir) -> tuple[Path | None, str | None]:
            if filename not in attachments_index.get(_rec["id"], {}):
                try:
                    atts = client.get_attachments(project.id, _rec["id"])
                except PolarionApiError as exc:
                    return None, str(exc)
                attachments_index[_rec["id"]] = {
                    _attachment_filename(a["id"]): a.get("links", {}).get("content") for a in atts
                }
            content_url = attachments_index.get(_rec["id"], {}).get(filename)
            if not content_url:
                return None, "첨부파일 목록에서 이미지 참조를 찾을 수 없음"
            dest = _dir / filename
            if dest.exists():
                return dest, None
            try:
                client.download_attachment(content_url, dest)
                return dest, None
            except Exception as exc:  # noqa: BLE001 - 다운로드 실패는 실패 목록에 기록하고 계속 진행
                return None, str(exc)

        def _resolve_link_title(item_id: str) -> str | None:
            return id_title_map.get(item_id)

        result = normalize_rich_text(
            chosen_html, resolve_image=_resolve_image, resolve_link_title=_resolve_link_title
        )
        rec["content_html"] = result.html
        rec["image_results"] = result.images
        rec["link_results"] = result.links
        stats.images_ok += sum(1 for i in result.images if i["ok"])
        stats.images_failed += sum(1 for i in result.images if not i["ok"])
        stats.links_unresolved += sum(1 for l in result.links if not l["resolved"])

    stats.total_items = len(records)
    return records
