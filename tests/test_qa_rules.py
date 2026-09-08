"""QA 규칙 로더 테스트.

픽스처는 실제 규칙 문서 구조를 모방한 **합성 텍스트**다 — 사내 QA 규칙 원문은 공개 저장소에
커밋하지 않는다. 실제 문서로 도는 확인은 수집된 사본이 있을 때만 동작하는 opt-in 테스트로
마지막에 둔다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.product_knowledge import KIND_INSTRUCTION_PROMPT, KIND_QA_RULES
from app.core.qa_rules import (
    GATE_TITLES,
    SKILL_TITLES,
    RuleSet,
    load_rule_set,
    normalize_markdown,
    parse_rule_document,
    rule_coverage,
)

# 실제 파일과 같은 escape 상태를 재현한 픽스처. `\#`, `&#x20;`, 줄마다 빈 줄.
ESCAPED_GUIDE = """\\# Acme TC 설계 및 자체검토 가이드

\\- 문서 버전: Rev.2.3

&#x20; 적용 대상: 사양 분석, Regression 분석

\\---

\\# 1. 전체 Agentic Workflow

모든 요청은 다음 순서로 수행한다.

1\\. 요청 모드 판별

2\\. Skill 선택

\\# 3. Skill Registry

\\## 3.1 S01 — Issue Analysis

Issue 유형과 Trigger 를 추출한다.

\\## 3.5 S05 — Regression Impact

Root Cause 기반 영향 범위를 분석한다.

\\# 19. API / WebSocket Semantic Verification

Command 번호만으로 의미를 추정하지 않는다.

\\# 32. Regression 영향 범위

\\## 직접

변경 기능을 검증한다.

\\## 상태

A→B→A 전이를 검증한다.

\\# 44. Agentic Workflow Gate

\\## Gate 2 — Specification Evidence

최신 유효 사양을 확인한다.

\\# 13. 출력 규칙

