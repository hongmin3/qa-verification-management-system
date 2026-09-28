"""Polarion REST 읽기 전용 클라이언트 (SPEC REQ-DAILY-002).

이 클래스는 GET 요청을 만드는 `_get` 하나만 갖는다. POST/PATCH/DELETE 를 만드는 함수가
없으므로, 일일 점검 코드가 Polarion 을 바꿀 경로가 코드에 존재하지 않는다
(`tests/test_daily_qa_polarion.py` 가 이 사실을 검사한다).

필드 이름(`oldId`, `descriptionKR`, `rndReviewResult` 등)은 ALM-QA-Automation 의 srs-spec 앱과
issue-export 앱(통합 전 alm-issue-export)이 실제 서버에서 확인해 쓰는 이름을 그대로 따른다.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import Any

import httpx

from app.modules.daily_qa.settings import PolarionSettings


class PolarionError(RuntimeError):
    """Polarion 요청 실패. 메시지에 토큰을 넣지 않는다."""


Transport = Callable[[str, dict[str, Any]], dict[str, Any]]


class ReadOnlyPolarionClient:
    def __init__(self, config: PolarionSettings, transport: Transport | None = None) -> None:
        if not config.configured:
            raise PolarionError("POLARION_HOST / POLARION_TOKEN / project_id 가 설정되지 않았습니다.")
        self.config = config
        self._transport = transport or self._http_get

    # -- 유일한 요청 경로 ------------------------------------------------------
    def _http_get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.config.token}", "Accept": "application/json"}
        last_error = ""
        for attempt in range(1, 4):
            try:
                response = httpx.get(
                    url, params=params, headers=headers,
                    timeout=self.config.timeout_seconds, verify=self.config.verify_ssl,
                )
                if response.status_code in (401, 403):
                    raise PolarionError(f"Polarion 인증/권한 오류 HTTP {response.status_code}")
                if response.status_code == 404:
                    raise PolarionError(f"Polarion 경로 없음 HTTP 404: {url.split('/rest/v1')[-1]}")
                response.raise_for_status()
                return response.json()
            except PolarionError:
                raise
            except (httpx.HTTPError, ValueError) as exc:
                last_error = type(exc).__name__
                time.sleep(min(2**attempt, 8))
            finally:
                if self.config.request_interval_seconds:
                    time.sleep(self.config.request_interval_seconds)
        raise PolarionError(f"Polarion 요청이 3회 실패했습니다 ({last_error}): {url.split('/rest/v1')[-1]}")

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.config.host}/polarion/rest/v1{path}"
        return self._transport(url, dict(params or {}))

    # -- 조회 ----------------------------------------------------------------
    def iter_workitems(self, query: str, fields: str = "@all", sort: str = "id") -> Iterator[dict[str, Any]]:
        path = f"/projects/{self.config.project_id}/workitems"
        page = 1
        seen: set[str] = set()
        while True:
            data = self._get(
                path,
                {
                    "query": query,
                    "fields[workitems]": fields,
                    "sort": sort,
                    "page[size]": self.config.page_size,
                    "page[number]": page,
                },
            )
            items = data.get("data") or []
            if not items:
                return
            for item in items:
                item_id = str(item.get("id") or "")
                if item_id in seen:
                    continue
                seen.add(item_id)
                yield item
            if not (data.get("links") or {}).get("next"):
                return
            page += 1

    def get_comments(self, workitem_id: str) -> list[dict[str, Any]]:
        short = workitem_id.split("/")[-1]
        try:
            data = self._get(f"/projects/{self.config.project_id}/workitems/{short}/comments", {"fields[workitem_comments]": "@all"})
        except PolarionError:
            return []
        return data.get("data") or []


def _rich_text(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("value") or "")
    return str(value or "")


def _enum_text(value: Any) -> str:
    """열거형 필드는 문자열 또는 `{"id": ...}` 로 온다."""
    if isinstance(value, dict):
        return str(value.get("id") or value.get("name") or "")
    return str(value or "")


def _relationship_ids(relationships: dict, name: str) -> list[str]:
    node = (relationships or {}).get(name) or {}
    data = node.get("data")
    if isinstance(data, dict):
        data = [data]
    ids = []
    for entry in data or []:
        raw = str((entry or {}).get("id") or "")
        if raw:
            ids.append(raw.split("/")[-1])
    return ids


def normalize_srs(item: dict[str, Any]) -> dict[str, Any]:
    """SRS Work Item 하나를 스냅샷 항목으로 바꾼다. 본문은 HTML 을 걷어낸 텍스트다."""
    from app.parsers.polarion_issue import html_to_text

    attrs = item.get("attributes") or {}
    item_id = str(attrs.get("id") or str(item.get("id") or "").split("/")[-1])
    body_html = _rich_text(attrs.get("descriptionKR")) or _rich_text(attrs.get("description"))
    text, _ = html_to_text(body_html)
    return {
        "id": item_id,
        "old_id": str(attrs.get("oldId") or ""),
        "title": str(attrs.get("title") or ""),
        "status": str(attrs.get("status") or ""),
        "updated": str(attrs.get("updated") or ""),
        "is_category": bool(attrs.get("isCategory") or False) or str(attrs.get("type") or "") == "category",
        "text": text.strip(),
    }


def normalize_issue(item: dict[str, Any]) -> dict[str, Any]:
    """이슈 Work Item 을 AI 입력용 사전으로 바꾼다. 첨부 이미지는 넣지 않는다."""
    from app.parsers.polarion_issue import html_to_text

    attrs = item.get("attributes") or {}
    relationships = item.get("relationships") or {}

    def text(name: str) -> str:
        value, _ = html_to_text(_rich_text(attrs.get(name)))
        return value.strip()

    return {
        "id": str(attrs.get("id") or str(item.get("id") or "").split("/")[-1]),
        "title": str(attrs.get("title") or ""),
        "status": str(attrs.get("status") or ""),
        "severity": str(attrs.get("severity") or ""),
        "lab_review_result": _enum_text(attrs.get("rndReviewResult")),
        "updated": str(attrs.get("updated") or ""),
        "created": str(attrs.get("created") or ""),
        "description": text("description"),
        "reproduction_step": text("reproductionStep"),
        "occurrence_cause": text("occurrenceCause"),
        "action_details": text("actionDetails"),
        "occurred_versions": _relationship_ids(relationships, "occurredVersion"),
        "target_versions": _relationship_ids(relationships, "targetVersion"),
        "linked_ids": _relationship_ids(relationships, "linkedWorkItems"),
    }
