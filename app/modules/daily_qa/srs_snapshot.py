"""스냅샷 저장과 SRS 비교 (SPEC REQ-DAILY-002, REQ-QAINTEL-003).

저장·읽기 함수는 SRS 와 이슈 스냅샷이 함께 쓴다. 제품·종류별 폴더는 `snapshots.py` 가 정한다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.modules.daily_qa.product_adapter import collapse


@dataclass
class SrsDiff:
    added: list[dict] = field(default_factory=list)
    removed: list[dict] = field(default_factory=list)
    #: {"id", "old_id", "title", "before": {...}, "after": {...}, "fields": [바뀐 필드],
    #:  "added_sentences": [...], "removed_sentences": [...]}
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

#: 문장 경계. 마침표·물음표·느낌표 뒤 공백, 줄바꿈, 번호 목록 앞에서 끊는다.
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。])\s+|\n+|(?=\s\d+[.)]\s)")


def save_snapshot(directory: Path, run_date: str, items: list[dict], collected_at: str = "", source: str = "",
                  extra: dict | None = None) -> Path:
    """임시 파일에 먼저 쓰고 이름을 바꾼다. 쓰다 끊겨도 반쪽 파일이 남지 않는다.

    `source` 는 이 시스템이 직접 수집하지 않은 스냅샷의 출처다(예: `alm_qa_automation`, REQ-QAINTEL-029).
    `extra` 는 머리에 더 적을 값이다(예: 되살린 파일 이름 `restored_files`).
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{run_date}.json"
    payload = {"run_date": run_date, "collected_at": collected_at, "count": len(items),
               **({"source": source} if source else {}), **(extra or {}),
               "items": sorted(items, key=lambda item: item["id"])}
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    temporary.replace(path)
    return path


def load_snapshot(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8")).get("items") or []


def snapshot_meta(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {"run_date": data.get("run_date", path.stem), "collected_at": data.get("collected_at", ""), "count": data.get("count", 0),
            "source": data.get("source", "")}


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


def sentences(text: str) -> list[str]:
    parts = [collapse(part) for part in SENTENCE_SPLIT_RE.split(text or "")]
    return [part for part in parts if part]


def sentence_changes(before: str, after: str) -> tuple[list[str], list[str]]:
    """본문 변경을 문장 단위로 좁힌다. (더해진 문장, 빠진 문장). 순서는 원문 순서다."""
    old, new = sentences(before), sentences(after)
    old_set, new_set = set(old), set(new)
    return [line for line in new if line not in old_set], [line for line in old if line not in new_set]


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
        # 공백·줄바꿈만 다른 것은 기능 의미가 없는 변경이다 (REQ-QAINTEL-003).
        changed = [name for name in COMPARED_FIELDS if collapse(old[item_id].get(name)) != collapse(new[item_id].get(name))]
        if changed:
            entry = {
                "id": item_id,
                "old_id": new[item_id].get("old_id", ""),
                "title": new[item_id].get("title", ""),
                "fields": changed,
                "before": {name: old[item_id].get(name, "") for name in changed},
                "after": {name: new[item_id].get(name, "") for name in changed},
            }
            if "text" in changed:
                added, removed = sentence_changes(old[item_id].get("text", ""), new[item_id].get("text", ""))
                entry["added_sentences"], entry["removed_sentences"] = added, removed
            result.modified.append(entry)
    return result
