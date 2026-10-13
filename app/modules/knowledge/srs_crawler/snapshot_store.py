"""Snapshot 저장/조회.

SRS 1건당 JSON 파일 1개로 저장한다 (snapshots/<date>/<project_id>/<id>.json).
Git으로 버전 관리할 때 파일 단위 diff가 그대로 SRS 단위 diff가 되도록 하기 위함이다
(하나의 거대한 배열 JSON에 비해 어떤 SRS가 바뀌었는지 한눈에 보이고, 충돌 위험도 적다).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("srs_automation")


def save_snapshot(snapshots_root: Path, date_str: str, project_id: str, records: list[dict[str, Any]]) -> None:
    project_dir = snapshots_root / date_str / project_id
    project_dir.mkdir(parents=True, exist_ok=True)
    for rec in records:
        dest = project_dir / f"{rec['id']}.json"
        dest.write_text(json.dumps(rec, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    logger.info("[%s] Snapshot %d건 저장: %s", project_id, len(records), project_dir)


def save_manifest(snapshots_root: Path, date_str: str, manifest: dict[str, Any]) -> None:
    dest = snapshots_root / date_str / "manifest.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def load_manifest(snapshots_root: Path, date_str: str) -> dict[str, Any] | None:
    path = snapshots_root / date_str / "manifest.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_snapshot(snapshots_root: Path, date_str: str, project_id: str) -> dict[str, dict[str, Any]]:
    project_dir = snapshots_root / date_str / project_id
    records: dict[str, dict[str, Any]] = {}
    if not project_dir.exists():
        return records
    for f in project_dir.glob("*.json"):
        rec = json.loads(f.read_text(encoding="utf-8"))
        records[rec["uid"]] = rec
    return records


def list_snapshot_dates(snapshots_root: Path) -> list[str]:
    if not snapshots_root.exists():
        return []
    dates = [p.name for p in snapshots_root.iterdir() if p.is_dir()]
    return sorted(dates)


def find_previous_snapshot_date(snapshots_root: Path, current_date_str: str) -> str | None:
    dates = [d for d in list_snapshot_dates(snapshots_root) if d < current_date_str]
    return dates[-1] if dates else None


def find_baseline_snapshot_date(snapshots_root: Path, since_date_str: str, current_date_str: str) -> str | None:
    """임의 기준일에 대한 비교 기준 스냅샷을 고른다.

    요청한 날짜에 스냅샷이 없을 수 있으므로(자동화가 매일 도는 것이 아니다),
    **요청일 이하에서 가장 늦은** 스냅샷을 쓴다. 그런 것이 없으면 None을 반환하고,
    호출부가 사용 가능한 날짜를 안내한다 - 임의로 다른 날짜를 골라 조용히 비교하면
    사용자가 "7월 25일 기준"이라고 믿는 리포트가 실제로는 다른 기준이 된다.
    """
    candidates = [d for d in list_snapshot_dates(snapshots_root) if since_date_str >= d and d < current_date_str]
    if candidates:
        return candidates[-1]
    return None
