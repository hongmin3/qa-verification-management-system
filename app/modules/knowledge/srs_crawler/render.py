"""정규화된 SRS 레코드 -> 표준 HTML 문서.

Polarion 웹 화면을 그대로 인쇄하는 대신, 시스템 라벨(Status/Description/Linked Work
Items 등)을 이 템플릿에서 직접 영어로 고정 출력한다. 이렇게 하면 Polarion의
사용자 locale/Export locale 설정과 무관하게 라벨 언어가 항상 일관되고, 반대로
SRS 본문(Rich Text) 내용은 원본 그대로(한국어/영어 혼재 포함) 보존한다.
"""
from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any

from .partition import FileGroup

_OLD_ID_NUM_RE = re.compile(r"\d+")


def _natural_key(old_id: str | None) -> tuple[int, ...]:
    if not old_id:
        return (999999,)
    nums = [int(n) for n in _OLD_ID_NUM_RE.findall(old_id)]
    return tuple(nums) if nums else (999999,)


def sort_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(records, key=lambda r: _natural_key(r.get("old_id")))


def _heading_level(old_id: str | None) -> int:
    if not old_id:
        return 3
    dashes = old_id.count("-")
    return min(dashes + 1, 3)


_CSS = """
body { font-family: 'Segoe UI', 'Malgun Gothic', sans-serif; font-size: 10.5pt; color: #111; }
.cover { text-align: center; padding-top: 120px; page-break-after: always; }
.cover h1 { font-size: 22pt; margin-bottom: 8px; }
.cover .meta { color: #555; font-size: 10pt; margin-top: 24px; }
h1.module { font-size: 16pt; border-bottom: 2px solid #333; margin-top: 36px; page-break-before: always; }
h2.category { font-size: 13pt; border-bottom: 1px solid #999; margin-top: 24px; }
h3.leaf { font-size: 11.5pt; color: #003366; margin-top: 18px; }
.srs-block { page-break-inside: avoid; margin-bottom: 14px; }
.srs-meta { font-size: 8.5pt; color: #555; margin-bottom: 6px; }
.srs-meta span { margin-right: 14px; }
.srs-body { margin-top: 6px; }
.srs-body img { max-width: 100%; }
.srs-body table { border-collapse: collapse; max-width: 100%; }
.srs-section-title { font-weight: bold; font-size: 9.5pt; color: #444; margin-top: 8px; }
ul.linked-items, ul.attachments-list, ul.comments-list { margin: 4px 0 4px 18px; padding: 0; font-size: 9.5pt; }
.badge { display:inline-block; padding:1px 6px; border-radius:3px; font-size:8.5pt; background:#eee; }
.badge.status-draft { background:#fff3cd; }
a.srs-ref-link { color: #3333CC; text-decoration: none; }
a.srs-ref-link:hover { text-decoration: underline; }
.broken-ref { color:#CC0000; }
"""


def _esc(v: Any) -> str:
    return html.escape(str(v)) if v is not None else ""


def _render_meta(rec: dict[str, Any]) -> str:
    parts = [
        f'<span><b>ID:</b> {_esc(rec["id"])}</span>',
        f'<span><b>Status:</b> <span class="badge status-{_esc(rec.get("status"))}">{_esc(rec.get("status"))}</span></span>',
    ]
    if rec.get("old_id"):
        parts.append(f'<span><b>Legacy No.:</b> {_esc(rec["old_id"])}</span>')
    if rec.get("updated"):
        parts.append(f'<span><b>Updated:</b> {_esc(rec["updated"])}</span>')
    if rec.get("jira_id"):
        parts.append(f'<span><b>Jira:</b> {_esc(rec["jira_id"])}</span>')
    if rec.get("content_field_used"):
        parts.append(f'<span><b>Source Field:</b> {_esc(rec["content_field_used"])}</span>')
    return '<div class="srs-meta">' + " ".join(parts) + "</div>"


def _render_linked(rec: dict[str, Any]) -> str:
    linked = [l for l in rec.get("linked_work_items", []) if l.get("role") != "parent"]
    parent = rec.get("parent_id")
    out = []
    if parent:
        out.append(f"<li>parent: {_esc(parent)}</li>")
    for l in linked:
        out.append(f"<li>{_esc(l.get('role'))}: {_esc(l.get('target_project'))}/{_esc(l.get('target_id'))}</li>")
    if not out:
        return ""
    return (
        '<div class="srs-section-title">Linked Work Items</div>'
        f'<ul class="linked-items">{"".join(out)}</ul>'
    )


def _render_attachments(rec: dict[str, Any]) -> str:
    atts = rec.get("attachments_meta", [])
    if not atts:
        return ""
    items = "".join(f"<li>{_esc(a['filename'])}</li>" for a in atts)
    return f'<div class="srs-section-title">Attachments</div><ul class="attachments-list">{items}</ul>'


def _render_comments(rec: dict[str, Any]) -> str:
    comments = rec.get("comments", [])
    if not comments:
        return ""
    items = []
    for c in comments:
        author = _esc(c.get("author"))
        created = _esc(c.get("created"))
        text = _esc(c.get("text"))
        items.append(f"<li><b>{author}</b> ({created}): {text}</li>")
    return f'<div class="srs-section-title">Comments</div><ul class="comments-list">{"".join(items)}</ul>'


def _render_record(rec: dict[str, Any]) -> str:
    level = _heading_level(rec.get("old_id"))
    tag = f"h{level}"
    css_class = "module" if level == 1 else ("category" if rec.get("is_category") else "leaf")
    heading = f'<{tag} class="{css_class}" id="srs-{_esc(rec["id"])}">{_esc(rec.get("old_id") or "")} {_esc(rec["title"])}</{tag}>'
    body = rec.get("content_html") or "<p><i>(본문 없음 / No content)</i></p>"
    return (
        f'<div class="srs-block">{heading}{_render_meta(rec)}'
        f'<div class="srs-body">{body}</div>'
        f"{_render_linked(rec)}{_render_attachments(rec)}{_render_comments(rec)}</div>"
    )


def render_group_html(group: FileGroup, *, project_label: str, generated_at: datetime | None = None) -> str:
    generated_at = generated_at or datetime.now()
    records = sort_records(group.records)
    body_html = "".join(_render_record(r) for r in records)
    cover = (
        '<div class="cover">'
        f"<h1>{_esc(group.display_name)}</h1>"
        f'<div class="meta">Project: {_esc(project_label)}<br/>'
        f"Generated: {generated_at.strftime('%Y-%m-%d %H:%M')}<br/>"
        f"Total SRS: {len(records)}</div></div>"
    )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{_esc(group.display_name)}</title><style>{_CSS}</style></head>"
        f"<body>{cover}{body_html}</body></html>"
    )
