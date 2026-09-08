"""규칙 구현현황 표가 실제와 어긋나지 않는지 확인한다.

두 가지 드리프트를 잡는다.

1. 규칙 문서 Rev 가 올라가 새 절이 생겼는데 분류를 안 한 경우 → 새 규칙이 조용히 미구현으로
   남는 것을 막는다. (수집된 실제 규칙 문서가 있을 때만 도는 opt-in)
2. `status=IMPLEMENTED` 라고 적었는데 `where` 파일이 없는 경우 → "구현했다"는 주장과 코드가
   어긋나는 것을 막는다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.qa_rules import KIND_QA_RULES, load_rule_set
from app.modules.qa_agent.rule_capability import (
    MODE_LABELS,
    RULE_CAPABILITY,
    STATUS_IMPLEMENTED,
    STATUS_LABELS,
    missing_paths,
    stale_sections,
    summary,
    unclassified_sections,
)

ROOT = Path(__file__).resolve().parents[1]


def test_every_entry_uses_a_known_mode_and_status() -> None:
    for section, capability in RULE_CAPABILITY.items():
        assert capability.mode in MODE_LABELS, f"§{section}: 알 수 없는 mode {capability.mode}"
        assert capability.status in STATUS_LABELS, f"§{section}: 알 수 없는 status {capability.status}"


def test_every_entry_explains_itself() -> None:
    """분류만 있고 이유가 없으면 나중에 판단을 되짚을 수 없다."""
    for section, capability in RULE_CAPABILITY.items():
        assert capability.title, f"§{section}: 제목 없음"
        assert capability.note or capability.where, f"§{section}: note 도 where 도 없음"


def test_implemented_entries_point_at_files_that_exist() -> None:
    missing = missing_paths(ROOT)
    assert missing == [], f"IMPLEMENTED 인데 파일이 없는 항목: {missing}"


def test_implemented_entries_declare_where() -> None:
    for section, capability in RULE_CAPABILITY.items():
        if capability.status == STATUS_IMPLEMENTED:
            assert capability.where, f"§{section}: IMPLEMENTED 인데 where 가 비어 있음"


def test_summary_counts_match_the_table() -> None:
    result = summary()
    assert result["total"] == len(RULE_CAPABILITY)
    assert sum(result["by_mode"].values()) == len(RULE_CAPABILITY)
    assert sum(result["by_status"].values()) == len(RULE_CAPABILITY)


def test_unclassified_detects_new_sections() -> None:
    assert unclassified_sections(set(RULE_CAPABILITY) | {"99"}) == ["99"]


def test_stale_detects_removed_sections() -> None:
    reduced = set(RULE_CAPABILITY) - {"1"}
    assert stale_sections(reduced) == ["1"]


def _collected_rule_sections(product: str) -> set[str] | None:
    rule_set = load_rule_set(product)
    guide = next((document for document in rule_set.documents if document.kind == KIND_QA_RULES), None)
    if guide is None:
        return None
    return {section.top_number for section in guide.sections if section.top_number}


@pytest.mark.skipif(_collected_rule_sections("VXvue") is None, reason="VXvue 규칙 문서가 아직 수집되지 않음")
def test_real_guide_has_no_unclassified_section() -> None:
    sections = _collected_rule_sections("VXvue")
    assert sections is not None
    unclassified = unclassified_sections(sections)
    assert unclassified == [], (
        f"규칙 문서에 있는데 rule_capability 표에 없는 절: {unclassified}. "
        "규칙 Rev 가 올라갔다면 새 절의 구현 방식을 분류해야 한다."
    )


@pytest.mark.skipif(_collected_rule_sections("VXvue") is None, reason="VXvue 규칙 문서가 아직 수집되지 않음")
def test_real_guide_still_contains_every_classified_section() -> None:
    sections = _collected_rule_sections("VXvue")
    assert sections is not None
    stale = stale_sections(sections)
    assert stale == [], f"표에는 있는데 규칙 문서에서 사라진 절: {stale}"
