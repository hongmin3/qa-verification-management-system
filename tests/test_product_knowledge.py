"""제품 지식 자산 레지스트리 테스트.

픽스처는 **합성 파일명**만 쓴다 — 실제 사내 문서명·내용은 공개 저장소에 커밋하지 않는다.
실제 폴더로 도는 확인은 `real_fixtures.local.env`의 `REAL_VXVUE_KNOWLEDGE_DIR`가 있을 때만
동작하는 opt-in 테스트로 아래 마지막에 둔다 (없으면 skip).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.product_config import KnowledgeSourceConfig, ProductConfig
from app.core.product_knowledge import (
    KIND_INSTRUCTION_PROMPT,
    KIND_MANUAL,
    KIND_QA_RULES,
    KIND_SPECIFICATION,
    KIND_TESTCASE,
    KIND_UNKNOWN,
    classify,
    collected_assets,
    load_manifest,
    parse_asset_name,
    product_dir,
    product_slug,
    scan_source,
    sync_product,
)

_LOCAL_ENV_PATH = Path(__file__).resolve().parents[1] / "real_fixtures.local.env"


def _config(source_dir: Path, **source_kwargs) -> ProductConfig:
    return ProductConfig(
        product="Acme Viewer",
        version="1.0",
        knowledge_source=KnowledgeSourceConfig(dir=str(source_dir), **source_kwargs),
    )


def _write(directory: Path, name: str, body: str = "본문") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(body, encoding="utf-8")
    return path


# --- 분류 ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("file_name", "expected"),
    [
        ("(사양서) Acme 사양서1(260907).pdf", KIND_SPECIFICATION),
        ("(사양서) Licence Manager SRS 사양서(260907).pdf", KIND_SPECIFICATION),
        ("(매뉴얼) Acme Operation Manual.V1.0.11_KO.pdf", KIND_MANUAL),
        ("System Integration Guide for Acme.V1.0.11_KO.pdf", KIND_MANUAL),
        ("(TC) RA16-148-002_Acme_TestCase.xlsx", KIND_TESTCASE),
        ("[QA 작성 규칙] Acme TC 설계 및 자체검토 가이드_Rev1.12.md", KIND_QA_RULES),
        ("Acme 업무 자동화 지침 프롬프트.txt", KIND_INSTRUCTION_PROMPT),
        ("회의록 2026-09-08.md", KIND_UNKNOWN),
    ],
)
def test_classify_follows_filename_convention(file_name: str, expected: str) -> None:
    assert classify(file_name) == expected


def test_tc_prefix_wins_over_checklist_pattern() -> None:
    """`(TC)` 접두사가 있으면 Checklist 패턴보다 먼저 testcase로 확정돼야 한다."""
    assert classify("(TC) R-20-643_Acme_변경사항 영향성평가_Checklist.xlsx") == KIND_TESTCASE


def test_classify_can_be_overridden_per_product() -> None:
    assert classify("사양_v3.pdf", {KIND_SPECIFICATION: ["사양_*"]}) == KIND_SPECIFICATION


# --- 파일명 분해 --------------------------------------------------------------


@pytest.mark.parametrize(
    ("file_name", "base_name", "revision", "revision_kind"),
    [
        ("(사양서) Acme 사양서1(260907).pdf", "Acme 사양서1", "260907", "date"),
        ("(매뉴얼) Acme Operation Manual.V1.0.11_KO.pdf", "Acme Operation Manual", "V1.0.11", "version"),
        ("(매뉴얼) Acme Manual.V1.0.12W1_KO_확인완료.docx", "Acme Manual", "V1.0.12W1", "version"),
        ("[QA 작성 규칙] Acme 가이드_Rev1.12.md", "Acme 가이드", "Rev1.12", "rev"),
        ("Acme 지침 프롬프트.txt", "Acme 지침 프롬프트", "", ""),
    ],
)
def test_parse_asset_name_extracts_revision(file_name: str, base_name: str, revision: str, revision_kind: str) -> None:
    parsed = parse_asset_name(file_name)
    assert parsed["base_name"] == base_name
    assert parsed["revision"] == revision
    assert parsed["revision_kind"] == revision_kind


@pytest.mark.parametrize(
    ("file_name", "doc_number"),
    [
        ("(TC) RA16-148-002_Acme_TestCase.xlsx", "RA16-148-002"),
        ("(TC) RA16-14B-010_Acme Basic Function Checklist.xlsx", "RA16-14B-010"),
        ("(TC) R-20-643_Acme_변경사항 영향성평가_Checklist.xlsx", "R-20-643"),
        ("(TC) R-23-2346_AcmeViewer_기본기능_Checklist.xlsx", "R-23-2346"),
    ],
)
def test_doc_number_is_not_truncated_before_underscore(file_name: str, doc_number: str) -> None:
    """`RA16-148-002_Acme`처럼 `_`가 이어지면 문서번호가 반만 잘리는 회귀가 있었다."""
    assert parse_asset_name(file_name)["doc_number"] == doc_number


def test_doc_number_is_not_confused_with_revision() -> None:
    parsed = parse_asset_name("(TC) R-20-643_Acme_TestCase.xlsx")
    assert parsed["revision"] == ""
    assert parsed["doc_number"] == "R-20-643"


def test_status_note_in_parentheses_is_stripped() -> None:
    parsed = parse_asset_name("(TC) Mammo System 연동 TestCase (개정).xlsx")
    assert parsed["base_name"] == "Mammo System 연동 TestCase"
    assert parsed["status_note"] == "개정"


def test_version_revision_case_is_normalised() -> None:
    """같은 제품 안에서도 V1.0.11 / v1.4.4 로 대소문자가 섞여 있다."""
    assert parse_asset_name("(매뉴얼) Acme API Protocol Manual v1.4.4_EN.pdf")["revision"] == "V1.4.4"


def test_language_is_separated_so_ko_and_en_are_distinct_documents() -> None:
    korean = parse_asset_name("(매뉴얼) Acme Manual.V1.0_KO.pdf")
    english = parse_asset_name("(매뉴얼) Acme Manual.V1.0_EN.pdf")
    assert korean["base_name"] == english["base_name"]
    assert (korean["language"], english["language"]) == ("KO", "EN")


# --- 스캔: 무엇이 최신인지 판단 ------------------------------------------------


def test_older_revision_is_excluded_with_reason(tmp_path: Path) -> None:
    source = tmp_path / "지식"
    _write(source, "(사양서) Acme 사양서1(260731).md")
    _write(source, "(사양서) Acme 사양서1(260824).md")
    latest = _write(source, "(사양서) Acme 사양서1(260907).md")

    scan = scan_source(_config(source))

    assert [asset.file_name for asset in scan.selected] == [latest.name]
    excluded = {asset.file_name: asset for asset in scan.assets if not asset.selected}
    assert len(excluded) == 2
    for asset in excluded.values():
        assert asset.superseded_by == latest.name
        assert "이전 리비전" in asset.exclude_reason


def test_manual_text_extract_loses_to_original_pdf(tmp_path: Path) -> None:
    """사람이 미리 뽑아둔 .txt가 원본보다 오래된 실제 사례를 재현한다."""
    source = tmp_path / "지식"
    original = _write(source, "(사양서) Acme 사양서1(260907).pdf")
    stale = _write(source, "Acme 사양서1(260824).txt")

    scan = scan_source(_config(source))

    assert [asset.file_name for asset in scan.selected] == [original.name]
    excluded = next(asset for asset in scan.assets if asset.file_name == stale.name)
    assert excluded.superseded_by == original.name


def test_same_revision_prefers_original_format_over_extract(tmp_path: Path) -> None:
    source = tmp_path / "지식"
    original = _write(source, "(매뉴얼) Acme Operation Manual.V1.0.11_KO.pdf")
    _write(source, "Acme Operation Manual.V1.0.11_KO.txt")

    scan = scan_source(_config(source))

    assert [asset.file_name for asset in scan.selected] == [original.name]
    excluded = next(asset for asset in scan.assets if asset.file_name.endswith(".txt"))
    assert "추출본" in excluded.exclude_reason


def test_different_languages_are_kept_as_separate_documents(tmp_path: Path) -> None:
    source = tmp_path / "지식"
    _write(source, "(매뉴얼) Acme Manual.V1.0_KO.pdf")
    _write(source, "(매뉴얼) Acme Manual.V1.0_EN.pdf")

    scan = scan_source(_config(source))

    assert len(scan.selected) == 2


def test_unknown_filenames_are_reported_not_silently_dropped(tmp_path: Path) -> None:
    source = tmp_path / "지식"
    _write(source, "회의록 2026-09-08.md")

    scan = scan_source(_config(source))

    assert scan.selected == []
    assert scan.assets[0].exclude_reason


def test_ignore_patterns_skip_files_entirely(tmp_path: Path) -> None:
    source = tmp_path / "지식"
    _write(source, "(사양서) Acme 사양서1(260907).md")
    _write(source, "[자동화 운영 지침] Acme.md")

    scan = scan_source(_config(source, ignore=["[자동화*"]))

    assert [asset.file_name for asset in scan.assets] == ["(사양서) Acme 사양서1(260907).md"]


def test_office_lock_files_are_skipped(tmp_path: Path) -> None:
    source = tmp_path / "지식"
    _write(source, "(TC) Acme_TestCase.md")
    _write(source, "~$(TC) Acme_TestCase.md")

    scan = scan_source(_config(source))

    assert [asset.file_name for asset in scan.assets] == ["(TC) Acme_TestCase.md"]


def test_missing_source_dir_is_not_an_error(tmp_path: Path) -> None:
    scan = scan_source(_config(tmp_path / "없는폴더"))
    assert scan.exists is False
    assert scan.assets == []


# --- 수집(sync) ---------------------------------------------------------------


def test_sync_copies_originals_and_writes_normalised_text(tmp_path: Path) -> None:
    source = tmp_path / "지식"
    _write(source, "(사양서) Acme 사양서1(260907).md", "SRS-1234 로그인 화면")
    root = tmp_path / "project"

    outcome = sync_product(_config(source), root=root)

    assert outcome.status == "SUCCESS"
    assert outcome.copied == ["(사양서) Acme 사양서1(260907).md"]
    target = product_dir("Acme Viewer", root)
    assert (target / "original" / "specification" / "(사양서) Acme 사양서1(260907).md").is_file()
    normalized = target / "normalized" / "specification" / "(사양서) Acme 사양서1(260907).md"
    assert normalized.read_text(encoding="utf-8") == "SRS-1234 로그인 화면"


def test_sync_is_idempotent_when_nothing_changed(tmp_path: Path) -> None:
    source = tmp_path / "지식"
    _write(source, "(사양서) Acme 사양서1(260907).md")
    root = tmp_path / "project"

    sync_product(_config(source), root=root)
    second = sync_product(_config(source), root=root)

    assert second.copied == []
    assert second.unchanged == ["(사양서) Acme 사양서1(260907).md"]


def test_sync_recollects_when_content_changes(tmp_path: Path) -> None:
    source = tmp_path / "지식"
    path = _write(source, "(사양서) Acme 사양서1(260907).md", "처음")
    root = tmp_path / "project"
    sync_product(_config(source), root=root)

    path.write_text("바뀐 내용", encoding="utf-8")
    outcome = sync_product(_config(source), root=root)

    assert outcome.copied == ["(사양서) Acme 사양서1(260907).md"]
    normalized = product_dir("Acme Viewer", root) / "normalized" / "specification" / "(사양서) Acme 사양서1(260907).md"
    assert normalized.read_text(encoding="utf-8") == "바뀐 내용"


def test_manifest_records_exclusions_for_qa_review(tmp_path: Path) -> None:
    source = tmp_path / "지식"
    _write(source, "(사양서) Acme 사양서1(260824).md")
    _write(source, "(사양서) Acme 사양서1(260907).md")
    root = tmp_path / "project"

    sync_product(_config(source), root=root)
    manifest = load_manifest("Acme Viewer", root)

    assert manifest["counts"] == {"specification": 1}
    assert len(manifest["excluded"]) == 1
    assert manifest["excluded"][0]["superseded_by"] == "(사양서) Acme 사양서1(260907).md"
    assert manifest["source_dir"] == str(source)


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    source = tmp_path / "지식"
    _write(source, "(사양서) Acme 사양서1(260907).md")
    root = tmp_path / "project"

    outcome = sync_product(_config(source), root=root, dry_run=True)

    assert outcome.status == "DRY_RUN"
    assert not product_dir("Acme Viewer", root).exists()


def test_sync_reports_failure_per_file_and_keeps_going(tmp_path: Path) -> None:
    """정규화에 실패하는 파일이 있어도 나머지는 수집돼야 한다."""
    source = tmp_path / "지식"
    _write(source, "(사양서) Acme 사양서1(260907).md", "정상")
    broken = source / "(사양서) Acme 사양서2(260907).pdf"
    broken.write_bytes("PDF 가 아닌 내용".encode("utf-8"))
    root = tmp_path / "project"

    outcome = sync_product(_config(source), root=root)

    assert outcome.status == "PARTIAL"
    assert outcome.copied == ["(사양서) Acme 사양서1(260907).md"]
    assert outcome.failed == ["(사양서) Acme 사양서2(260907).pdf"]
    assert collected_assets("Acme Viewer", root=root) == [
        asset for asset in load_manifest("Acme Viewer", root)["assets"] if asset["file_name"].endswith("1(260907).md")
    ]


def test_missing_source_dir_yields_needs_config(tmp_path: Path) -> None:
    outcome = sync_product(_config(tmp_path / "없는폴더"), root=tmp_path / "project")
    assert outcome.status == "NEEDS_CONFIG"


def test_env_var_in_dir_is_expanded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ACME_KNOWLEDGE_DIR", str(tmp_path / "지식"))
    config = KnowledgeSourceConfig(dir="${ACME_KNOWLEDGE_DIR}")
    assert config.dir == str(tmp_path / "지식")


def test_unset_env_var_becomes_empty_not_literal_path() -> None:
    """`${...}` 문자열이 그대로 경로가 되면 엉뚱한 폴더를 만든다."""
    assert KnowledgeSourceConfig(dir="${DEFINITELY_NOT_SET_QA_TEST}").dir == ""


def test_env_default_is_used_when_variable_is_unset() -> None:
    """담당자 PC 는 환경변수를 설정하지 않는다 — YAML 의 기본값이 그대로 쓰여야 한다."""
    assert KnowledgeSourceConfig(dir="${NOT_SET_QA_TEST:-C:/PC/지식}").dir == "C:/PC/지식"


def test_env_default_is_overridden_on_the_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """운영 서버는 같은 폴더를 마운트 지점으로 본다."""
    monkeypatch.setenv("QA_TEST_MOUNT", "/srv/knowledge/vxvue")
    assert KnowledgeSourceConfig(dir="${QA_TEST_MOUNT:-C:/PC/지식}").dir == "/srv/knowledge/vxvue"


def test_windows_default_path_with_colon_is_not_truncated() -> None:
    """기본값에 `C:/...` 처럼 콜론이 들어가도 `:-` 구분자와 혼동하지 않아야 한다."""
    assert KnowledgeSourceConfig(dir="${NOT_SET_QA_TEST:-D:/QA/지식파일}").dir == "D:/QA/지식파일"


@pytest.mark.parametrize(
    ("product", "slug"),
    [("VXvue", "vxvue"), ("Bellalun Viewer", "bellalun-viewer"), ("VDMS-1100TM", "vdms-1100tm")],
)
def test_product_slug_is_path_safe(product: str, slug: str) -> None:
    assert product_slug(product) == slug


# --- 실제 폴더 opt-in 확인 ----------------------------------------------------


def _real_knowledge_dir() -> Path | None:
    if not _LOCAL_ENV_PATH.exists():
        return None
    for raw_line in _LOCAL_ENV_PATH.read_text(encoding="utf-8-sig").splitlines():
        key, _, value = raw_line.strip().partition("=")
        if key.strip() == "REAL_VXVUE_KNOWLEDGE_DIR" and value.strip():
            path = Path(value.strip())
            return path if path.is_dir() else None
    return None


@pytest.mark.skipif(_real_knowledge_dir() is None, reason="real_fixtures.local.env의 REAL_VXVUE_KNOWLEDGE_DIR 미설정")
def test_real_knowledge_dir_has_no_unclassified_assets() -> None:
    """실제 폴더의 모든 파일이 규약에 맞게 분류되는지 확인한다.

    새 문서가 들어왔을 때 조용히 unknown으로 빠지는 것을 잡기 위한 테스트다.
    """
    source = _real_knowledge_dir()
    assert source is not None
    scan = scan_source(_config(source))

    unclassified = [asset.file_name for asset in scan.assets if asset.kind == KIND_UNKNOWN]
    assert unclassified == [], f"분류되지 않은 파일: {unclassified}"
    assert scan.counts().get(KIND_SPECIFICATION, 0) > 0
    assert scan.counts().get(KIND_QA_RULES, 0) == 1
    assert scan.counts().get(KIND_INSTRUCTION_PROMPT, 0) == 1


@pytest.mark.skipif(_real_knowledge_dir() is None, reason="real_fixtures.local.env의 REAL_VXVUE_KNOWLEDGE_DIR 미설정")
def test_real_knowledge_dir_selects_one_file_per_logical_document() -> None:
    source = _real_knowledge_dir()
    assert source is not None
    scan = scan_source(_config(source))

    seen: dict[str, str] = {}
    for asset in scan.selected:
        assert asset.logical_id not in seen, f"논리 문서 중복: {asset.file_name} vs {seen[asset.logical_id]}"
        seen[asset.logical_id] = asset.file_name
    assert json.dumps(scan.counts(), ensure_ascii=False)
