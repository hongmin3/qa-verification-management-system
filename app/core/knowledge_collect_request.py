"""제품 상세의 `지금 수집` 요청 (SPEC REQ-KNOW-021).

운영 서버는 담당자 PC 의 지식 폴더를 볼 수 없다. 그래서 버튼은 요청 시각만 남기고, PC 의 1분 예약
(`app/core/knowledge_retry.run_due`)이 그 요청을 보고 평소 업로드 경로(REQ-SYNC-002)로 올린다.

요청은 그 제품의 수집 폴더 안 파일 하나다. 끝났는지는 따로 지우지 않고 동기화 기록으로 판단한다 —
업로드 확정이나 PC 의 실패 보고가 요청 뒤에 끝났으면 그 요청은 처리된 것이다.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.core.product_knowledge import product_dir

REQUEST_FILE = "collect_request.json"
#: 지식 폴더 수집·업로드 확정이 남기는 동기화 기록 종류 (REQ-KNOW-016).
FOLDER_SYNC_KIND = "product_knowledge"


def _parse(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def request_collect(product: str, root: Path | None = None, now: str | None = None) -> str:
    """요청 시각을 남긴다. 이미 있으면 시각만 새로 쓴다."""
    requested_at = now or datetime.now(timezone.utc).isoformat()
    path = product_dir(product, root) / REQUEST_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"product": product, "requested_at": requested_at}, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)
    return requested_at


def pending_request(product: str, storage, root: Path | None = None) -> str:
    """아직 처리되지 않은 요청 시각. 없으면 빈 문자열."""
    try:
        payload = json.loads((product_dir(product, root) / REQUEST_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    requested_at = str(payload.get("requested_at") or "")
    requested = _parse(requested_at)
    if requested is None:
        return ""
    finished = storage.latest_finished_sync(product, FOLDER_SYNC_KIND)
    done = _parse((finished or {}).get("synced_at", ""))
    return requested_at if done is None or requested > done else ""
