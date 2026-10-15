"""분석 상세 화면에 보일 이슈 원문 (SPEC REQ-QAINTEL-020 4·7번 구획).

제품의 가장 최근 이슈 스냅샷(`data/daily_qa/snapshots/<제품>/issues/<날짜>.json` 의 `items`)에서 읽는다.
화면을 여는 일은 Polarion 도 AI 도 부르지 않으므로 저장된 파일만 본다. 파일은 수정 시각이 같으면 다시
읽지 않는다. 작성자·댓글 작성자의 번호와 이름은 화면에 보이기만 하며 AI 입력에는 넣지 않는다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock

#: 이름을 찾지 못한 작성자 표시 (REQ-QAINTEL-020 1번 구획).
UNKNOWN_NAME = "이름 미확인 ({id})"

_CACHE: dict[str, tuple[int, int, dict[str, dict]]] = {}
_LOCK = Lock()


@dataclass
class IssueBody:
    id: str
    title: str
    status: str
    rd_result: str
    created: str
    updated: str
    reporter_id: str
    reporter_name: str
    description: str
    reproduction_step: str
    occurrence_cause: str
    action_details: str
    occurred_versions: list[str] = field(default_factory=list)
    target_versions: list[str] = field(default_factory=list)
    #: 시간순이 아니라 스냅샷에 저장된 순서다. 각 값은 `id, created, author_id, author_name, text`.
    comments: list[dict] = field(default_factory=list)


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()


def _read_items(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8")).get("items") or []


def _newest(snapshot_dir: Path) -> Path | None:
    if not snapshot_dir.is_dir():
        return None
    files = sorted(snapshot_dir.glob("*.json"), key=lambda path: path.stem)
    return files[-1] if files else None


def _index(path: Path) -> dict[str, dict] | None:
    """파일 수정 시각·크기가 같으면 읽어 둔 값을 쓴다. 읽을 수 없는 파일은 None 이다."""
    try:
        stat = path.stat()
        key = str(path)
        with _LOCK:
            cached = _CACHE.get(key)
        if cached and cached[:2] == (stat.st_mtime_ns, stat.st_size):
            return cached[2]
        items = {str(item.get("id")): item for item in _read_items(path) if isinstance(item, dict) and item.get("id")}
    except (OSError, ValueError, AttributeError):
        return None
    with _LOCK:
        _CACHE[key] = (stat.st_mtime_ns, stat.st_size, items)
    return items


def _text(value) -> str:
    return "" if value is None else str(value)


def _names(storage, product_slug: str, ids: set[str]) -> dict[str, str]:
    lookup = getattr(storage, "qa_reporter_names", None)
    if not lookup or not ids:
        return {}
    try:
        return lookup(product_slug, sorted(ids))
    except Exception:   # 이름 표를 읽지 못해도 본문은 보인다
        return {}


def load(product_slug: str, issue_id: str, *, snapshot_dir: Path | None = None, storage=None) -> IssueBody | None:
    """이슈 하나의 원문. 스냅샷이나 이슈가 없으면 None 이다.

    `snapshot_dir` 는 제품의 이슈 스냅샷 폴더다. 비우면 서버 설정의 위치를 쓴다.
    `storage` 가 있으면 작성자 표(`qa_issue_reporters`)에서 번호에 맞는 이름을 채운다.
    """
    if snapshot_dir is None:
        from app.core.config import get_settings
        snapshot_dir = get_settings().root / "data" / "daily_qa" / "snapshots" / product_slug / "issues"
    path = _newest(Path(snapshot_dir))
    items = _index(path) if path else None
    item = (items or {}).get(str(issue_id))
    if not item:
        return None
    raw_comments = [entry for entry in item.get("comments") or [] if isinstance(entry, dict)]
    ids = {_text(entry.get("author")) for entry in raw_comments} | {_text(item.get("reporter_id"))}
    names = _names(storage, product_slug, {value for value in ids if value})

    def person(person_id: str, saved: str = "") -> str:
        if saved:
            return saved
        if not person_id:
            return ""
        return names.get(person_id) or UNKNOWN_NAME.format(id=person_id)

    reporter_id = _text(item.get("reporter_id"))
    return IssueBody(
        id=str(item["id"]), title=_text(item.get("title")), status=_text(item.get("status")),
        rd_result=_text(item.get("rd_result")), created=_text(item.get("created")), updated=_text(item.get("updated")),
        reporter_id=reporter_id, reporter_name=person(reporter_id, _text(item.get("reporter_name"))),
        description=_text(item.get("description")), reproduction_step=_text(item.get("reproduction_step")),
        occurrence_cause=_text(item.get("occurrence_cause")), action_details=_text(item.get("action_details")),
        occurred_versions=[_text(value) for value in item.get("occurred_versions") or []],
        target_versions=[_text(value) for value in item.get("target_versions") or []],
        comments=[{"id": _text(entry.get("id")), "created": _text(entry.get("created")),
                   "author_id": _text(entry.get("author")), "author_name": person(_text(entry.get("author"))),
                   "text": _text(entry.get("text"))} for entry in raw_comments],
    )