근거와 판정을 분리한다.
"""


def _guide(text: str = ESCAPED_GUIDE, name: str = "Acme 가이드"):
    return parse_rule_document(text, name=name, kind=KIND_QA_RULES)


# --- escape 정규화 -------------------------------------------------------------


def test_normalize_unescapes_headings() -> None:
    """실제 파일은 모든 특수문자가 escape 돼 있어 `grep '^#'` 이 0건이었다."""
    assert "\n# 1. 전체 Agentic Workflow" in normalize_markdown(ESCAPED_GUIDE)


def test_normalize_resolves_html_entities_and_nbsp() -> None:
    normalized = normalize_markdown("A&#x20;B&amp;C")
    assert "&#x20;" not in normalized and "&amp;" not in normalized
    assert " " not in normalized


def test_normalize_collapses_double_blank_lines() -> None:
    assert "\n\n\n" not in normalize_markdown("가\n\n\n\n나")


def test_normalize_keeps_intentional_markdown_tables() -> None:
    assert "| SRS No | Title |" in normalize_markdown("\\| SRS No \\| Title \\|")


# --- 절 파싱 ------------------------------------------------------------------


def test_sections_are_parsed_with_numbers_and_titles() -> None:
    document = _guide()
    headings = {section.heading for section in document.sections}
    assert "1 전체 Agentic Workflow" in headings
    assert "19 API / WebSocket Semantic Verification" in headings


def test_revision_is_read_from_document_header() -> None:
    assert _guide().revision == "Rev2.3"


def test_container_headings_without_body_are_not_emitted() -> None:
    """`# 3. Skill Registry` 처럼 하위 절만 묶는 제목은 프롬프트에 넣을 내용이 없다."""
    assert "3 Skill Registry" not in {section.heading for section in _guide().sections}


def test_anchor_identifies_document_and_section() -> None:
    section = next(s for s in _guide().sections if s.number == "19")
    assert section.anchor == "Acme 가이드#19"


# --- Skill / Gate 태깅 ---------------------------------------------------------


def test_explicit_skill_number_in_title_wins() -> None:
    section = next(s for s in _guide().sections if s.number == "3.1")
    assert section.skills == ("S01",)


def test_topic_keyword_maps_section_to_skill() -> None:
    section = next(s for s in _guide().sections if s.number == "19")
    assert "S08" in section.skills


def test_subsection_inherits_parent_tags() -> None:
    """`# 32. Regression 영향 범위` 아래 `## 직접`/`## 상태`는 제목만으로 주제를 알 수 없다."""
    children = [s for s in _guide().sections if s.title in ("직접", "상태")]
    assert len(children) == 2
    assert all("S05" in section.skills for section in children)


def test_gate_number_in_title_is_tagged() -> None:
    section = next(s for s in _guide().sections if "Specification Evidence" in s.title)
    assert section.gates == ("G2",)


def test_framework_sections_stay_untagged_as_common_rules() -> None:
    section = next(s for s in _guide().sections if s.number == "13")
    assert section.skills == () and section.gates == ()


def test_keyword_matching_ignores_whitespace_differences() -> None:
    """제품마다 `자체검토` / `자체 검토` 로 다르게 적는다."""
    spaced = parse_rule_document("# 14. 자체 검토 체크리스트\n\n출력 전 확인한다.\n", name="X", kind=KIND_QA_RULES)
    packed = parse_rule_document("# 14. 자체검토 체크리스트\n\n출력 전 확인한다.\n", name="X", kind=KIND_QA_RULES)
    assert spaced.sections[0].gates == packed.sections[0].gates == ("G5",)


# --- 슬라이싱 (토큰 예산) ------------------------------------------------------


def _rule_set() -> RuleSet:
    return RuleSet(product="Acme", documents=[_guide()])


def test_slice_includes_only_the_requested_skill_sections() -> None:
    sections = _rule_set().slice(skill="S05", char_budget=6000)
    numbers = {section.number or section.title for section in sections}
    # `3.5`는 제목이 S05를 직접 적은 절, `직접`/`상태`는 §32에서 태그를 물려받은 하위 절이다.
    # §32 자체는 본문이 없는 컨테이너라 절 목록에 없다.
    assert {"3.5", "직접", "상태"} <= numbers
    assert "3.1" not in numbers  # S01 전용 절은 들어가지 않는다


def test_slice_respects_char_budget() -> None:
    sections = _rule_set().slice(skill="S05", char_budget=60)
    assert sum(section.size for section in sections) <= 60


def test_slice_reserves_budget_for_skill_sections_over_common_ones() -> None:
    """공통 절이 예산을 다 먹어 정작 그 Skill 규칙이 빠지는 회귀가 있었다."""
    sections = _rule_set().slice(skill="S01", char_budget=200, general_ratio=0.3)
    assert any("S01" in section.skills for section in sections)


def test_slice_adds_common_sections_when_budget_allows() -> None:
    sections = _rule_set().slice(skill="S01", char_budget=6000)
    assert any(section.skills == () and section.gates == () for section in sections)


def test_dropped_sections_are_reported_not_silently_cut() -> None:
    rule_set = _rule_set()
    kept = rule_set.slice(skill="S05", char_budget=60)
    dropped = rule_set.dropped_sections(skill="S05", char_budget=60)
    assert len(kept) + len(dropped) == len(rule_set.sections)


def test_slice_by_gate() -> None:
    sections = _rule_set().slice(gate="G2", char_budget=6000)
    assert any("G2" in section.gates for section in sections)


def test_render_labels_product_and_revision() -> None:
    rendered = _rule_set().render(skill="S05", char_budget=6000)
    assert "Acme QA 규칙 Rev2.3" in rendered
    assert "발췌" in rendered


def test_render_is_empty_when_no_rules_match() -> None:
    assert RuleSet(product="Acme").render(skill="S05") == ""


def test_find_resolves_anchor_back_to_section() -> None:
    rule_set = _rule_set()
    assert rule_set.find("Acme 가이드#19") is not None
    assert rule_set.find("Acme 가이드#없는절") is None


# --- 커버리지 집계 -------------------------------------------------------------


def test_rule_coverage_reports_every_skill_and_gate() -> None:
    coverage = rule_coverage(_rule_set())
    assert set(coverage["skills"]) >= set(SKILL_TITLES)
    assert set(coverage["gates"]) >= set(GATE_TITLES)


def test_rule_coverage_reports_zero_for_uncovered_skill() -> None:
    """제품 규칙 문서가 그 Skill 을 다루지 않으면 0으로 보고돼야 한다 (조용히 넘기지 않는다)."""
    coverage = rule_coverage(_rule_set())
    assert coverage["skills"]["S09"]["sections"] == 0


def test_rule_set_without_documents_is_unavailable() -> None:
    rule_set = RuleSet(product="Acme")
    assert rule_set.available is False
    assert rule_set.revision == ""


def test_instruction_prompt_and_guide_are_both_loaded() -> None:
    rule_set = RuleSet(
        product="Acme",
        documents=[
            parse_rule_document("## 1. 역할\n\n너는 QA Agent 다.\n", name="Acme 지침", kind=KIND_INSTRUCTION_PROMPT),
            _guide(),
        ],
    )
    assert len(rule_set.documents) == 2
    assert rule_set.revision == "Rev2.3"  # 대표 리비전은 TC 작성 규칙 쪽을 쓴다


# --- 수집 사본에서 읽기 --------------------------------------------------------


def test_load_rule_set_reads_collected_copies(tmp_path: Path) -> None:
    from app.core.product_config import KnowledgeSourceConfig, ProductConfig
    from app.core.product_knowledge import sync_product

    source = tmp_path / "지식"
    source.mkdir()
    (source / "[QA 작성 규칙] Acme 가이드_Rev2.3.md").write_text(ESCAPED_GUIDE, encoding="utf-8")
    (source / "Acme 지침 프롬프트.txt").write_text("## 1. 역할\n\n너는 QA Agent 다.\n", encoding="utf-8")
    config = ProductConfig(product="Acme", knowledge_source=KnowledgeSourceConfig(dir=str(source)))
    root = tmp_path / "project"
    sync_product(config, root=root)

    rule_set = load_rule_set("Acme", root=root)

    assert rule_set.available is True
    assert rule_set.revision == "Rev2.3"
    assert {document.kind for document in rule_set.documents} == {KIND_QA_RULES, KIND_INSTRUCTION_PROMPT}


def test_load_rule_set_is_empty_when_nothing_collected(tmp_path: Path) -> None:
    """규칙이 없어도 예외를 올리지 않아야 한다 — 나머지 분석은 계속 돌아야 하기 때문이다."""
    rule_set = load_rule_set("Acme", root=tmp_path / "project")
    assert rule_set.available is False


def test_revision_falls_back_to_filename_when_document_lacks_header(tmp_path: Path) -> None:
    from app.core.product_config import KnowledgeSourceConfig, ProductConfig
    from app.core.product_knowledge import sync_product

    source = tmp_path / "지식"
    source.mkdir()
    (source / "[QA 작성 규칙] Acme 가이드_Rev9.9.md").write_text("# 1. 역할\n\n본문\n", encoding="utf-8")
    config = ProductConfig(product="Acme", knowledge_source=KnowledgeSourceConfig(dir=str(source)))
    root = tmp_path / "project"
    sync_product(config, root=root)

    assert load_rule_set("Acme", root=root).revision == "Rev9.9"


# --- 실제 수집본 opt-in 확인 ---------------------------------------------------


def _has_collected(product: str) -> bool:
    return load_rule_set(product).available


@pytest.mark.skipif(not _has_collected("VXvue"), reason="VXvue 규칙 자산이 아직 수집되지 않음")
def test_real_vxvue_rules_slice_saves_most_of_the_budget() -> None:
    """규칙 전문을 매 호출에 보내지 않는다는 것이 이 계층의 존재 이유다."""
    rule_set = load_rule_set("VXvue")
    total = sum(section.size for section in rule_set.sections)
    assert total > 20_000, "규칙 전문이 20KB 미만이면 파싱이 깨진 것"
    for skill in SKILL_TITLES:
        sliced = sum(section.size for section in rule_set.slice(skill=skill, char_budget=6000))
        assert sliced <= total * 0.30, f"{skill} slice 가 전문의 30%를 넘음"


@pytest.mark.skipif(not _has_collected("VXvue"), reason="VXvue 규칙 자산이 아직 수집되지 않음")
def test_real_vxvue_rules_cover_every_skill() -> None:
    coverage = rule_coverage(load_rule_set("VXvue"))
    uncovered = [skill for skill, info in coverage["skills"].items() if info["sections"] == 0]
    assert uncovered == [], f"규칙이 태깅되지 않은 Skill: {uncovered}"
