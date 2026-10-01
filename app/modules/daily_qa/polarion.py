"""Polarion REST 읽기 전용 클라이언트 (SPEC REQ-DAILY-002).

이 클래스는 GET 요청을 만드는 `_get` 하나만 갖는다. POST/PATCH/DELETE 를 만드는 함수가
없으므로, 일일 점검 코드가 Polarion 을 바꿀 경로가 코드에 존재하지 않는다
(`tests/test_daily_qa_polarion.py` 가 이 사실을 검사한다).

응답을 공통 모델로 바꾸는 일(필드 이름 대응)은 이 파일이 하지 않는다. 제품마다 필드 이름이 달라서
제품 설정과 `product_adapter.py` 가 맡는다 (SPEC REQ-QAINTEL-002).
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

    def get_comments(self, workitem_id: str, strict: bool = False) -> list[dict[str, Any]]:
        """댓글 목록. `strict` 면 실패를 예외로 알린다 (스냅샷이 실패를 '댓글 없음'으로 오해하지 않게)."""
        short = workitem_id.split("/")[-1]
        path = f"/projects/{self.config.project_id}/workitems/{short}/comments"
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            try:
                data = self._get(path, {"fields[workitem_comments]": "@all", "page[size]": self.config.page_size, "page[number]": page})
            except PolarionError:
                if strict:
                    raise
                return items
            batch = data.get("data") or []
            items.extend(batch)
            if not batch or not (data.get("links") or {}).get("next"):
                return items
            page += 1
