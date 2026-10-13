# Validates: REQ-SYNC-003
import sys
import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytest.importorskip('bs4', reason='SRS 수집 PC의 선택 라이브러리')

from app.modules.knowledge.srs_crawler import render_recovery
from app.modules.knowledge.srs_crawler.pdf import PdfResult


def _rec(uid, title="Title", content_html="<p>hello</p>"):
    return {
        "uid": uid,
        "id": uid.split("/")[-1],
        "project_id": "VXvue",
        "title": title,
        "old_id": "01-10-10",
        "status": "draft",
        "content_html": content_html,
        "is_category": False,
        "linked_work_items": [],
        "parent_id": None,
        "attachments_meta": [],
        "comments": [],
    }


def test_plaintext_fallback_preserves_text_and_images():
    rec = _rec(
        "VXvue/VP-1",
        content_html='<ul><ul><li>keep this text</li></ul></ul><img src="file:///C:/img/a.png"/>',
    )
    render_recovery._plaintext_fallback(rec)

    assert "keep this text" in rec["content_html"]
    assert 'src="file:///C:/img/a.png"' in rec["content_html"]
    assert rec["render_fallback_applied"] is True
    assert "렌더링 오류" in rec["content_html"]


def test_isolate_finds_the_single_bad_record(tmp_path, monkeypatch):
    records = [_rec(f"VXvue/VP-{i}") for i in range(8)]
    bad_uid = "VXvue/VP-5"

    bad_id = bad_uid.split("/")[-1]

    def fake_html_to_pdf(html_path, pdf_path, timeout_seconds=180):
        html_content = html_path.read_text(encoding="utf-8")
        ok = f'"srs-{bad_id}"' not in html_content
        return PdfResult(path=pdf_path, size_bytes=100 if ok else 0, page_count=5 if ok else 0, ok=ok, error=None if ok else "timeout after 180s")

    monkeypatch.setattr(render_recovery, "html_to_pdf", fake_html_to_pdf)

    problematic: list[dict] = []
    render_recovery._isolate(records, "Test Group", "VXvue", tmp_path, 0, 8, 180, problematic)

    assert [r["uid"] for r in problematic] == [bad_uid]


def test_render_group_pdf_with_recovery_isolates_and_recovers(tmp_path, monkeypatch):
    records = [_rec(f"VXvue/VP-{i}") for i in range(6)]
    bad_uid = "VXvue/VP-3"
    from app.modules.knowledge.srs_crawler.partition import FileGroup

    group = FileGroup(file_key="spec_test", display_name="Test Group", records=records)

    def fake_html_to_pdf(html_path, pdf_path, timeout_seconds=180):
        html_content = html_path.read_text(encoding="utf-8")
        # 격리(fallback) 이후에는 bad_uid의 content_html이 place-holder로 바뀌어
        # 원래의 트리거 텍스트("BAD_TRIGGER")가 사라지므로 성공해야 한다.
        ok = "BAD_TRIGGER" not in html_content
        return PdfResult(path=pdf_path, size_bytes=100 if ok else 0, page_count=5 if ok else 0, ok=ok, error=None if ok else "timeout after 180s")

    for r in records:
        if r["uid"] == bad_uid:
            # 트리거는 태그/속성 자체(예: 깨진 구조)로 시뮬레이션한다 - 폴백은 텍스트는
            # 보존하고 태그/속성만 제거하므로, 텍스트 자체를 트리거로 쓰면 폴백 후에도
            # 남아있어 오탐이 된다.
            r["content_html"] = '<div class="BAD_TRIGGER">some text</div>'

    monkeypatch.setattr(render_recovery, "html_to_pdf", fake_html_to_pdf)

    html_dir = tmp_path / "html"
    html_dir.mkdir()
    pdf_path = tmp_path / "out.pdf"

    result, isolated = render_recovery.render_group_pdf_with_recovery(
        group, project_label="VXvue", html_dir=html_dir, pdf_path=pdf_path
    )

    assert result.ok is True
    assert [r["uid"] for r in isolated] == [bad_uid]
    fixed_rec = next(r for r in records if r["uid"] == bad_uid)
    assert "BAD_TRIGGER" not in fixed_rec["content_html"]
    assert fixed_rec["render_fallback_applied"] is True


# ---------- known_problem_srs 캐시 ----------


def _state(tmp_path):
    from app.modules.knowledge.srs_crawler.problem_state import ProblemState

    return ProblemState.load(tmp_path)


def _always_ok(html_path, pdf_path, timeout_seconds=180):
    return PdfResult(path=pdf_path, size_bytes=100, page_count=5, ok=True, error=None)


def _fail_on(marker):
    def fake(html_path, pdf_path, timeout_seconds=180):
        ok = marker not in html_path.read_text(encoding="utf-8")
        return PdfResult(
            path=pdf_path,
            size_bytes=100 if ok else 0,
            page_count=5 if ok else 0,
            ok=ok,
            error=None if ok else "timeout after 180s",
        )

    return fake


def _group(records):
    from app.modules.knowledge.srs_crawler.partition import FileGroup

    return FileGroup(file_key="spec_test", display_name="Test Group", records=records)


