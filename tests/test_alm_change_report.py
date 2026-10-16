"""기간 안 기록을 하루씩 비교한 변경 목록 (SPEC REQ-ALMCHG-002)."""

from __future__ import annotations

from dataclasses import asdict

from app.modules.daily_qa.change_report import build_change_report, kst_day
from app.modules.daily_qa.snapshots import KIND_ISSUES, KIND_SRS, SnapshotStore
from app.modules.daily_qa.srs_snapshot import save_snapshot
from tests.daily_qa_fixtures import make_settings

SCHEDULE = {"enabled": True, "day_of_week": "*", "skip_holidays": False, "calendar": "kr", "extra": ()}


def issue(item_id="VP-1", **values):
    base = {"id": item_id, "title": f"{item_id} 제목", "status": "A", "severity": "", "rd_result_raw": "",
            "rd_result": "", "description": "설명", "reproduction_step": "", "occurrence_cause": "",
            "action_details": "", "occurred_versions": [], "target_versions": [], "linked_ids": [],
            "comment_ids": [], "comments": [], "user_names": {}}
    base.update(values)
    return base


def srs(item_id="VP-9", **values):
    base = {"id": item_id, "old_id": "", "title": f"{item_id} SRS", "status": "draft", "text": "본문 하나."}
    base.update(values)
    return base


def setup(tmp_path):
    cfg = make_settings(tmp_path, tmp_path / "ws")
    return cfg, SnapshotStore(root=tmp_path / "snap", legacy_srs_dir=tmp_path / "legacy")


def save(store, kind, day, items):
    store.save(kind, day, items, collected_at=f"{day}T04:00:00+09:00")


def build(cfg, store, since="2026-10-16", until="2026-10-19"):
    return build_change_report(cfg, store, since, until, schedule=SCHEDULE)


# Validates: REQ-ALMCHG-002
def test_status_round_trip_shows_two_lines_and_no_net_change(tmp_path):
    cfg, store = setup(tmp_path)
    for day, status in (("2026-10-16", "A"), ("2026-10-17", "B"), ("2026-10-19", "A")):
        save(store, KIND_ISSUES, day, [issue(status=status)])
    report = build(cfg, store)
    lines = [line for line in report.issues[0].lines if line.kind == "status"]
    assert [(line.day, line.before, line.after) for line in lines] == [("2026-10-17", "A", "B"), ("2026-10-19", "B", "A")]
    assert "status" not in report.issues[0].net
    assert report.counts["status"] == 2
    assert report.compared_days["issues"] == ["2026-10-16", "2026-10-17", "2026-10-19"]


# Validates: REQ-ALMCHG-002
def test_net_change_keeps_start_and_last_values(tmp_path):
    cfg, store = setup(tmp_path)
    save(store, KIND_ISSUES, "2026-10-16", [issue(status="A", assignees=["u1"])])
    save(store, KIND_ISSUES, "2026-10-17", [issue(status="B", assignees=["u1"])])
    save(store, KIND_ISSUES, "2026-10-19", [issue(status="C", assignees=["u2"], user_names={"u2": "이영희"})])
    net = build(cfg, store).issues[0].net
    assert net == {"status": ("A", "C"), "assignees": ("u1", "이영희")}


# Validates: REQ-ALMCHG-002
def test_assignee_line_has_names(tmp_path):
    cfg, store = setup(tmp_path)
    save(store, KIND_ISSUES, "2026-10-16", [issue(assignees=["u1"], user_names={"u1": "김철수"})])
    save(store, KIND_ISSUES, "2026-10-17", [issue(assignees=["u1", "u3"], user_names={"u3": "박민수"})])
    line = [l for l in build(cfg, store).issues[0].lines if l.kind == "assignee"][0]
    assert (line.before, line.after) == ("김철수", "김철수, 박민수")
    save(store, KIND_ISSUES, "2026-10-19", [issue(assignees=["u9"])])
    last = [l for l in build(cfg, store).issues[0].lines if l.kind == "assignee"][-1]
    assert last.after == "u9"


# Validates: REQ-ALMCHG-002
def test_comment_day_uses_kst(tmp_path):
    cfg, store = setup(tmp_path)
    save(store, KIND_ISSUES, "2026-10-16", [issue()])
    new = {"id": "c1", "created": "2026-10-16T16:30:00Z", "author": "u1", "text": "확인했습니다 본문"}
    save(store, KIND_ISSUES, "2026-10-19", [issue(comment_ids=["c1"], comments=[new], user_names={"u1": "김철수"})])
    line = build(cfg, store).issues[0].lines[0]
    assert (line.kind, line.day, line.time) == ("comment", "2026-10-17", "01:30")
    assert (line.detail, line.comment_id, line.comment_text) == ("김철수", "c1", "확인했습니다 본문")
    assert kst_day("2026-10-16T16:30:00Z") == "2026-10-17"


