"""Stable Partitioning: SRS를 5~6개 PDF 파일로 고정 배정한다.

핵심 원칙:
- 같은 SRS는 절대 두 파일에 걸치지 않는다 (record 단위로 file_key를 1개만 배정).
- 배정 기준은 VP ID(생성 순서, 매번 바뀔 수 있음)가 아니라 oldId의 최상위 모듈 번호
  (module_key, 예 '01')다. Polarion 카테고리 트리 조사 결과 이 번호는 신규 SRS가
  추가돼도 해당 모듈 트리 안 제자리에 삽입되므로 매주 실행해도 같은 SRS가 같은
  파일에 남는다.
- config.yaml의 매핑에 없는 새 모듈 번호가 나타나면 자동으로 마지막 그룹에 임시
  배정하되, 반드시 경고로 남겨 사람이 config를 갱신하도록 한다 (조용히 묻히지 않게).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .config import Config

logger = logging.getLogger("srs_automation")


@dataclass
class FileGroup:
    file_key: str
    display_name: str
    records: list[dict[str, Any]]


def assign_file_groups(
    all_records: list[dict[str, Any]], config: Config
) -> tuple[list[FileGroup], list[str]]:
    warnings: list[str] = []

    module_to_file: dict[str, str] = {}
    file_display: dict[str, str] = {}
    file_order: list[str] = []
    for g in config.vxvue_groups:
        file_display[g.file_key] = g.display_name
        file_order.append(g.file_key)
        for m in g.modules:
            module_to_file[m.zfill(2)] = g.file_key

    single_file_by_project: dict[str, str] = {}
    for s in config.single_file_projects:
        file_display[s.file_key] = s.display_name
        file_order.append(s.file_key)
        single_file_by_project[s.project_id] = s.file_key

    fallback_file_key = config.vxvue_groups[-1].file_key if config.vxvue_groups else None

    buckets: dict[str, list[dict[str, Any]]] = {k: [] for k in file_order}

    for rec in all_records:
        if rec["project_id"] in single_file_by_project:
            buckets[single_file_by_project[rec["project_id"]]].append(rec)
            continue

        if rec["project_id"] != config.vxvue_project_id:
            msg = f"partition 설정에 없는 프로젝트({rec['project_id']}) - 배정 불가: {rec['uid']}"
            warnings.append(msg)
            logger.error(msg)
            continue

        module_key = rec.get("module_key")
        file_key = module_to_file.get(module_key) if module_key else None
        if file_key is None:
            file_key = fallback_file_key
            msg = (
                f"모듈 매핑에 없는 SRS 발견 (module_key={module_key}) - "
                f"임시로 '{file_key}'에 배정: {rec['uid']} ({rec.get('old_id')})"
            )
            warnings.append(msg)
            logger.warning(msg)
        rec["file_group"] = file_key
        buckets[file_key].append(rec)

    for rec in all_records:
        if rec["project_id"] in single_file_by_project:
            rec["file_group"] = single_file_by_project[rec["project_id"]]

    groups = [
        FileGroup(file_key=fk, display_name=file_display[fk], records=buckets.get(fk, []))
        for fk in file_order
    ]
    return groups, warnings
