"""ALM-QA-Automation 사양서 자동화(`apps/srs-spec`)의 과거 SRS 스냅샷 가져오기 (SPEC REQ-QAINTEL-029).

`srs-spec` 은 날짜 폴더마다 SRS 하나를 JSON 파일 하나로 남긴다.

    <srs-spec>/snapshots/2026-08-31/manifest.json
    <srs-spec>/snapshots/2026-08-31/VXvue/VP-1277.json

이 모듈은 그 원본 필드를 Polarion 응답 모양으로 되돌린 뒤, 매일 실행과 같은 함수(`normalize_srs`)로
공통 모델을 만든다. 그래서 가져온 스냅샷과 오늘 수집이 같은 글자를 만든다. `srs-spec` 폴더에는 쓰지 않는다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.modules.daily_qa import snapshots
from app.modules.daily_qa.product_adapter import ProductProfile, normalize_srs, srs_titles

SOURCE = "alm_qa_automation"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class DayResult:
    date: str
    status: str            # imported | exists | skipped | would_import
    count: int = 0
    reason: str = ""
    restored: list[str] = field(default_factory=list)

    def line(self) -> str:
        labels = {"imported": "가져옴", "exists": "이미 있음", "skipped": "건너뜀", "would_import": "가져올 예정(시험)"}
        text = f"{self.date} {labels[self.status]}"
        if self.count:
            text += f" {self.count}건"
        if self.restored:
            text += f" (되살린 파일 {len(self.restored)}개)"
        return text + (f" ({self.reason})" if self.reason else "")


@dataclass
class ImportResult:
    days: list[DayResult] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for day in self.days:
            result[day.status] = result.get(day.status, 0) + 1
        return result


def as_workitem(raw: dict) -> dict:
    """`srs-spec` 의 SRS 기록 → Polarion REST 응답의 Work Item 모양."""
    def rich(key: str):
        return {"type": "text/html", "value": raw[key]} if raw.get(key) else None

    return {"id": raw.get("uid") or raw.get("id"), "attributes": {
        "id": raw.get("id"), "title": raw.get("title"), "status": raw.get("status"), "updated": raw.get("updated"),
        "type": raw.get("type"), "oldId": raw.get("old_id"), "isCategory": raw.get("is_category"),
        "descriptionKR": rich("description_kr_raw"), "description": rich("description_raw"),
    }}


def _expected_total(day_dir: Path, project_id: str) -> int | None:
    path = day_dir / "manifest.json"
    if not path.is_file():
        return None
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for stat in manifest.get("stats") or []:
        if stat.get("project_id") == project_id:
            return int(stat.get("expected_total") or stat.get("total_items") or 0) or None
    return None


def _generated_at(day_dir: Path) -> str:
    try:
        return str(json.loads((day_dir / "manifest.json").read_text(encoding="utf-8")).get("generated_at") or "")
    except (OSError, ValueError):
        return ""


def restore(text: str) -> dict | None:
    """끝에 이전 쓰기의 꼬리가 남은 JSON 을 되살린다.

    `srs-spec` 이 같은 파일을 동시에 두 번 쓰면 짧은 쓰기 뒤에 긴 쓰기의 끝이 남는다. 앞부분이 온전한
    JSON 하나이고 남은 글자가 그 JSON 의 끝과 글자 그대로 같을 때만 앞 JSON 을 믿는다.
    """
    try:
        value, end = json.JSONDecoder().raw_decode(text)
    except ValueError:
        return None
    head, tail = text[:end].rstrip(), text[end:].rstrip()
    if not isinstance(value, dict) or not tail or not head.endswith(tail):
        return None
    return value


def read_day(day_dir: Path, project_id: str, profile: ProductProfile) -> tuple[list[dict] | None, str, list[str]]:
    """그 날짜의 SRS 를 공통 모델로. 믿을 수 없으면 (None, 이유). 세 번째 값은 되살린 파일 이름."""
    project_dir = day_dir / project_id
    if not project_dir.is_dir():
        return None, f"{project_id} 폴더 없음", []
    raws, broken, restored = [], [], []
    for path in sorted(project_dir.glob("*.json")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            broken.append(path.name)
            continue
        try:
            raws.append(json.loads(text))
        except ValueError:
            value = restore(text)
            if value is None:
                broken.append(path.name)
            else:
                raws.append(value)
                restored.append(path.name)
    if broken:
        return None, f"읽지 못한 파일 {len(broken)}개: {', '.join(broken[:3])}" + (" 외" if len(broken) > 3 else ""), []
    if not raws:
        return None, "SRS 파일 없음", []
    expected = _expected_total(day_dir, project_id)
    if expected is not None and expected != len(raws):
        return None, f"manifest 개수 {expected}건과 파일 {len(raws)}건이 다름", []
    items = [as_workitem(raw) for raw in raws]
    titles = srs_titles(items, profile)
    return [normalize_srs(item, profile, titles) for item in items], "", restored


def import_history(source_dir: Path, store: snapshots.SnapshotStore, project_id: str, profile: ProductProfile,
                   dry_run: bool = False) -> ImportResult:
    """`source_dir` 의 날짜 폴더를 차례로 가져온다. 여러 번 돌려도 결과가 같다."""
    result = ImportResult()
    for day_dir in sorted(path for path in source_dir.iterdir() if path.is_dir() and DATE_RE.match(path.name)):
        date = day_dir.name
        if store.exists(snapshots.KIND_SRS, date):
            result.days.append(DayResult(date, "exists"))
            continue
        items, reason, restored = read_day(day_dir, project_id, profile)
        if items is None:
            result.days.append(DayResult(date, "skipped", reason=reason))
            continue
        if dry_run:
            result.days.append(DayResult(date, "would_import", len(items), restored=restored))
            continue
        store.save(snapshots.KIND_SRS, date, items, _generated_at(day_dir), source=SOURCE,
                   extra={"restored_files": restored} if restored else None)
        result.days.append(DayResult(date, "imported", len(items), restored=restored))
    return result