# Validates: REQ-ALMCHG-002
def test_unread_comment_is_replaced_later(tmp_path):
    cfg, store = setup(tmp_path)
    save(store, KIND_ISSUES, "2026-10-16", [issue()])
    save(store, KIND_ISSUES, "2026-10-17", [issue(comment_ids=["c1"], comments=[], comments_deferred=True)])
    first = build(cfg, store, until="2026-10-17").issues[0].lines
    assert [(l.kind, l.comment_id, l.detail, l.day) for l in first] == [("comment", "c1", "작성 정보 미수집", "2026-10-17")]
    real = {"id": "c1", "created": "2026-10-17T05:00:00Z", "author": "u1", "text": "본문입니다"}
    save(store, KIND_ISSUES, "2026-10-19", [issue(comment_ids=["c1"], comments=[real], user_names={"u1": "김철수"})])
    lines = build(cfg, store).issues[0].lines
    assert [(l.comment_id, l.detail, l.time) for l in lines] == [("c1", "김철수", "14:00")]


# Validates: REQ-ALMCHG-002
def test_srs_lines_and_sentence_counts(tmp_path):
    cfg, store = setup(tmp_path)
    save(store, KIND_SRS, "2026-10-16", [srs("S1", text="가. 나. 다."), srs("S3")])
    save(store, KIND_SRS, "2026-10-17", [srs("S1", text="가. 라. 마."), srs("S2")])
    report = build(cfg, store)
    by_id = {entity.entity_id: entity.lines for entity in report.srs}
    assert [l.kind for l in by_id["S2"]] == ["srs_created"]
    assert [l.kind for l in by_id["S3"]] == ["srs_removed"]
    modified = by_id["S1"][0]
    assert modified.kind == "srs_modified" and modified.day == "2026-10-17"
    assert "더해진 문장 2" in modified.detail and "빠진 문장 2" in modified.detail


# Validates: REQ-ALMCHG-002
def test_gap_and_before_first_notices(tmp_path):
    cfg, store = setup(tmp_path)
    save(store, KIND_ISSUES, "2026-10-16", [issue()])
    save(store, KIND_ISSUES, "2026-10-19", [issue()])
    report = build(cfg, store, since="2026-10-15", until="2026-10-19")
    assert "2026-10-17 ~ 2026-10-18 기록 없음: 이 사이 변경은 2026-10-19에 합쳐서 보입니다." in report.notices
    assert "2026-10-16 전의 변경은 확인할 기록이 없습니다." in report.notices


# Validates: REQ-ALMCHG-002
def test_between_reads_legacy_srs_dir(tmp_path):
    cfg, store = setup(tmp_path)
    save_snapshot(tmp_path / "legacy", "2026-10-14", [srs()], "", "")
    save(store, KIND_SRS, "2026-10-17", [srs()])
    names = [path.stem for path in store.between(KIND_SRS, "2026-10-16", "2026-10-19")]
    assert names == ["2026-10-14", "2026-10-17"]


# Validates: REQ-ALMCHG-002
def test_no_records_and_single_record_reasons(tmp_path):
    cfg, store = setup(tmp_path)
    assert build(cfg, store).empty_reason == "이 기간에 저장된 기록이 없습니다."
    save(store, KIND_ISSUES, "2026-10-16", [issue()])
    assert build(cfg, store).empty_reason == "비교할 기록이 하나뿐입니다(2026-10-16). 다음 기록부터 변경이 보입니다."
    save(store, KIND_ISSUES, "2026-10-19", [issue()])
    assert build(cfg, store).empty_reason == ""


# Validates: REQ-ALMCHG-002
def test_same_input_same_report(tmp_path):
    cfg, store = setup(tmp_path)
    save(store, KIND_ISSUES, "2026-10-16", [issue(status="A")])
    save(store, KIND_ISSUES, "2026-10-17", [issue(status="B")])
    assert asdict(build(cfg, store)) == asdict(build(cfg, store))


# Validates: REQ-ALMCHG-002
def test_removed_then_back_shows_both(tmp_path):
    cfg, store = setup(tmp_path)
    save(store, KIND_ISSUES, "2026-10-16", [issue("VP-1"), issue("VP-2")])
    save(store, KIND_ISSUES, "2026-10-17", [issue("VP-2")])
    save(store, KIND_ISSUES, "2026-10-19", [issue("VP-1"), issue("VP-2")])
    entity = [e for e in build(cfg, store).issues if e.entity_id == "VP-1"][0]
    assert [(l.kind, l.day) for l in entity.lines] == [("removed", "2026-10-17"), ("created", "2026-10-19")]


# Validates: REQ-ALMCHG-002
def test_link_uses_host_and_project(tmp_path):
    cfg, store = setup(tmp_path)
    save(store, KIND_ISSUES, "2026-10-16", [issue()])
    save(store, KIND_ISSUES, "2026-10-17", [issue(status="B")])
    assert build(cfg, store).issues[0].link == "https://alm.example/polarion/redirect/project/VXvue/workitem?id=VP-1"
