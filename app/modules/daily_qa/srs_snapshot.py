"""SRS 스냅샷 저장과 비교 (SPEC REQ-DAILY-002)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SrsDiff:
    added: list[dict] = field(default_factory=list)
    removed: list[dict] = field(default_factory=list)
    #: {"id", "old_id", "title", "before": {...}, "after": {...}, "fields": [바뀐 필드]}
    modified: list[dict] = field(default_factory=list)
    baseline_only: bool = False

    @property
    def changed_ids(self) -> list[str]:
        return [item["id"] for item in (*self.added, *self.removed, *self.modified)]

    def as_dict(self) -> dict:
        return {
            "added": self.added,
            "removed": self.removed,
            "modified": self.modified,
            "baseline_only": self.baseline_only,
        }


#: 비교에 쓰는 필드. updated 는 본문 변화 없이도 바뀌므로 넣지 않는다.
COMPARED_FIELDS = ("old_id", "title", "status", "text")


def save_snapshot(directory: Path, run_date: str, items: list[dict]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{run_date}.json"
    payload = {"run_date": run_date, "count": len(items), "items": sorted(items, key=lambda item: item["id"])}
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    temporary.replace(path)
    return path


def load_snapshot(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8")).get("items") or []


def previous_snapshot(directory: Path, run_date: str) -> Path | None:
    """`run_date` 보다 앞선 가장 최근 스냅샷. 같은 날 다시 돌면 그날 것은 제외한다."""
    if not directory.is_dir():
        return None
    candidates = sorted(path for path in directory.glob("*.json") if path.stem < run_date)
    return candidates[-1] if candidates else None


def snapshots_since(directory: Path, first_date: str) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.glob("*.json") if path.stem >= first_date)


def diff_snapshots(before: list[dict] | None, after: list[dict]) -> SrsDiff:
    if before is None:
        return SrsDiff(baseline_only=True)
    old = {item["id"]: item for item in before}
    new = {item["id"]: item for item in after}
    result = SrsDiff()
    for item_id in sorted(new.keys() - old.keys()):
        result.added.append(new[item_id])
    for item_id in sorted(old.keys() - new.keys()):
        result.removed.append(old[item_id])
    for item_id in sorted(new.keys() & old.keys()):
        changed = [name for name in COMPARED_FIELDS if (old[item_id].get(name) or "") != (new[item_id].get(name) or "")]
        if changed:
            result.modified.append(
                {
                    "id": item_id,
                    "old_id": new[item_id].get("old_id", ""),
                    "title": new[item_id].get("title", ""),
                    "fields": changed,
                    "before": {name: old[item_id].get(name, "") for name in changed},
                    "after": {name: new[item_id].get(name, "") for name in changed},
                }
            )
    return result
