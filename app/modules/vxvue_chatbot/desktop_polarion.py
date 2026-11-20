"""On-demand Polarion GETs. Preserve every API field and expose bounded pages."""
import base64
from datetime import datetime, timezone
import json
from urllib.parse import quote
from pathlib import Path
import re
import time
import uuid
from html.parser import HTMLParser
from html import escape

from app.parsers.polarion_issue import html_to_text
from app.core.security_filter import mask_payload


class _DisplayHTML(HTMLParser):
    """Drop layout-only attributes, keeping text, links, tables and revision semantics."""
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        keep = []
        for key, value in attrs:
            if key in ('class', 'style'):
                if value and re.search(r'line-through|strike|deleted|inserted|underline', value, re.I):
                    keep.append((key, value))
            elif key not in ('width', 'height', 'align', 'valign', 'face', 'size', 'color'):
                keep.append((key, value))
        self.parts.append('<' + tag + ''.join(' ' + k + ('="' + escape(v, quote=True) + '"' if v is not None else '') for k, v in keep) + '>')

    def handle_endtag(self, tag): self.parts.append('</' + tag + '>')
    def handle_data(self, data): self.parts.append(data)
    def handle_entityref(self, name): self.parts.append('&' + name + ';')
    def handle_charref(self, name): self.parts.append('&#' + name + ';')
    def handle_comment(self, data): self.parts.append('<!--' + data + '-->')


def _compact_html(value):
    if isinstance(value, list):
        return [_compact_html(v) for v in value]
    if isinstance(value, dict):
        result = {k: _compact_html(v) for k, v in value.items()}
        if value.get('type') == 'text/html' and isinstance(value.get('value'), str):
            parser = _DisplayHTML(); parser.feed(value['value']); parser.close()
            result['value'] = ''.join(parser.parts)
        return result
    return value


_FIELD = re.compile(r"[A-Za-z][A-Za-z0-9_.]{0,63}")
ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024


def _project(item, fields):
    """Keep the item id and the requested attributes (or relationships) only (REQ-VXVUECHATBOT-024)."""
    attrs = item.get("attributes") or {}
    relations = item.get("relationships") or {}
    row, absent = {"id": item.get("id")}, []
    for name in fields:
        if name in attrs:
            row[name] = attrs[name]
        elif name in relations:
            row[name] = relations[name]
        else:
            absent.append(name)
    if absent:
        row["absent"] = absent
    return row


def integer(value, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"정수 범위 {minimum}~{maximum}를 확인하세요.")
    return value