def _run(group, tmp_path, monkeypatch, fake, **kwargs):
    monkeypatch.setattr(render_recovery, "html_to_pdf", fake)
    html_dir = tmp_path / "html"
    html_dir.mkdir(exist_ok=True)
    return render_recovery.render_group_pdf_with_recovery(
        group, project_label="VXvue", html_dir=html_dir, pdf_path=tmp_path / "out.pdf", **kwargs
    )


def test_known_problem_with_unchanged_content_skips_bisection(tmp_path, monkeypatch):
    """본문이 그대로면 이분 탐색 없이 곧바로 서식 단순화가 적용된다."""
    bad = _rec("VXvue/VP-1277", content_html='<div class="BAD_TRIGGER">text</div>')
    records = [_rec("VXvue/VP-1"), bad]
    state = _state(tmp_path)
    state.record("VXvue/VP-1277", content_html=bad["content_html"], title=bad["title"])

    calls = []

    def counting(html_path, pdf_path, timeout_seconds=180):
        calls.append(html_path.name)
        return _fail_on("BAD_TRIGGER")(html_path, pdf_path, timeout_seconds)

    result, isolated = _run(
        _group(records), tmp_path, monkeypatch, counting,
        known_problem_srs={"VXvue/VP-1277"}, problem_state=state,
    )

    assert result.ok is True
    assert [r["uid"] for r in isolated] == ["VXvue/VP-1277"]
    assert isolated[0]["render_fallback_reason"] == "known_problem_cache"
    # 렌더링은 단 1회 - 이분 탐색(bisect_*)이 전혀 일어나지 않았다.
    assert calls == ["spec_test.html"]


def test_known_problem_with_changed_content_is_rechecked(tmp_path, monkeypatch):
    """본문이 수정되었으면 캐시를 무시하고 정상 렌더링을 재확인한다."""
    bad = _rec("VXvue/VP-1277", content_html="<p>이제는 서식이 단순해진 수정본</p>")
    state = _state(tmp_path)
    state.record("VXvue/VP-1277", content_html="<div class='BAD_TRIGGER'>예전 본문</div>", title=bad["title"])

    result, isolated = _run(
        _group([_rec("VXvue/VP-1"), bad]), tmp_path, monkeypatch, _always_ok,
        known_problem_srs={"VXvue/VP-1277"}, problem_state=state,
    )

    assert result.ok is True
    assert isolated == []
    assert bad.get("render_fallback_applied") is None
    # 정상 렌더링되었으므로 상태에서 제거되어 config 정리를 안내할 수 있다.
    assert "VXvue/VP-1277" not in state.entries


def test_recheck_flag_ignores_cache(tmp_path, monkeypatch):
    bad = _rec("VXvue/VP-1277", content_html='<div class="BAD_TRIGGER">text</div>')
    state = _state(tmp_path)
    state.record("VXvue/VP-1277", content_html=bad["content_html"], title=bad["title"])

    calls = []

    def counting(html_path, pdf_path, timeout_seconds=180):
        calls.append(html_path.name)
        return _fail_on("BAD_TRIGGER")(html_path, pdf_path, timeout_seconds)

    result, isolated = _run(
        _group([_rec("VXvue/VP-1"), bad]), tmp_path, monkeypatch, counting,
        known_problem_srs={"VXvue/VP-1277"}, problem_state=state, recheck_known=True,
    )

    assert result.ok is True
    assert [r["uid"] for r in isolated] == ["VXvue/VP-1277"]
    assert isolated[0]["render_fallback_reason"] == "bisect_timeout"
    # 캐시를 무시했으므로 이분 탐색이 실제로 수행되었다.
    assert any(name.startswith("bisect_") for name in calls)


def test_newly_found_problem_is_recorded_with_original_hash(tmp_path, monkeypatch):
    """신규 격리된 SRS는 fallback 적용 전의 원본 본문 해시로 기록된다."""
    from app.modules.knowledge.srs_crawler.problem_state import content_hash

    bad = _rec("VXvue/VP-9", content_html='<div class="BAD_TRIGGER">text</div>')
    original = bad["content_html"]
    state = _state(tmp_path)

    result, isolated = _run(
        _group([_rec("VXvue/VP-1"), bad]), tmp_path, monkeypatch, _fail_on("BAD_TRIGGER"),
        known_problem_srs=set(), problem_state=state,
    )

    assert result.ok is True
    assert [r["uid"] for r in isolated] == ["VXvue/VP-9"]
    assert state.entries["VXvue/VP-9"]["content_sha256"] == content_hash(original)


def test_problem_state_roundtrip_and_corrupt_file(tmp_path):
    from app.modules.knowledge.srs_crawler.problem_state import ProblemState

    st = ProblemState.load(tmp_path)
    st.record("VXvue/VP-1", content_html="<p>a</p>", title="T")
    st.save()
    assert ProblemState.load(tmp_path).is_still_valid("VXvue/VP-1", "<p>a</p>") is True
    assert ProblemState.load(tmp_path).is_still_valid("VXvue/VP-1", "<p>b</p>") is False

    (tmp_path / "render_problem_state.json").write_text("{ not json", encoding="utf-8")
    assert ProblemState.load(tmp_path).entries == {}


