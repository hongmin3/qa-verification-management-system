"""제품별 스냅샷 폴더 (SPEC REQ-QAINTEL-003·004·023).

    data/daily_qa/snapshots/<slug>/srs/<날짜>.json
    data/daily_qa/snapshots/<slug>/issues/<날짜>.json

개편 전 SRS 스냅샷은 `data/daily_qa/snapshots/<날짜>.json` 에 있다. 새 폴더에 아직 스냅샷이 없으면
`daily_qa.product` 로 적힌 제품에 한해 옛 위치를 비교 기준으로 읽는다. 파일은 옮기지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.modules.daily_qa.srs_snapshot import load_snapshot, previous_snapshot, save_snapshot, snapshot_meta

KIND_SRS = "srs"
KIND_ISSUES = "issues"


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
