"""Polarion Issue 파서 테스트.

픽스처는 실제 `backup.json` 구조를 모방한 **합성 Issue** 다 — 실제 사내 Issue 내용은 공개
저장소에 커밋하지 않는다. 실제 Export 폴더로 도는 확인은 `config/products/*.yaml` 의
`issue_source.export_dir` 가 이 호스트에 있을 때만 동작하는 opt-in 테스트로 마지막에 둔다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.product_knowledge import resolve_config
from app.parsers.polarion_issue import (
    TYPE_BLOCKED,
    TYPE_DUPLICATE,
    TYPE_PROGRAM_FIXED,
    TYPE_SPEC_NOT_BUG,
    TYPE_UNCLASSIFIED,
    build_search_terms,
    classify_issue_type,
    html_to_text,
    list_exported_issues,
    load_exported_issue,
    load_issue,
    normalize_version_label,
    parse_issue_backup,
    split_marked_sections,
)

STANDARD_BODY = (
    "<div>[Precondition]</div><div>ACME-1000 장비 연결</div>"
    "<div>[Step]</div><div>1. 설정 화면을 연다.</div><div>2. Security 옵션을 켠다.</div>"
    "<div>[Expected Result]</div><div>2. 확인 팝업이 표시된다.</div>"
    "<div>[Actual Result]</div><div>팝업이 표시되지 않는다.</div>"
    "<div>[Log, Data]</div><div>AcmeLog_20260908.zip</div>"
)


def _payload(**overrides) -> dict:
    attributes = {
        "id": "AP-1001",
        "type": "issue",
        "title": "Security 옵션 확인 팝업이 표시되지 않음",
        "titleEng": "Confirmation popup is not shown",
        "status": "reviewed",
        "severity": "normal",
        "priority": "50.0",
        "occurrenceFrequency": "always",
        "defectScore": "grade_c",
        "productStage": "dc",
        "created": "2026-06-01T06:52:54.308Z",
        "updated": "2026-07-31T00:05:16.831Z",
        "rndReviewResult": "lab_fixed",
        "reproductionStep": {"type": "text/html", "value": STANDARD_BODY},
        "occurrenceCause": {"type": "text/plain", "value": "설정 반영 시점 누락 (AP-428 참고)"},
        "actionDetails": {"type": "text/plain", "value": "설정 적용 시점을 로그인 직후로 변경"},
    }
    attributes.update(overrides.pop("attributes", {}))
    payload = {
        "workitem": {
            "id": "Acme/AP-1001",
            "attributes": attributes,
            "relationships": {
                "occurredVersion": {"data": [{"type": "plans", "id": "Acme/Acme_1_1_0_001"}]},
                "targetVersion": {"data": [{"type": "plans", "id": "Acme/Acme_1_1_0_002"}]},
            },
            "links": {"portal": "https://alm.example.com/redirect?id=AP-1001"},
        },
        "linkedWorkItems": [
            {"attributes": {"role": "relates_to"}, "target": {"id": "Acme/AP-5500", "attributes": {"type": "srs"}}},
            {"attributes": {"role": "relates_to"}, "target": {"id": "Acme/AP-9001", "attributes": {"type": "issue"}}},
        ],
        "comments": [
            {"attributes": {"id": "1", "created": "2026-07-27T00:03:21.970Z", "text": {"type": "text/html", "value": "<p>확인했습니다.</p>"}}},
        ],
        "attachments": [{"id": "Acme/AP-1001/1-shot.png", "attributes": {"fileName": "shot.png"}}],
    }
    payload.update(overrides)
    return payload


# --- HTML → 텍스트 -----------------------------------------------------------


def test_html_breaks_become_lines() -> None:
    text, _ = html_to_text("가<br/>나<div>다</div>")
    assert text.splitlines() == ["가", "나", "다"]


def test_html_entities_and_nbsp_are_resolved() -> None:
    text, _ = html_to_text("A&nbsp;B&amp;C")
    assert text == "A B&C"


def test_images_are_kept_as_markers_not_dropped() -> None:
    """이미지는 사람이 확인해야 하므로 있었다는 사실이 남아야 한다."""
    text, images = html_to_text('설명<img src="workitemimg:1-shot.png"/>')
    assert images == ["1-shot.png"]
    assert "[이미지: 1-shot.png]" in text


def test_inline_styles_are_stripped_but_text_kept() -> None:
    text, _ = html_to_text('<span style="font-weight: bold;">1.1.0.001 Reopen</span>')
    assert text == "1.1.0.001 Reopen"


# --- 마커 구획 분리 -----------------------------------------------------------


def test_standard_markers_are_split_into_sections() -> None:
    text, _ = html_to_text(STANDARD_BODY)
    sections, preamble = split_marked_sections(text)
    assert set(sections) == {"precondition", "steps", "expected", "actual", "log_data"}
    assert preamble == []


def test_korean_marker_aliases_are_recognised() -> None:
    sections, _ = split_marked_sections("[기대결과]\n표시된다\n[현재결과]\n표시되지 않는다")
    assert sections["expected"] == ["표시된다"]
    assert sections["actual"] == ["표시되지 않는다"]


def test_unknown_bracket_tags_stay_as_content() -> None:
    """`[동물용]`, `[뷰어]` 같은 본문 꼬리표가 실제로 쓰인다 — 구획을 바꾸면 안 된다."""
    sections, _ = split_marked_sections("[Step]\n[동물용] 촬영을 시작한다\n2. 종료한다")
    assert sections["steps"] == ["[동물용] 촬영을 시작한다", "2. 종료한다"]


def test_text_outside_markers_is_kept_as_preamble() -> None:
    sections, preamble = split_marked_sections("1.1.0.001 Reopen\n[Step]\n1. 시작")
    assert preamble == ["1.1.0.001 Reopen"]
    assert sections["steps"] == ["1. 시작"]


# --- Issue 유형 분류 (규칙 §6) -------------------------------------------------


@pytest.mark.parametrize(
    ("lab_result", "expected"),
    [
        ("lab_fixed", TYPE_PROGRAM_FIXED),
        ("lab_inspec", TYPE_SPEC_NOT_BUG),
        ("lab_nobug", TYPE_SPEC_NOT_BUG),
        ("lab_duplicate", TYPE_DUPLICATE),
        ("lab_pending", TYPE_BLOCKED),
    ],
)
def test_lab_review_result_maps_to_issue_type_without_llm(lab_result: str, expected: str) -> None:
    issue_type, candidates = classify_issue_type(lab_result)
    assert issue_type == expected
    assert candidates == (expected,)


def test_ambiguous_lab_result_is_not_guessed() -> None:
    """`lab_noact` 는 사양대로(B)인지 재현 불가(G)인지 코드만으로 갈리지 않는다.

    규칙 §37 이 근거 없는 확정을 금지하므로 후보만 남기고 QA 가 고른다.
    """
    issue_type, candidates = classify_issue_type("lab_noact")
    assert issue_type == TYPE_UNCLASSIFIED
    assert len(candidates) == 2


def test_unknown_lab_result_is_unclassified() -> None:
    assert classify_issue_type("lab_something_new") == (TYPE_UNCLASSIFIED, ())


def test_spec_type_blocks_auto_runtime_tc() -> None:
    """규칙 §6: Spec/Not Bug 는 요구받지 않으면 Runtime TC 를 자동 생성하지 않는다."""
    record = parse_issue_backup(_payload(attributes={"rndReviewResult": "lab_inspec"}))
    assert record.blocks_auto_runtime_tc is True


def test_program_fixed_allows_runtime_tc() -> None:
    assert parse_issue_backup(_payload()).blocks_auto_runtime_tc is False


# --- 전체 파싱 ----------------------------------------------------------------


def test_parse_extracts_identity_and_metadata() -> None:
    record = parse_issue_backup(_payload())
    assert (record.issue_id, record.project) == ("AP-1001", "Acme")
    assert record.title_en == "Confirmation popup is not shown"
    assert (record.status, record.occurrence_frequency, record.defect_score) == ("reviewed", "always", "grade_c")
    assert record.portal_url.endswith("AP-1001")


def test_parse_extracts_standard_body_sections() -> None:
    record = parse_issue_backup(_payload())
    assert record.precondition == ["ACME-1000 장비 연결"]
    assert record.steps == ["설정 화면을 연다.", "Security 옵션을 켠다."]
    assert record.expected == ["2. 확인 팝업이 표시된다."]
    assert record.actual == ["팝업이 표시되지 않는다."]
    assert record.log_data == ["AcmeLog_20260908.zip"]
    assert record.has_standard_body is True


def test_step_numbers_are_stripped_for_comparison() -> None:
    record = parse_issue_backup(_payload())
    assert not record.steps[0].startswith("1.")


def test_linked_work_items_are_split_by_target_type() -> None:
    record = parse_issue_backup(_payload())
    assert record.linked_srs == ["AP-5500"]
    assert record.linked_issues == ["AP-9001"]


def test_versions_are_extracted() -> None:
    record = parse_issue_backup(_payload())
    assert record.occurred_versions == ["Acme_1_1_0_001"]
    assert record.target_versions == ["Acme_1_1_0_002"]


def test_cause_and_resolution_are_extracted() -> None:
    record = parse_issue_backup(_payload())
    assert "설정 반영 시점 누락" in record.occurrence_cause
    assert "로그인 직후" in record.action_details


def test_comments_and_attachments_are_extracted() -> None:
    record = parse_issue_backup(_payload())
    assert [comment.text for comment in record.comments] == ["확인했습니다."]
    assert record.attachments == ["shot.png"]


def test_missing_fields_do_not_raise() -> None:
    """실측에서 필드 존재율이 고르지 않다 (description 은 38건 중 1건뿐)."""
    record = parse_issue_backup({"workitem": {"id": "Acme/AP-2", "attributes": {"id": "AP-2"}}})
    assert record.issue_id == "AP-2"
    assert record.has_standard_body is False
    assert record.occurrence_cause == ""


def test_body_without_markers_is_treated_as_steps() -> None:
    payload = _payload(attributes={"reproductionStep": {"type": "text/html", "value": "1. 실행한다<br/>2. 종료한다"}})
    record = parse_issue_backup(payload)
    assert record.steps == ["실행한다", "종료한다"]


def test_missing_step_marker_is_reported_by_has_standard_body() -> None:
    """실제로 `[Expected Result]` 만 있는 Issue 가 있다 — G1 이 보고할 사실이다."""
    payload = _payload(attributes={"reproductionStep": {"type": "text/html", "value": "[Expected Result]<br/>표시된다"}})
    record = parse_issue_backup(payload)
    assert record.expected == ["표시된다"]
    assert record.steps == []
    assert record.has_standard_body is False


# --- 검색 키 (규칙 §9.1) -------------------------------------------------------


def test_search_terms_include_linked_and_mentioned_ids() -> None:
    record = parse_issue_backup(_payload())
    assert "AP-5500" in record.search_terms
    assert "AP-428" in record.search_terms  # 발생원인 본문에서 발견


def test_search_terms_exclude_the_issue_itself() -> None:
    record = parse_issue_backup(_payload())
    assert "AP-1001" not in record.search_terms


def test_search_terms_pick_up_dicom_tags_and_commands() -> None:
    payload = _payload(
        attributes={"reproductionStep": {"type": "text/html", "value": "[Step]<br/>1. Command No. 0x1234 를 보낸다<br/>2. (0008,0018) 을 확인한다"}}
    )
    terms = build_search_terms(parse_issue_backup(payload))
    assert "(0008,0018)" in terms
    assert "Command 0x1234" in terms


def test_search_terms_pick_up_quoted_ui_labels() -> None:
    payload = _payload(attributes={"title": '"Save Dose" 버튼이 동작하지 않음'})
    assert "Save Dose" in build_search_terms(parse_issue_backup(payload))


def test_search_terms_are_deduplicated_case_insensitively() -> None:
    payload = _payload(attributes={"title": '"Save Dose" 와 "save dose" 가 같이 나온다'})
    terms = [term.casefold() for term in build_search_terms(parse_issue_backup(payload))]
    assert len(terms) == len(set(terms))


# --- Export 폴더 --------------------------------------------------------------


def test_load_issue_reads_a_backup_file(tmp_path: Path) -> None:
    path = tmp_path / "AP-1001" / "backup.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_payload(), ensure_ascii=False), encoding="utf-8")
    record = load_issue(path)
    assert record.issue_id == "AP-1001"
    assert record.source_path == str(path)


def test_list_exported_issues_finds_folders_with_backup(tmp_path: Path) -> None:
    for issue_id in ("AP-1001", "AP-1002"):
        path = tmp_path / issue_id / "backup.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(_payload(), ensure_ascii=False), encoding="utf-8")
    (tmp_path / "AP-1003").mkdir()  # backup.json 없음
    assert list_exported_issues(tmp_path) == ["AP-1001", "AP-1002"]


def test_missing_export_dir_is_not_an_error(tmp_path: Path) -> None:
    assert list_exported_issues(tmp_path / "없는폴더") == []
    assert load_exported_issue(tmp_path / "없는폴더", "AP-1") is None


@pytest.mark.parametrize(
    ("version_id", "expected"),
    [("VXvue_1_1_0_001", "1.1.0.001"), ("Acme_2_0", "2.0"), ("", ""), ("NoVersion", "NoVersion")],
)
def test_version_label_is_normalised_for_document_matching(version_id: str, expected: str) -> None:
    assert normalize_version_label(version_id) == expected


# --- 실제 Export 폴더 opt-in 확인 ----------------------------------------------


def _real_export_dir() -> Path | None:
    config = resolve_config("vxvue")
    if config is None or not config.issue_source.export_dir:
        return None
    path = Path(config.issue_source.export_dir)
    return path if path.is_dir() else None


@pytest.mark.skipif(_real_export_dir() is None, reason="Polarion Export 폴더가 이 호스트에 없음")
def test_real_export_parses_every_issue_without_error() -> None:
    export_dir = _real_export_dir()
    assert export_dir is not None
    issue_ids = list_exported_issues(export_dir)
    assert issue_ids, "Export 폴더에 Issue 가 없다"
    for issue_id in issue_ids:
        record = load_exported_issue(export_dir, issue_id)
        assert record is not None and record.issue_id == issue_id


@pytest.mark.skipif(_real_export_dir() is None, reason="Polarion Export 폴더가 이 호스트에 없음")
def test_real_export_mostly_uses_the_standard_body_format() -> None:
    """규칙 §29 표준 형식 사용률. 낮아지면 Issue 작성 품질 문제이므로 눈에 보여야 한다."""
    export_dir = _real_export_dir()
    assert export_dir is not None
    records = [load_exported_issue(export_dir, issue_id) for issue_id in list_exported_issues(export_dir)]
    standard = [record for record in records if record and record.has_standard_body]
    assert len(standard) >= len(records) * 0.7, f"표준 형식 {len(standard)}/{len(records)}건"


@pytest.mark.skipif(_real_export_dir() is None, reason="Polarion Export 폴더가 이 호스트에 없음")
def test_real_export_classifies_most_issues_without_llm() -> None:
    export_dir = _real_export_dir()
    assert export_dir is not None
    records = [load_exported_issue(export_dir, issue_id) for issue_id in list_exported_issues(export_dir)]
    classified = [record for record in records if record and record.issue_type != TYPE_UNCLASSIFIED]
    assert len(classified) >= len(records) * 0.7, f"코드로 분류된 Issue {len(classified)}/{len(records)}건"