class PolarionLookup:
    def __init__(self, root, client, *, project='VXvue'):
        directory = 'vxvue_desktop' if project == 'VXvue' else 'bellalun_shared'
        self.root = Path(root) / 'data' / directory / 'polarion'
        self.client = client

    def _save(self, response, source, compact=False, fields=None):
        self.root.mkdir(parents=True, exist_ok=True)
        for path in self.root.glob("*.json"):
            if re.fullmatch(r"[0-9a-f]{32}\.json", path.name) and path.stat().st_mtime < time.time() - 86400:
                path.unlink(missing_ok=True)
        items = response.get("data") or []
        if isinstance(items, dict):
            items = [items]
        if fields is not None:
            return self._write({"source": source, "collected_at": datetime.now(timezone.utc).isoformat(),
                                "fields": fields, "rows": [_project(item, fields) for item in items],
                                "scope": "고른 속성만 저장(REQ-VXVUECHATBOT-024). 다른 속성·관계·링크는 fields 없이 다시 조회. 민감값은 자리표로 대체. 수집은 분석 완료가 아님."},
                               compact)
        readable = []
        for item in items:
            attrs = item.get("attributes") or {}
            texts = {}
            for key, val in attrs.items():
                if isinstance(val, dict) and isinstance(val.get("value"), str):
                    texts[key] = html_to_text(val["value"])[0]
            if texts:
                readable.append({"id": item.get("id"), **texts})
        value = {"source": source, "collected_at": datetime.now(timezone.utc).isoformat(),
                 "response": response, "readable": readable,
                 "scope": "API 속성·관계·링크 필드 보존. 민감값은 자리표로 대체. 첨부 본문 미수집. 수집은 분석 완료가 아님."}
        return self._write(value, compact)

    def _write(self, value, compact):
        value, report = mask_payload(value)
        value["protection"] = report.as_dict()
        result_id = uuid.uuid4().hex
        (self.root / f"{result_id}.json").write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return self.read(result_id, compact=compact)

    def read(self, result_id, offset=0, limit=12000, compact=False):
        if not isinstance(result_id, str) or not re.fullmatch(r"[0-9a-f]{32}", result_id):
            raise ValueError("수집 결과 번호를 확인하세요.")
        integer(offset, 0, 100_000_000)
        integer(limit, 1, 12000)
        path = self.root / f"{result_id}.json"
        if not path.is_file() or path.stat().st_mtime < time.time() - 86400:
            raise ValueError("수집 결과가 만료됐습니다. Polarion을 다시 조회하세요.")
        text = path.read_text(encoding="utf-8")
        if not isinstance(compact, bool):
            raise ValueError('compact는 boolean입니다.')
        if compact:
            value = _compact_html(json.loads(text))
            value['selection'] = {'omitted': 'HTML 표시용 속성; 원본은 compact=false로 조회'}
            text = json.dumps(value, ensure_ascii=False)
        end = min(offset + limit, len(text))
        return {"result_id": result_id, "view": 'compact' if compact else 'full', "text": text[offset:end], "offset": offset, "total_chars": len(text),
                "truncated": end < len(text), "next_offset": end if end < len(text) else None}

    def query(self, query, page=1, page_size=10, compact=False, fields=None):
        if not isinstance(query, str) or not query.strip() or len(query) > 8000:
            raise ValueError("Polarion 필터는 1~8,000자로 입력하세요.")
        if fields is not None and (not isinstance(fields, list) or not 1 <= len(fields) <= 30
                                   or any(not isinstance(f, str) or not _FIELD.fullmatch(f) for f in fields)):
            raise ValueError("fields는 Polarion 속성 이름 1~30개입니다(영문·숫자·밑줄·점).")
        integer(page, 1, 100000)
        integer(page_size, 1, 50)
        project = self.client.config.project_id
        path = f"/projects/{project}/workitems"
        response = self.client._get(path, {"query": query, "fields[workitems]": "@all", "include": "author", "fields[users]": "name", "sort": "id",
                                          "page[size]": page_size, "page[number]": page})
        if not isinstance(response.get("data"), list) or response.get("errors"):
            raise ValueError("Polarion 목록 응답 형식이 맞지 않습니다. 0건으로 처리하지 않았습니다.")
        count = (response.get("meta") or {}).get("totalCount")
        more = bool((response.get("links") or {}).get("next"))
        if isinstance(count, int) and not isinstance(count, bool) and count > page * page_size:
            more = True
        if more and not response["data"]:
            raise ValueError("Polarion이 다음 페이지를 알렸지만 현재 목록이 비었습니다. 재조회가 필요합니다.")
        source = {"path": path, "query": query, "page": page, "page_size": page_size}
        if fields is not None:
            source["fields"] = fields
        result = self._save(response, source, compact, fields)
        items = response.get("data") or []
        listed = ([i.get("id") for i in items] if fields is not None else
                  [{"id": i.get("id"), "title": (i.get("attributes") or {}).get("title"),
                    "type": (i.get("attributes") or {}).get("type")} for i in items])
        return {**result, "page": page, "returned": len(items), "total_count": (response.get("meta") or {}).get("totalCount"),
                "next_page": page + 1 if more else None, "items": listed}

    def attachment(self, vp_id, attachment_id):
        """그 항목의 첨부 이미지 하나를 PNG로 돌려준다(REQ-VXVUECHATBOT-025). 원본은 저장하지 않는다."""
        if not isinstance(vp_id, str) or not re.fullmatch(r"VP-\d{1,12}", vp_id, re.I):
            raise ValueError("VP-1234 형식의 번호를 입력하세요.")
        vp = vp_id.upper()
        project = self.client.config.project_id
        if not isinstance(attachment_id, str) or not attachment_id.strip() or len(attachment_id) > 300:
            raise ValueError("첨부 번호를 확인하세요.")
        parts = attachment_id.split("/")
        if len(parts) == 3:
            if parts[0] != project or parts[1].upper() != vp:
                raise ValueError("이 항목의 첨부 번호만 조회할 수 있습니다.")
            parts = parts[2:]
        name = parts[0] if len(parts) == 1 else ""
        if not name.strip() or "\\" in name or ".." in name:
            raise ValueError('첨부 번호를 확인하세요. get_polarion_item(section="attachments")의 첨부 번호를 쓰세요.')
        path = f"/projects/{project}/workitems/{vp}/attachments/{quote(name, safe='')}/content"
        data = self.client.get_bytes(path, ATTACHMENT_MAX_BYTES)
        if not data.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"BM")):
            raise ValueError("이미지(PNG·JPEG·GIF·BMP) 첨부만 볼 수 있습니다. 다른 첨부는 Polarion에서 직접 확인하세요.")
        import fitz
        try:
            pixmap = fitz.Pixmap(data)
            if pixmap.alpha or pixmap.n != 3:
                pixmap = fitz.Pixmap(fitz.csRGB, fitz.Pixmap(pixmap, 0) if pixmap.alpha else pixmap)
            longest = max(pixmap.width, pixmap.height)
            if longest > 1800:
                scale = 1800 / longest
                with fitz.open() as doc:
                    page = doc.new_page(width=pixmap.width, height=pixmap.height)
                    page.insert_image(page.rect, pixmap=pixmap)
                    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            png = pixmap.tobytes("png")
        except Exception:
            raise ValueError("첨부 이미지를 읽지 못했습니다. Polarion에서 직접 확인하세요.") from None
        meta, _ = mask_payload({"vp_id": vp, "attachment_id": name, "width": pixmap.width, "height": pixmap.height,
                                "original_bytes": len(data), "visual_status": "rendered-not-reviewed",
                                "instruction": "Expected·Actual 화면을 직접 확인하세요. 이미지 제공은 내용 검증 완료를 뜻하지 않습니다."})
        return [{"type": "text", "text": json.dumps(meta, ensure_ascii=False)},
                {"type": "image", "mimeType": "image/png", "data": base64.b64encode(png).decode()}]

    def item(self, vp_id, section="item", page=1, compact=False):
        if not isinstance(vp_id, str) or not re.fullmatch(r"VP-\d{1,12}", vp_id, re.I):
            raise ValueError("VP-1234 형식의 번호를 입력하세요.")
        integer(page, 1, 100000)
        if section not in ("item", "comments", "attachments", "links"):
            raise ValueError("item, comments, attachments, links 중 하나를 선택하세요.")
        path = f"/projects/{self.client.config.project_id}/workitems/{vp_id.upper()}"
        if section != "item":
            path += "/" + ("linkedworkitems" if section == "links" else section)
        resource = {"item": "workitems", "comments": "workitem_comments", "attachments": "workitem_attachments", "links": None}[section]
        params = {f"fields[{resource}]": "@all"} if resource else {}
        if section != "item":
            params.update({"page[size]": 50, "page[number]": page})
        response = self.client._get(path, params)
        expected_type = dict if section == "item" else list
        if not isinstance(response.get("data"), expected_type) or response.get("errors"):
            raise ValueError("Polarion 항목 응답 형식이 맞지 않습니다. 조회 실패를 빈 결과로 처리하지 않았습니다.")
        return {**self._save(response, {"path": path, "vp_id": vp_id.upper(), "section": section, "page": page}, compact),
                "next_page": page + 1 if (response.get("links") or {}).get("next") else None}
