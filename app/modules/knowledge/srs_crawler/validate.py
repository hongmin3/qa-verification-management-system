"""실행 성공 판정.

Python 프로세스가 예외 없이 끝났다는 것만으로 성공으로 보지 않는다. 아래 항목을
모두 통과해야 최종 산출물을 지식파일 폴더에 반영한다. 하나라도 실패하면 기존
최신 사양서를 교체하지 않는다.
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .collector import CollectStats
from .pdf import PdfResult

logger = logging.getLogger("srs_automation")


@dataclass
class ValidationResult:
    ok: bool
    checks: list[dict[str, Any]] = field(default_factory=list)

    def add(self, name: str, passed: bool, detail: str = "") -> None:
        self.checks.append({"name": name, "passed": passed, "detail": detail})
        if not passed:
            self.ok = False
            logger.error("검증 실패 [%s]: %s", name, detail)
        else:
            logger.info("검증 통과 [%s]: %s", name, detail)


def check_duplicates(all_records: list[dict[str, Any]]) -> list[str]:
    seen: dict[str, int] = {}
    for r in all_records:
        seen[r["uid"]] = seen.get(r["uid"], 0) + 1
    return [uid for uid, count in seen.items() if count > 1]


def validate_run(
    *,
    collect_stats: list[CollectStats],
    all_records: list[dict[str, Any]],
    pdf_results: list[PdfResult],
    expected_pdf_count: int,
    partition_warnings: list[str],
    assigned_records: list[dict[str, Any]],
    required_pdf_names: list[str],
    previous_srs_count: int | None = None,
    min_expected_srs_ratio: float = 0.95,
    require_all_pdfs: bool = True,
) -> ValidationResult:
    result = ValidationResult(ok=True)

    for st in collect_stats:
        result.add(
            f"srs_count_match:{st.project_id}",
            st.total_items == st.expected_total,
            f"expected={st.expected_total}, collected={st.total_items}",
        )

    duplicates = check_duplicates(all_records)
    result.add("no_duplicate_srs_id", len(duplicates) == 0, f"duplicates={duplicates}" if duplicates else "OK")

    missing_id = [r["uid"] for r in all_records if not r.get("id")]
    result.add("no_missing_srs_id", len(missing_id) == 0, f"missing={len(missing_id)}")

    collected = Counter(r["uid"] for r in all_records)
    assigned = Counter(r["uid"] for r in assigned_records)
    result.add(
        "all_srs_assigned_once",
        collected == assigned and all(count == 1 for count in assigned.values()),
        f"missing={list((collected - assigned).elements())}, extra={list((assigned - collected).elements())}",
    )
    ratio_valid = 0 <= min_expected_srs_ratio <= 1
    result.add("valid_min_expected_srs_ratio", ratio_valid, f"ratio={min_expected_srs_ratio}")
    if previous_srs_count:
        result.add(
            "previous_snapshot_count_ratio",
            ratio_valid and len(all_records) >= previous_srs_count * min_expected_srs_ratio,
            f"current={len(all_records)}, previous={previous_srs_count}, minimum_ratio={min_expected_srs_ratio}",
        )

    names = [pr.path.name.casefold() for pr in pdf_results]
    required = [name.casefold() for name in required_pdf_names]
    result.add("nonempty_groups_have_pdfs", set(required).issubset(names),
               f"missing={sorted(set(required) - set(names))}")
    result.add("unique_pdf_names", len(names) == len(set(names)) and len(required) == len(set(required)),
               "Each group must have a distinct output name")
    result.add(
        "all_pdfs_generated",
        bool(pdf_results) and (len(pdf_results) == expected_pdf_count if require_all_pdfs
                               else len(required) <= len(pdf_results) <= expected_pdf_count),
        f"generated={len(pdf_results)}, expected={expected_pdf_count}",
    )

    for pr in pdf_results:
        result.add(f"pdf_nonzero:{pr.path.name}", pr.ok and pr.size_bytes > 0, f"size={pr.size_bytes}, error={pr.error}")
        result.add(f"pdf_has_pages:{pr.path.name}", pr.ok and pr.page_count > 0, f"pages={pr.page_count}")

    if partition_warnings:
        # 분할 매핑 밖 SRS가 있어도 자동으로 마지막 그룹에 배정하므로 데이터 유실은 아니지만,
        # config 갱신이 필요하다는 신호이므로 검증 항목에는 남기되 실패로 처리하지는 않는다.
        logger.warning("Partition 경고 %d건 - config.yaml의 vxvue_groups.modules 갱신을 검토하세요.", len(partition_warnings))

    total_images_failed = sum(1 for r in all_records for i in r.get("image_results", []) if not i.get("ok"))
    if total_images_failed:
        logger.warning("이미지 다운로드 실패 %d건 (리포트에 기록됨, 실행 실패로 처리하지는 않음)", total_images_failed)

    return result
