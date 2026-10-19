"""제품별 스냅샷 폴더 (SPEC REQ-QAINTEL-003·004·023).

    data/daily_qa/snapshots/<slug>/srs/<날짜>.json
    data/daily_qa/snapshots/<slug>/issues/<날짜>.json

개편 전 SRS 스냅샷은 `data/daily_qa/snapshots/<날짜>.json` 에 있다. 새 폴더에 아직 스냅샷이 없으면
`daily_qa.product` 로 적힌 제품에 한해 옛 위치를 비교 기준으로 읽는다. 파일은 옮기지 않는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app.modules.daily_qa.srs_snapshot import load_snapshot, previous_snapshot, save_snapshot, snapshot_meta

KIND_SRS = "srs"
KIND_ISSUES = "issues"
_DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True)
class SnapshotStore:
    root: Path
    legacy_srs_dir: Path | None = None

    def directory(self, kind: str) -> Path:
        return self.root / kind

    def _dirs(self, kind: str) -> list[Path]:
        dirs = [self.directory(kind)]
        if kind == KIND_SRS and self.legacy_srs_dir is not None:
            dirs.append(self.legacy_srs_dir)
        return dirs

    def previous(self, kind: str, run_date: str) -> Path | None:
        """오늘 이전 가장 최근 스냅샷. 새 폴더를 먼저 보고, 없을 때만 옛 위치를 본다."""
        for directory in self._dirs(kind):
            found = previous_snapshot(directory, run_date)
            if found:
                return found
        return None

    def at_or_before(self, kind: str, day: str) -> Path | None:
        """`day` 날짜이거나 그 전 가장 최근 스냅샷 (기간 분석의 기준, REQ-QAINTEL-027)."""
        return self.previous(kind, f"{day}~")   # '~' 는 숫자보다 뒤라 그날 파일까지 포함한다

    def between(self, kind: str, start_day: str, end_day: str) -> list[Path]:
        """변경 리포트의 비교 대상 (REQ-ALMCHG-002). 시작일 또는 그 전 가장 가까운 기록과
        `start_day < 날짜 <= end_day` 인 기록을 날짜순으로. 같은 날짜가 두 폴더에 있으면 새 폴더 것이다."""
        by_day: dict[str, Path] = {}
        for directory in reversed(self._dirs(kind)):     # 새 폴더가 마지막에 덮어쓴다
            if directory.is_dir():
                for path in directory.glob("*.json"):
                    if _DAY_RE.fullmatch(path.stem):
                        by_day[path.stem] = path
        start = max((day for day in by_day if day <= start_day), default=None)
        return [by_day[day] for day in sorted(by_day)
                if day == start or start_day < day <= end_day]

    def latest(self, kind: str) -> Path | None:
        return self.previous(kind, "9999-99-99")

    def oldest_before(self, kind: str, run_date: str) -> Path | None:
        for directory in self._dirs(kind):
            if directory.is_dir():
                candidates = sorted(path for path in directory.glob("*.json") if path.stem < run_date)
                if candidates:
                    return candidates[0]
        return None

    def load(self, path: Path | None) -> list[dict] | None:
        return load_snapshot(path) if path else None

    def meta(self, path: Path | None) -> dict:
        return snapshot_meta(path) if path else {}

    def save(self, kind: str, run_date: str, items: list[dict], collected_at: str = "", source: str = "",
             extra: dict | None = None) -> Path:
        return save_snapshot(self.directory(kind), run_date, items, collected_at, source, extra)

    def exists(self, kind: str, run_date: str) -> bool:
        """그 날짜 스냅샷이 이 제품 폴더에 있는가 (옛 위치는 보지 않는다)."""
        return (self.directory(kind) / f"{run_date}.json").is_file()

    def oldest(self, kind: str) -> Path | None:
        """가장 오래된 스냅샷 (기간 입력의 최솟값, REQ-QAINTEL-027)."""
        return self.oldest_before(kind, "9999-99-99")

    def discard(self, kind: str, run_date: str) -> None:
        """방금 저장한 오늘 스냅샷을 되돌린다 (이벤트 저장이 실패했을 때, REQ-QAINTEL-006)."""
        (self.directory(kind) / f"{run_date}.json").unlink(missing_ok=True)


def for_settings(cfg) -> SnapshotStore:
    return SnapshotStore(root=cfg.snapshot_dir, legacy_srs_dir=cfg.legacy_snapshot_dir)