# ---------- '느린 그룹' (개별 문제 SRS 없음) 복구 ----------


def test_slow_group_retries_with_extended_timeout(tmp_path, monkeypatch):
    """이분 탐색에서 범인을 못 찾으면 실패가 아니라 시간제한을 늘려 재시도한다.

    실제로 Task Scheduler(우선순위 낮음) 실행에서 정상 그룹이 90초를 넘겨 실패 처리되던
    버그를 재현한다: 그룹 전체는 짧은 제한을 넘지만 절반씩 나누면 모두 통과한다.
    """
    records = [_rec(f"VXvue/VP-{i}") for i in range(8)]
    base_timeout = 90
    seen_timeouts = []

    def slow_but_healthy(html_path, pdf_path, timeout_seconds=180):
        seen_timeouts.append(timeout_seconds)
        n_records = html_path.read_text(encoding="utf-8").count('id="srs-VP-')
        # 전체(8건)는 짧은 제한에서 실패, 늘려주면 성공. 부분 집합은 항상 성공.
        ok = n_records < 8 or timeout_seconds > base_timeout
        return PdfResult(
            path=pdf_path,
            size_bytes=100 if ok else 0,
            page_count=120 if ok else 0,
            ok=ok,
            error=None if ok else f"timeout after {timeout_seconds}s",
        )

    result, isolated = _run(
        _group(records), tmp_path, monkeypatch, slow_but_healthy, timeout_seconds=base_timeout
    )

    assert result.ok is True, "느린 그룹은 실패가 아니라 시간제한 연장으로 복구되어야 한다"
    assert isolated == []
    # 어떤 SRS도 서식이 깎이지 않았다.
    assert all("render_fallback_applied" not in r for r in records)
    # 연장된 제한(90 * 3)으로 재시도한 흔적이 있다.
    assert max(seen_timeouts) == base_timeout * render_recovery.SLOW_GROUP_TIMEOUT_FACTOR


def test_slow_group_retry_failure_is_reported_as_failure(tmp_path, monkeypatch):
    """시간제한을 늘려도 안 되면 그때는 정직하게 실패로 보고한다."""
    records = [_rec(f"VXvue/VP-{i}") for i in range(8)]

    def never_ok_for_full_group(html_path, pdf_path, timeout_seconds=180):
        n_records = html_path.read_text(encoding="utf-8").count('id="srs-VP-')
        ok = n_records < 8
        return PdfResult(
            path=pdf_path,
            size_bytes=100 if ok else 0,
            page_count=5 if ok else 0,
            ok=ok,
            error=None if ok else f"timeout after {timeout_seconds}s",
        )

    result, isolated = _run(
        _group(records), tmp_path, monkeypatch, never_ok_for_full_group, timeout_seconds=90
    )

    assert result.ok is False
    assert isolated == []


# ---------- 캐시 키에 렌더링 파이프라인 해시 포함 ----------


def test_cache_invalidated_when_render_pipeline_changes(tmp_path, monkeypatch):
    """본문이 같아도 HTML 템플릿/인쇄 옵션이 바뀌면 캐시를 신뢰하지 않는다.

    템플릿 수정으로 폭주가 해소되었는데 본문 해시가 같아 캐시가 계속 적용되면
    개선이 영구히 반영되지 않으므로, 파이프라인 변경은 반드시 재확인해야 한다.
    """
    from app.modules.knowledge.srs_crawler import problem_state

    bad = _rec("VXvue/VP-1277", content_html='<div class="BAD_TRIGGER">text</div>')
    state = _state(tmp_path)
    state.record("VXvue/VP-1277", content_html=bad["content_html"], title=bad["title"])
    assert state.is_still_valid("VXvue/VP-1277", bad["content_html"]) is True

    # 렌더링 파이프라인이 바뀐 상황을 시뮬레이션한다.
    monkeypatch.setattr(problem_state, "render_pipeline_hash", lambda: "pipeline-changed")
    assert state.is_still_valid("VXvue/VP-1277", bad["content_html"]) is False


def test_legacy_state_entry_without_pipeline_hash_is_invalidated(tmp_path):
    """파이프라인 해시가 없던 구버전 상태 파일은 무효로 보고 재확인한다."""
    import json

    from app.modules.knowledge.srs_crawler.problem_state import ProblemState, content_hash

    (tmp_path / "render_problem_state.json").write_text(
        json.dumps({"VXvue/VP-1": {"content_sha256": content_hash("<p>a</p>")}}), encoding="utf-8"
    )
    assert ProblemState.load(tmp_path).is_still_valid("VXvue/VP-1", "<p>a</p>") is False


def test_render_pipeline_hash_is_deterministic_and_nonempty():
    from app.modules.knowledge.srs_crawler.problem_state import render_pipeline_hash

    h = render_pipeline_hash()
    assert len(h) == 64
    assert h == render_pipeline_hash()
