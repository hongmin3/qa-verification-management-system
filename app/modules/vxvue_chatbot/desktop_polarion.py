"""On-demand Polarion GETs. Preserve every API field and expose bounded pages."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time
import uuid

from app.parsers.polarion_issue import html_to_text
from app.core.security_filter import mask_payload


def integer(value, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"정수 범위 {minimum}~{maximum}를 확인하세요.")
    return value


class PolarionLookup:
    def __init__(self, root, client):
        self.root = Path(root) / "data/vxvue_desktop/polarion"
        self.client = client

    def _save(self, response, source):
        self.root.mkdir(parents=True, exist_ok=True)
        for path in self.root.glob("*.json"):
            if re.fullmatch(r"[0-9a-f]{32}\.json", path.name) and path.stat().st_mtime < time.time() - 86400:
                path.unlink(missing_ok=True)
        items = response.get("data") or []
        if isinstance(items, dict):
            items = [items]
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
        value, report = mask_payload(value)
        value["protection"] = report.as_dict()
        result_id = uuid.uuid4().hex
        (self.root / f"{result_id}.json").write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return self.read(result_id)

    def read(self, result_id, offset=0, limit=12000):
        if not isinstance(result_id, str) or not re.fullmatch(r"[0-9a-f]{32}", result_id):
            raise ValueError("수집 결과 번호를 확인하세요.")
        integer(offset, 0, 100_000_000)
        integer(limit, 1, 12000)
        path = self.root / f"{result_id}.json"
        if not path.is_file() or path.stat().st_mtime < time.time() - 86400:
            raise ValueError("수집 결과가 만료됐습니다. Polarion을 다시 조회하세요.")
        text = path.read_text(encoding="utf-8")
        end = min(offset + limit, len(text))
        return {"result_id": result_id, "text": text[offset:end], "offset": offset, "total_chars": len(text),
                "truncated": end < len(text), "next_offset": end if end < len(text) else None}

    def query(self, query, page=1, page_size=10):
        if not isinstance(query, str) or not query.strip() or len(query) > 8000:
            raise ValueError("Polarion 필터는 1~8,000자로 입력하세요.")
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
        result = self._save(response, {"path": path, "query": query, "page": page, "page_size": page_size})
        items = response.get("data") or []
        return {**result, "page": page, "returned": len(items), "total_count": (response.get("meta") or {}).get("totalCount"),
                "next_page": page + 1 if more else None,
                "items": [{"id": i.get("id"), "title": (i.get("attributes") or {}).get("title"),
                           "type": (i.get("attributes") or {}).get("type")} for i in items]}

    def item(self, vp_id, section="item", page=1):
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
        return {**self._save(response, {"path": path, "vp_id": vp_id.upper(), "section": section, "page": page}),
                "next_page": page + 1 if (response.get("links") or {}).get("next") else None}
