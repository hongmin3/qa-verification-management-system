"""분석 종류별 작업 입력: 후보 압축·신규/수정 완료/Spec 판정/새 댓글 분석 입력 (TEST-QAINTEL-010).

가짜 Polarion·가짜 Claude 만 쓴다. 후보 압축은 `Corpus`·`build_item` 을 직접 불러 엔진을 재고,
파이프라인 전체 경로(사양서 로더 실패, 댓글 거르기, Regression 축, spec_doc 근거)는 `Harness` 로 돌려
가짜 Claude 가 받은 작업 입력(`h.payloads`)을 읽는다.

Validates: REQ-QAINTEL-011, REQ-QAINTEL-012, REQ-QAINTEL-013, REQ-QAINTEL-014, REQ-QAINTEL-015
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.core.document_schemas import SpecificationChunk
from app.modules.daily_qa.change_events import COMMENT, FIXED_ISSUE, NEW_ISSUE, SPEC_DECISION
from app.modules.daily_qa.evidence_validation import validate_finding
from app.modules.daily_qa.intelligence import DEFAULT_AXES, AnalysisTarget, Corpus, build_item, extract_terms
from app.modules.daily_qa.settings import IntelligenceSettings
from app.modules.daily_qa.tc_index import TcRow
from tests.daily_qa_fixtures import VXVUE_PROFILE, comment, issue_item, make_settings, srs_item
from tests.qa_intel_harness import DAY1, DAY2, DAY3, Harness

# -- 공용 도구 -------------------------------------------------------------------------


def mk_issue(issue_id: str, title: str = "Worklist 정렬 오류", *, occurred=(), target=(), rd: str = "", linked=(),
             description: str = "Worklist 에서 날짜 정렬이 반대로 표시된다", comments=None) -> dict:
    """공통 모델(정규화 뒤) 이슈. 엔진은 이 모양만 안다."""
    return {"id": issue_id, "title": title, "status": "open", "severity": "major", "rd_result": rd, "rd_result_raw": rd.lower(),
            "created": "2026-09-01T00:00:00Z", "updated": "2026-09-28T00:00:00Z", "description": description,
            "reproduction_step": "[Step] 1. Worklist 를 연다 2. 날짜 열을 누른다 [Expected Result] 오름차순 [Actual Result] 내림차순",
            "occurrence_cause": "", "action_details": "", "occurred_versions": list(occurred), "target_versions": list(target),
            "linked_ids": list(linked), "comment_ids": None, "comments": comments}


def mk_srs(srs_id: str, title: str = "Worklist 정렬", text: str = "날짜 열을 누르면 오름차순으로 정렬한다", old_id: str = "") -> dict:
    return {"id": srs_id, "old_id": old_id, "title": title, "status": "approved", "updated": "", "is_category": False, "text": text}


def mk_tc(number: int, srs_ref: str = "VP-10", workbook: str = "(TC) VXvue_TestCase.xlsx") -> TcRow:
    return TcRow(workbook=workbook, sheet="Viewer", row=number + 1, tc_id=f"TC_{number:03d}", srs_refs=[srs_ref],
                 title=f"Worklist 날짜 정렬 {number}", step="1. Worklist 날짜 열을 누른다.", expected="1. 오름차순 정렬된다.")


def mk_chunk(number: int, text: str = "Worklist 날짜 열 정렬은 오름차순이 기본이다") -> SpecificationChunk:
    return SpecificationChunk(chunk_id=f"spec-doc-p{number}", document_id="spec-doc", page=number, heading="Worklist 정렬", text=text)


def cfg_with(tmp_path, *, tc_limit: int = 10, profile=VXVUE_PROFILE, **intel):
    return make_settings(tmp_path / "root", tmp_path / "ws", tc_candidate_limit=tc_limit, profile=profile,
                         intelligence=IntelligenceSettings(**intel))


def corpus_of(issues, srs=(), tcs=(), chunks=(), profile=VXVUE_PROFILE) -> Corpus:
    return Corpus(profile, list(issues), list(srs), list(tcs), list(chunks), {"spec-doc": "VXvue 사양서"} if chunks else {})


def target(kind: str, entity_id: str, events=None) -> AnalysisTarget:
    events = events if events is not None else [{"id": 1, "event_type": "ISSUE_CREATED"}]
    return AnalysisTarget(kind, entity_id, [event.get("id") for event in events], events)


def item_for(kind, entity_id, corpus, cfg, events=None, comments_loader=None):
    known = corpus.known()
    item = build_item(target(kind, entity_id, events), corpus, known, cfg, comments_loader=comments_loader)
    return item, known


def issue_ids(item) -> list[str]:
    return [entry["id"] for entry in item["candidates"]["issues"]]


# -- REQ-QAINTEL-011 후보 압축: 개수 상한 --------------------------------------------------


@pytest.mark.parametrize("others, expected", [(2, 2), (3, 3), (4, 3), (0, 0)],
                         ids=["under-limit", "exactly-limit", "limit-plus-one", "empty-pool"])
def test_similar_issue_candidates_never_exceed_configured_limit(tmp_path, others, expected):
    cfg = cfg_with(tmp_path, issue_candidates=3)
    issues = [mk_issue("VP-900")] + [mk_issue(f"VP-{100 + number}") for number in range(others)]
    item, _ = item_for(NEW_ISSUE, "VP-900", corpus_of(issues), cfg)
    assert len(item["candidates"]["issues"]) == expected
    assert "VP-900" not in issue_ids(item)


@pytest.mark.parametrize("pool, limit", [(6, 6), (7, 6), (3, 6)], ids=["exactly-limit", "limit-plus-one", "under-limit"])
def test_srs_spec_doc_and_tc_candidates_never_exceed_their_limits(tmp_path, pool, limit):
    cfg = cfg_with(tmp_path, tc_limit=limit, spec_candidates=limit, spec_doc_candidates=limit)
    corpus = corpus_of([mk_issue("VP-900", rd="FIXED", linked=("VP-10",))],
                       srs=[mk_srs(f"VP-{10 + number}") for number in range(pool)],
                       tcs=[mk_tc(number) for number in range(pool)], chunks=[mk_chunk(number) for number in range(pool)])
    item, _ = item_for(FIXED_ISSUE, "VP-900", corpus, cfg, events=[{"id": 1, "event_type": "ISSUE_RD_RESULT_CHANGED"}])
    candidates = item["candidates"]
    assert 0 < len(candidates["srs"]) <= min(pool, limit)
    assert 0 < len(candidates["spec_docs"]) <= min(pool, limit)
    assert len(candidates["tcs"]) == min(pool, limit)


def test_new_issue_tc_candidates_are_capped_at_eight_even_if_setting_is_larger(tmp_path):
    cfg = cfg_with(tmp_path, tc_limit=15)
    corpus = corpus_of([mk_issue("VP-900")], tcs=[mk_tc(number) for number in range(12)])
    item, _ = item_for(NEW_ISSUE, "VP-900", corpus, cfg)
    assert len(item["candidates"]["tcs"]) == 8


def test_zero_spec_doc_limit_sends_no_spec_doc(tmp_path):
    cfg = cfg_with(tmp_path, spec_doc_candidates=0)
    item, known = item_for(NEW_ISSUE, "VP-900", corpus_of([mk_issue("VP-900")], chunks=[mk_chunk(1)]), cfg)
    assert item["candidates"]["spec_docs"] == []
    assert known.spec_refs == set()


# -- 자기 자신 제외 -----------------------------------------------------------------------


def test_target_issue_is_excluded_even_when_it_is_the_best_textual_match(tmp_path):
    cfg = cfg_with(tmp_path, issue_candidates=2)
    # 대상 본문이 자기 번호를 적고 있어도 검색어에서 빠지고, 후보에서도 빠진다.
    me = mk_issue("VP-900", description="VP-900 재현: Worklist 에서 날짜 정렬이 반대로 표시된다")
    others = [mk_issue("VP-101"), mk_issue("VP-102", title="Study 목록 필터", description="필터 초기화 안 됨")]
    item, _ = item_for(NEW_ISSUE, "VP-900", corpus_of([me, *others]), cfg)
    assert sorted(issue_ids(item)) == ["VP-101", "VP-102"]
    assert "VP-900" not in item["search"]["terms"]


def test_other_issue_that_mentions_target_number_is_still_a_candidate(tmp_path):
    cfg = cfg_with(tmp_path, issue_candidates=5)
    me = mk_issue("VP-900")
    mention = mk_issue("VP-101", title="VP-900 과 같은 증상", description="Worklist 날짜 정렬")
    item, _ = item_for(FIXED_ISSUE, "VP-900", corpus_of([me, mention]), cfg,
                       events=[{"id": 1, "event_type": "ISSUE_RD_RESULT_CHANGED"}])
    assert issue_ids(item) == ["VP-101"]


def test_only_target_in_pool_gives_empty_candidates_without_error(tmp_path):
    cfg = cfg_with(tmp_path)
    item, _ = item_for(NEW_ISSUE, "VP-900", corpus_of([mk_issue("VP-900")]), cfg)
    assert item["candidates"] == {"issues": [], "srs": [], "spec_docs": [], "tcs": []}


def test_target_missing_from_today_snapshot_returns_none(tmp_path):
    cfg = cfg_with(tmp_path)
    for kind in (NEW_ISSUE, FIXED_ISSUE, SPEC_DECISION, COMMENT):
        item, _ = item_for(kind, "VP-404", corpus_of([mk_issue("VP-900")]), cfg)
        assert item is None, kind


# -- 같은 버전 우선 -----------------------------------------------------------------------


def test_same_occurred_or_target_version_candidates_come_first(tmp_path):
    cfg = cfg_with(tmp_path, issue_candidates=5)
    me = mk_issue("VP-900", occurred=("2.1.0",), target=("2.2.0",))
    # 가장 비슷한 후보(본문이 대상과 같음)는 다른 버전이고, 덜 비슷한 두 후보가 같은 버전이다.
    best_text_other_version = mk_issue("VP-101", occurred=("1.0.0",))
    weak_same_occurred = mk_issue("VP-102", title="Worklist 표시", description="목록 글꼴이 작다", occurred=("2.1.0",))
    weak_same_target = mk_issue("VP-103", title="Worklist 열 너비", description="열 너비가 초기화된다", target=("2.2.0",))
    unrelated = mk_issue("VP-104", title="로그인", description="비밀번호 만료 안내", occurred=("0.9.0",))
    pool = [best_text_other_version, unrelated, weak_same_occurred, weak_same_target]
    item, _ = item_for(NEW_ISSUE, "VP-900", corpus_of([me, *pool]), cfg)
    ids = issue_ids(item)
    # 대조군: 버전이 겹치지 않는 대상이면 검색 순서 그대로다. 같은 버전 후보만 앞으로 옮겨야 한다.
    control, _ = item_for(NEW_ISSUE, "VP-900", corpus_of([mk_issue("VP-900", occurred=("9.9.9",)), *pool]), cfg)
    base = issue_ids(control)
    same = {"VP-102", "VP-103"}
    expected = [value for value in base if value in same] + [value for value in base if value not in same]
    assert base != expected, "대조군이 이미 같은 버전 후보를 앞에 두면 이 테스트는 아무것도 재지 못한다"
    assert ids == expected
    for entry in item["candidates"]["issues"]:
        assert ("같은 버전" in entry["match"]) == (entry["id"] in {"VP-102", "VP-103"})


def test_target_without_versions_keeps_retrieval_order_and_marks_nothing(tmp_path):
    cfg = cfg_with(tmp_path, issue_candidates=5)
    pool = [mk_issue("VP-101", occurred=("2.1.0",)), mk_issue("VP-102", title="로그인", description="만료", target=("2.2.0",)),
            mk_issue("VP-103", title="Worklist 열 너비", description="열 너비가 초기화된다")]
    no_versions, _ = item_for(NEW_ISSUE, "VP-900", corpus_of([mk_issue("VP-900"), *pool]), cfg)
    # 버전이 아무 후보와도 겹치지 않으면 검색 순서가 그대로다.
    no_overlap, _ = item_for(NEW_ISSUE, "VP-900", corpus_of([mk_issue("VP-900", occurred=("9.9.9",)), *pool]), cfg)
    assert issue_ids(no_versions) == issue_ids(no_overlap)
    assert sorted(issue_ids(no_versions)) == ["VP-101", "VP-102", "VP-103"]
    for item in (no_versions, no_overlap):
        assert all("같은 버전" not in entry["match"] for entry in item["candidates"]["issues"])


# -- 걸린 이유 -----------------------------------------------------------------------------


def test_every_candidate_carries_a_match_reason(tmp_path):
    cfg = cfg_with(tmp_path)
    me = mk_issue("VP-900", rd="FIXED", linked=("VP-10",), description="VP-10 에 따라 Worklist 날짜 정렬이 반대로 표시된다")
    corpus = corpus_of([me, mk_issue("VP-101"), mk_issue("VP-102", title="VP-10 관련 표시")],
                       srs=[mk_srs("VP-10", old_id="01-02-03"), mk_srs("VP-11", title="검색")],
                       tcs=[mk_tc(1), mk_tc(2, srs_ref="VP-11")], chunks=[mk_chunk(1), mk_chunk(2, "로그인 화면")])
    item, _ = item_for(FIXED_ISSUE, "VP-900", corpus, cfg, events=[{"id": 1, "event_type": "ISSUE_RD_RESULT_CHANGED"}])
    for group in ("issues", "srs", "spec_docs", "tcs"):
        entries = item["candidates"][group]
        assert entries, group
        assert all(str(entry.get("match") or "").strip() for entry in entries), group
    srs_reasons = {entry["id"]: entry["match"] for entry in item["candidates"]["srs"]}
    assert srs_reasons["VP-10"] == "'VP-10' 정확 일치"
    for group in ("issues", "srs", "spec_docs"):
        for entry in item["candidates"][group]:
            reason = entry["match"].replace(" · 같은 버전", "")
            assert reason.endswith("정확 일치") or reason.startswith("용어 유사도 "), (group, reason)
    tc_reasons = {entry["tc_id"]: entry["match"] for entry in item["candidates"]["tcs"]}
    assert tc_reasons["TC_001"] == "SRS 번호로 연결"


# -- 사양서 조각 ref 등록 (결과 검증 기준) ---------------------------------------------------


def test_spec_doc_refs_sent_in_input_are_registered_and_accepted_as_evidence(tmp_path):
    cfg = cfg_with(tmp_path, spec_doc_candidates=1)
    corpus = corpus_of([mk_issue("VP-900")], chunks=[mk_chunk(1), mk_chunk(2, "로그인 비밀번호 만료 안내")])
    item, known = item_for(NEW_ISSUE, "VP-900", corpus, cfg)
    sent = item["candidates"]["spec_docs"]
    assert len(sent) == 1
    sent_ref = sent[0]["ref"]
    assert sent_ref in {"spec:spec-doc-p1", "spec:spec-doc-p2"}
    assert known.spec_refs == {sent_ref}
    page = sent_ref.rsplit("p", 1)[1]
    assert sent[0]["location"].startswith(f"VXvue 사양서 · p.{page}")
    not_sent = ({"spec:spec-doc-p1", "spec:spec-doc-p2"} - {sent_ref}).pop()

    finding = {"subject": "VP-900", "summary": "사양 위반", "verdict": "SPEC_VIOLATION", "confidence": "Review Needed",
               "evidence": [{"source_type": "spec_doc", "ref": sent_ref, "location": "보낸 조각"},
                            # 코퍼스에는 있지만 입력으로 보내지 않은 조각은 근거로 쓸 수 없다.
                            {"source_type": "spec_doc", "ref": not_sent, "location": "보내지 않은 조각"}]}
    validated, reason = validate_finding(finding, NEW_ISSUE, known, {"VP-900"})
    assert validated is not None, reason
    assert [entry["ref"] for entry in validated["evidence"]] == [sent_ref]


# -- REQ-QAINTEL-012/013/014 분석 종류별 입력 모양 -------------------------------------------


def test_fixed_issue_input_carries_product_regression_axes(tmp_path):
    cfg = cfg_with(tmp_path)
    item, _ = item_for(FIXED_ISSUE, "VP-900", corpus_of([mk_issue("VP-900", rd="FIXED")]), cfg,
                       events=[{"id": 1, "event_type": "ISSUE_RD_RESULT_CHANGED"}])
    assert item["regression_axes"] == list(VXVUE_PROFILE.regression_axes)
    assert "GENERATOR" in item["regression_axes"]


def test_fixed_issue_without_product_axes_uses_common_axes(tmp_path):
    profile = replace(VXVUE_PROFILE, regression_axes=())
    cfg = cfg_with(tmp_path, profile=profile)
    item, _ = item_for(FIXED_ISSUE, "VP-900", corpus_of([mk_issue("VP-900", rd="FIXED")], profile=profile), cfg,
                       events=[{"id": 1, "event_type": "ISSUE_RD_RESULT_CHANGED"}])
    assert item["regression_axes"] == list(DEFAULT_AXES)
    assert "GENERATOR" not in item["regression_axes"]


def test_new_issue_and_spec_decision_inputs_have_no_regression_axes(tmp_path):
    cfg = cfg_with(tmp_path)
    corpus = corpus_of([mk_issue("VP-900", rd="SPEC")], tcs=[mk_tc(1)])
    new_item, _ = item_for(NEW_ISSUE, "VP-900", corpus, cfg)
    spec_item, _ = item_for(SPEC_DECISION, "VP-900", corpus, cfg, events=[{"id": 1, "event_type": "ISSUE_RD_RESULT_CHANGED"}])
    assert "regression_axes" not in new_item and "regression_axes" not in spec_item
    # Spec 판정 분석은 TC 초안을 만들지 않으므로 TC 후보도 보내지 않는다 (REQ-QAINTEL-014 입력).
    assert "tcs" not in spec_item["candidates"]
    assert "tcs" in new_item["candidates"]


def test_fixed_and_spec_decision_inputs_include_loaded_comments_and_register_their_ids(tmp_path):
    cfg = cfg_with(tmp_path)
    loaded = [{"id": "c-1", "created": "2026-09-28T00:00:00Z", "text": "원인은 정렬 비교 함수 부호 오류입니다"}]
    calls: list[str] = []

    def loader(issue_id):
        calls.append(issue_id)
        return loaded

    corpus = corpus_of([mk_issue("VP-900", rd="FIXED")])
    for kind in (FIXED_ISSUE, SPEC_DECISION):
        item, known = item_for(kind, "VP-900", corpus, cfg, events=[{"id": 1, "event_type": "ISSUE_RD_RESULT_CHANGED"}],
                               comments_loader=loader)
        assert item["issue"]["comments"] == loaded
        assert known.comment_ids == {"c-1"}
    new_item, _ = item_for(NEW_ISSUE, "VP-900", corpus, cfg, comments_loader=loader)
    assert "comments" not in new_item["issue"]
    assert calls == ["VP-900", "VP-900"]


def test_fixed_issue_without_linked_srs_still_gets_search_based_tc_candidates(tmp_path):
    cfg = cfg_with(tmp_path)
    corpus = corpus_of([mk_issue("VP-900", rd="FIXED", linked=("VP-77",))], srs=[mk_srs("VP-10")], tcs=[mk_tc(1), mk_tc(2)])
    item, _ = item_for(FIXED_ISSUE, "VP-900", corpus, cfg, events=[{"id": 1, "event_type": "ISSUE_RD_RESULT_CHANGED"}])
    tcs = item["candidates"]["tcs"]
    assert len(tcs) == 2
    assert all(entry["match"].startswith("같은 기능(검색)") for entry in tcs)


# -- REQ-QAINTEL-015 새 댓글 입력 (단위) ---------------------------------------------------


def test_comment_input_contains_only_significant_new_comments_and_registers_them(tmp_path):
    cfg = cfg_with(tmp_path)
    old = {"id": "c1", "created": "2026-09-01T00:00:00Z", "text": "예전 댓글: 재현 조건은 Study 100건 이상"}
    earlier = [{"id": f"e{number}", "created": f"2026-09-0{number}T00:00:00Z", "text": f"예전 댓글 {number}"} for number in range(1, 8)]
    meaningful = {"id": "c2", "created": "2026-09-29T00:00:00Z", "text": "원인은 캐시 초기화 누락으로 확인되었습니다."}
    noise = {"id": "c3", "created": "2026-09-29T00:00:00Z", "text": "확인했습니다."}
    issue = mk_issue("VP-900", comments=[*earlier, old, meaningful, noise])
    event = {"id": 7, "event_type": "ISSUE_COMMENT_ADDED", "after": {"comments": [meaningful, noise], "significant_ids": ["c2"]}}
    item, known = item_for(COMMENT, "VP-900", corpus_of([issue]), cfg, events=[event])
    assert [entry["id"] for entry in item["new_comments"]] == ["c2"]
    assert known.comment_ids == {"c2"}
    # 이전 댓글은 최근 5개만, 새 댓글은 빠진다.
    earlier_ids = [entry["id"] for entry in item["earlier_comments"]]
    assert len(earlier_ids) == 5
    assert "c1" in earlier_ids and "e1" not in earlier_ids
    assert "c2" not in [entry["id"] for entry in item["earlier_comments"]]


def test_comment_event_without_significant_ids_sends_no_new_comment(tmp_path):
    cfg = cfg_with(tmp_path)
    noise = {"id": "c3", "created": "2026-09-29T00:00:00Z", "text": "확인했습니다."}
    event = {"id": 7, "event_type": "ISSUE_COMMENT_ADDED", "after": {"comments": [noise], "significant_ids": []}}
    item, known = item_for(COMMENT, "VP-900", corpus_of([mk_issue("VP-900", comments=[noise])]), cfg, events=[event])
    assert item["new_comments"] == []
    assert known.comment_ids == set()


# -- REQ-QAINTEL-011 1번 검색어 뽑기 ---------------------------------------------------------


def test_extract_terms_finds_identifiers_in_korean_and_english_text():
    text = ("VP-1234 과 SRS-55 를 보면 Legacy 01-02-03 항목에서 (0010,0020) Tag 가 비고 Command 0x1F 를 보낸 뒤 "
            "E1234 와 0xDEAD 오류가 났다. 화면 문구 \"저장 실패\" 와 'Save failed' 가 보인다.")
    terms = extract_terms(text)
    for expected in ("VP-1234", "SRS-55", "01-02-03", "(0010,0020)", "Command 0x1F", "E1234", "0xDEAD", "저장 실패", "Save failed"):
        assert expected in terms, expected


def test_extract_terms_does_not_make_terms_from_plain_prose():
    # 식별자·따옴표 문구가 없는 일반 문장은 Exact 검색어를 만들지 않는다 (BM25 가 맡는다).
    assert extract_terms("Worklist 에서 날짜 정렬이 반대로 표시된다", "the list is sorted in reverse") == []


def test_extract_terms_handles_empty_input():
    assert extract_terms() == []
    assert extract_terms("", None) == []
    assert extract_terms("", extra=("VP-1",)) == ["VP-1"]


def test_extract_terms_dedupes_case_insensitively_and_keeps_extra_first():
    terms = extract_terms("'Save Failed' 와 'save failed', VP-10 VP-10", extra=("VP-10", "VP-77"))
    assert terms[:2] == ["VP-10", "VP-77"]
    assert terms.count("VP-10") == 1
    assert [term.casefold() for term in terms].count("save failed") == 1


def test_extract_terms_excludes_the_target_number_and_one_char_quotes():
    terms = extract_terms("VP-900 은 VP-901 과 같다. 'a' 는 무시", extra=("VP-900",), exclude="VP-900")
    assert "VP-900" not in terms
    assert "VP-901" in terms
    assert "a" not in terms


# -- 파이프라인 경로 (Harness) ----------------------------------------------------------------


def _baseline(h: Harness, **issue) -> None:
    h.srs[:] = [srs_item("VP-10", "01-01", "Worklist 정렬", "날짜 열을 누르면 오름차순으로 정렬한다")]
    h.issues[:] = [issue_item("VP-100", "2026-09-28T00:00:00Z", ["VP-10"], **issue)]


def _payload(h: Harness, analysis_type: str) -> dict:
    found = [payload for payload in h.payloads if payload["analysis_type"] == analysis_type]
    assert found, [payload["analysis_type"] for payload in h.payloads]
    return found[-1]


def test_fixed_issue_task_payload_carries_regression_axes_end_to_end(tmp_path):
    h = Harness(tmp_path)
    _baseline(h, review="", status="open")
    h.run(DAY1)
    h.issues[0] = issue_item("VP-100", "2026-09-29T09:00:00Z", ["VP-10"], review="lab_fixed", status="resolved",
                             cause="정렬 비교 부호 오류", action="비교 함수 수정")
    h.run(DAY2)
    item = _payload(h, FIXED_ISSUE)["items"][0]
    assert item["target"] == "VP-100"
    assert item["regression_axes"] == list(VXVUE_PROFILE.regression_axes)


def test_comment_payload_contains_only_meaningful_new_comments(tmp_path):
    h = Harness(tmp_path)
    _baseline(h, review="", status="open", comment_ids=["c1"])
    h.comments = {"VP-100": [comment("c1", "원인은 아직 모릅니다. 재현 조건을 더 확인하겠습니다.", "2026-09-28T01:00:00Z")]}
    h.run(DAY1)
    h.issues[0] = issue_item("VP-100", "2026-09-29T09:00:00Z", ["VP-10"], review="", status="open",
                             comment_ids=["c1", "c2", "c3", "c4"])
    h.comments["VP-100"] += [
        comment("c2", "원인은 캐시 초기화 누락으로 확인되었습니다."),
        comment("c3", "in progress ..........."),     # 길이는 넘지만 기본 잡음 모양
        comment("c4", "확인"),                          # 너무 짧다
    ]
    h.run(DAY2)
    item = _payload(h, COMMENT)["items"][0]
    assert [entry["id"] for entry in item["new_comments"]] == ["c2"]
    assert "c1" in [entry["id"] for entry in item["earlier_comments"]]

    # 새 댓글이 없는 다음 날은 댓글 분석을 부르지 않는다 (이미 본 댓글은 다시 보내지 않는다).
    h.payloads.clear()
    h.issues[0] = issue_item("VP-100", "2026-09-30T09:00:00Z", ["VP-10"], review="", status="open",
                             comment_ids=["c1", "c2", "c3", "c4"])
    h.run(DAY3)
    assert [payload["analysis_type"] for payload in h.payloads if payload["analysis_type"] == COMMENT] == []


def test_product_comment_noise_patterns_filter_comments(tmp_path):
    profile = replace(VXVUE_PROFILE, comment_noise_patterns=(r"빌드 [0-9.]+ 에 반영되었습니다.*",))
    h = Harness(tmp_path, profile=profile)
    _baseline(h, review="", status="open", comment_ids=[])
    h.run(DAY1)
    h.issues[0] = issue_item("VP-100", "2026-09-29T09:00:00Z", ["VP-10"], review="", status="open", comment_ids=["c7", "c8"])
    h.comments = {"VP-100": [comment("c7", "빌드 2.1.0.15 에 반영되었습니다. 확인 부탁드립니다."),
                             comment("c8", "재현 조건: Study 가 100건 이상일 때만 정렬이 뒤집힙니다.")]}
    h.run(DAY2)
    item = _payload(h, COMMENT)["items"][0]
    assert [entry["id"] for entry in item["new_comments"]] == ["c8"]


def _new_issue_day2(h: Harness) -> None:
    _baseline(h, review="", status="open")
    h.run(DAY1, spec_chunks_loader=lambda: ([], {}, []))
    h.issues.append(issue_item("VP-200", "2026-09-29T09:00:00Z", ["VP-10"], review="", status="open",
                               title="Worklist 날짜 정렬 반대", description="Worklist 날짜 정렬이 반대로 표시된다"))
    h.script["VP-200"] = {"verdict": "SPEC_VIOLATION", "evidence": [
        {"source_type": "spec_doc", "ref": "spec:spec-doc-p1", "location": "VXvue 사양서 p.1", "validity": "Current"},
        {"source_type": "spec_doc", "ref": "spec:made-up", "location": "없는 조각", "validity": "Current"}]}


def test_spec_loader_exception_is_recorded_as_unreadable_and_run_continues(tmp_path):
    h = Harness(tmp_path)
    _new_issue_day2(h)

    def broken():
        raise RuntimeError("knowledge db locked")

    outcome = h.run(DAY2, spec_chunks_loader=broken)
    payload = _payload(h, NEW_ISSUE)
    assert payload["unreadable_documents"] == ["사양서 문서 목록: RuntimeError"]
    assert payload["items"][0]["candidates"]["spec_docs"] == []
    assert payload["items"][0]["candidates"]["srs"], "SRS 후보만으로 진행한다"
    assert outcome["status"] in ("SUCCESS", "PARTIAL")
    assert outcome["stages"][NEW_ISSUE]["status"] == "ok"
    events = h.events(run_id=None, statuses=("done",))
    assert any(event["entity_id"] == "VP-200" for event in events)


def test_loader_reported_unreadable_document_names_reach_task_input(tmp_path):
    h = Harness(tmp_path)
    _new_issue_day2(h)
    h.run(DAY2, spec_chunks_loader=lambda: ([mk_chunk(1)], {"spec-doc": "VXvue 사양서"}, ["(사양서) 깨진 파일.pdf"]))
    payload = _payload(h, NEW_ISSUE)
    assert payload["unreadable_documents"] == ["(사양서) 깨진 파일.pdf"]
    assert [entry["ref"] for entry in payload["items"][0]["candidates"]["spec_docs"]] == ["spec:spec-doc-p1"]


def test_unreadable_document_name_is_written_to_stage_note(tmp_path):
    h = Harness(tmp_path)
    _new_issue_day2(h)
    outcome = h.run(DAY2, spec_chunks_loader=lambda: ([], {}, ["(사양서) 깨진 파일.pdf"]))
    notes = " ".join(str(stage.get("note") or "") for stage in outcome["stages"].values())
    assert "(사양서) 깨진 파일.pdf" in notes


def test_spec_loader_exception_is_written_to_stage_note_and_analysis_continues(tmp_path):
    """사양서 목록 자체를 못 읽어도 SRS 후보만으로 분석하고, 단계 비고에 그 사실을 남긴다 (REQ-QAINTEL-011 6번)."""
    h = Harness(tmp_path)
    _new_issue_day2(h)

    def broken():
        raise OSError("disk")

    outcome = h.run(DAY2, spec_chunks_loader=broken)
    stage = outcome["stages"][NEW_ISSUE]
    assert "읽지 못한 사양서: 사양서 문서 목록: OSError" in stage["note"]
    assert stage["status"] == "ok" and stage["counts"]["done"] == 1
    assert _payload(h, NEW_ISSUE)["items"][0]["candidates"]["spec_docs"] == []


def test_stage_note_has_no_unreadable_text_when_all_documents_read(tmp_path):
    h = Harness(tmp_path)
    _new_issue_day2(h)
    outcome = h.run(DAY2, spec_chunks_loader=lambda: ([mk_chunk(1)], {"spec-doc": "VXvue 사양서"}, []))
    assert all("읽지 못한 사양서" not in str(stage.get("note") or "") for stage in outcome["stages"].values())


def test_spec_doc_evidence_from_input_survives_validation_end_to_end(tmp_path):
    h = Harness(tmp_path)
    _new_issue_day2(h)
    h.run(DAY2, spec_chunks_loader=lambda: ([mk_chunk(1)], {"spec-doc": "VXvue 사양서"}, []))
    event = next(event for event in h.events() if event["entity_id"] == "VP-200")
    assert event["analysis_status"] == "done" and event["finding_ids"]
    finding = h.store.get_finding(event["finding_ids"][0])
    refs = [entry.get("ref") for entry in finding["evidence"]]
    assert refs == ["spec:spec-doc-p1"]


def test_knowledge_documents_disabled_skips_loader(tmp_path):
    h = Harness(tmp_path, intelligence=IntelligenceSettings(use_knowledge_documents=False))
    _new_issue_day2(h)
    called: list[int] = []
    h.run(DAY2, spec_chunks_loader=lambda: called.append(1) or ([mk_chunk(1)], {}, []))
    payload = _payload(h, NEW_ISSUE)
    assert called == []
    assert payload["items"][0]["candidates"]["spec_docs"] == []
    assert payload["unreadable_documents"] == []
