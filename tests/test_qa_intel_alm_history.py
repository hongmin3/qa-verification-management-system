"""ALM-QA-Automation 과거 SRS 스냅샷 가져오기와 본문 링크 풀기.

TEST-QAINTEL-015 과거 SRS 가져오기와 본문 링크.

Validates: REQ-QAINTEL-003, REQ-QAINTEL-021, REQ-QAINTEL-027, REQ-QAINTEL-029
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from app.modules.daily_qa import scheduled_jobs, snapshots
from app.modules.daily_qa.alm_history import SOURCE, import_history
from app.modules.daily_qa.collector import collect_srs
from app.modules.daily_qa.product_adapter import normalize_srs, resolve_rich_text, srs_titles
from app.modules.daily_qa.settings import PolarionSettings
from app.modules.qa_agent import dashboard as dash
from tests.daily_qa_fixtures import VXVUE_PROFILE, FakePolarion, make_settings
from tests.qa_intel_harness import DAY1, DAY2, DAY3, Harness

LINK = '<span class="polarion-rte-link" data-type="workItem" id="fake" data-item-id="{0}" data-option-id="long"></span>'
ICON = '<img src="/polarion/icons/default/enums/type_purple_srs.png"/>'


def _srs(item_id: str, title: str, html: str) -> dict:
    return {"id": f"VXvue/{item_id}", "attributes": {
        "id": item_id, "oldId": "01-01", "title": title, "status": "draft", "type": "srs", "isCategory": False,
        "updated": "2026-09-28T00:00:00Z", "descriptionKR": {"type": "text/html", "value": html}}}


# -- 본문 링크 (REQ-QAINTEL-003 4번) ------------------------------------------------


def test_links_are_resolved_to_id_and_title_and_icons_removed():
    html = f"<p>설정은 ({LINK.format('VP-678')}) 참고. 없는 대상 {LINK.format('VP-999')}</p><p>{ICON}<a href=\"/polarion/#/project/VXvue/workitem?id=VP-678\">VP-678 - Status Bar</a></p>"
    text = resolve_rich_text(html, {"VP-678": "Status Bar <Bar>"})
    assert "VP-678 - Status Bar &lt;Bar&gt;" in text            # 제목은 HTML 로 이스케이프한다
    assert "VP-999 (참조 대상 확인 불가)" in text
    assert "/polarion/icons" not in text


def test_collected_srs_text_uses_titles_from_the_same_collection():
    items = [_srs("VP-1", "목록", f"<p>{LINK.format('VP-2')} 를 본다</p>"), _srs("VP-2", "검색", "<p>검색</p>")]
    collected = collect_srs(FakePolarion(items, []), VXVUE_PROFILE, None)
    by_id = {item["id"]: item for item in collected.items}
    assert by_id["VP-1"]["text"] == "VP-2 - 검색 를 본다"


def test_without_titles_normalize_keeps_old_behaviour():
    """제목표를 주지 않는 호출(이슈 등)은 예전과 같다 — 링크 표시는 글자가 없어 사라진다."""
    item = _srs("VP-1", "목록", f"<p>앞 {LINK.format('VP-2')} 뒤</p>")
    assert normalize_srs(item, VXVUE_PROFILE)["text"] == "앞 뒤"
    assert normalize_srs(item, VXVUE_PROFILE, srs_titles([item], VXVUE_PROFILE))["text"] == "앞 VP-2 (참조 대상 확인 불가) 뒤"


# -- 가져오기 (REQ-QAINTEL-029) ---------------------------------------------------


def _raw(item_id: str, title: str, html: str) -> dict:
    return {"id": item_id, "uid": f"VXvue/{item_id}", "title": title, "status": "draft", "type": "srs",
            "old_id": "01-01", "is_category": False, "updated": "2026-09-01T00:00:00Z",
            "description_kr_raw": html, "description_raw": None, "content_html": "<p>쓰지 않는 값</p>"}


def _day(source: Path, date: str, records: list[dict], expected: int | None = None, broken: str = "") -> None:
    folder = source / date / "VXvue"
    folder.mkdir(parents=True)
    for record in records:
        text = json.dumps(record, ensure_ascii=False, indent=2)
        if record["id"] == broken:
            text += '  "title": "꼬리",\n}'                       # 동시에 두 번 쓰여 꼬리가 남은 파일
        (folder / f"{record['id']}.json").write_text(text, encoding="utf-8")
    manifest = {"generated_at": f"{date}T09:04:54", "run_date": date,
                "stats": [{"project_id": "VXvue", "expected_total": expected or len(records), "total_items": len(records)}]}
    (source / date / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "srs-spec" / "snapshots"
    two = [_raw("VP-10", "목록", f"<p>{LINK.format('VP-11')} 참고</p>"), _raw("VP-11", "검색", "<p>검색</p>")]
    _day(source, "2026-09-27", two)
    _day(source, "2026-09-28", [two[0], _raw("VP-11", "검색 화면", "<p>검색</p>")], broken="VP-11")
    _day(source, "2026-09-29", two, expected=3)
    _day(source, "2026-09-30", [two[0], _raw("VP-11", "검색 화면", "<p>검색 결과를 최신순으로</p>")])
    (source / "render_problem_state.json").write_text("{}", encoding="utf-8")      # 날짜 폴더가 아닌 것은 무시
    return source


def test_import_takes_only_reliable_days_and_is_idempotent(tmp_path):
    source = _source(tmp_path)
    before = sorted((str(p), p.stat().st_mtime_ns) for p in source.rglob("*") if p.is_file())
    store = snapshots.SnapshotStore(root=tmp_path / "store")

    dry = import_history(source, store, "VXvue", VXVUE_PROFILE, dry_run=True)
    assert [day.status for day in dry.days] == ["would_import", "skipped", "skipped", "would_import"]
    assert not (tmp_path / "store").exists()                                       # 시험 실행은 쓰지 않는다

    result = import_history(source, store, "VXvue", VXVUE_PROFILE)
    statuses = {day.date: (day.status, day.reason) for day in result.days}
    assert statuses["2026-09-27"][0] == "imported" and statuses["2026-09-30"][0] == "imported"
    assert statuses["2026-09-28"] == ("skipped", "읽지 못한 파일 1개: VP-11.json")
    assert statuses["2026-09-29"] == ("skipped", "manifest 개수 3건과 파일 2건이 다름")

    path = store.directory(snapshots.KIND_SRS) / "2026-09-27.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["source"] == SOURCE and data["collected_at"] == "2026-09-27T09:04:54" and data["count"] == 2
    assert data["items"][0]["text"] == "VP-11 - 검색 참고"                           # 원시 본문 + 링크 풀기(content_html 은 안 씀)

    again = import_history(source, store, "VXvue", VXVUE_PROFILE)
    assert {day.date: day.status for day in again.days}["2026-09-27"] == "exists"   # 덮어쓰지 않는다
    assert sorted((str(p), p.stat().st_mtime_ns) for p in source.rglob("*") if p.is_file()) == before   # 원본은 읽기만


def test_file_whose_tail_repeats_its_own_end_is_restored(tmp_path):
    source = tmp_path / "srs-spec" / "snapshots"
    records = [_raw("VP-10", "목록", "<p>목록</p>"), _raw("VP-11", "검색", "<p>검색</p>")]
    _day(source, "2026-09-21", records)
    path = source / "2026-09-21" / "VXvue" / "VP-11.json"
    text = path.read_text(encoding="utf-8")
    path.write_text(text + text[-60:], encoding="utf-8")      # 짧은 쓰기 뒤에 긴 쓰기의 끝 60글자가 남았다
    store = snapshots.SnapshotStore(root=tmp_path / "store")

    result = import_history(source, store, "VXvue", VXVUE_PROFILE)
    day = result.days[0]
    assert (day.status, day.count, day.restored) == ("imported", 2, ["VP-11.json"])
    assert day.line() == "2026-09-21 가져옴 2건 (되살린 파일 1개)"
    data = json.loads((store.directory(snapshots.KIND_SRS) / "2026-09-21.json").read_text(encoding="utf-8"))
    assert data["restored_files"] == ["VP-11.json"]
    assert [item["title"] for item in data["items"]] == ["목록", "검색"]


def test_import_does_not_overwrite_a_snapshot_the_daily_run_collected(tmp_path):
    source = _source(tmp_path)
    store = snapshots.SnapshotStore(root=tmp_path / "store")
    store.save(snapshots.KIND_SRS, "2026-09-30", [{"id": "VP-10", "old_id": "", "title": "매일 실행", "status": "", "text": ""}])
    result = import_history(source, store, "VXvue", VXVUE_PROFILE)
    assert {day.date: day.status for day in result.days}["2026-09-30"] == "exists"
    assert store.load(store.at_or_before(snapshots.KIND_SRS, "2026-09-30"))[0]["title"] == "매일 실행"


# -- 가져온 스냅샷으로 기간 실행 (REQ-QAINTEL-021, 027) ------------------------------------


def test_period_run_over_imported_days_finds_srs_change(tmp_path):
    h = Harness(tmp_path)
    store = snapshots.for_settings(h.cfg)
    store.save(snapshots.KIND_SRS, DAY1.date().isoformat(), [{"id": "VP-11", "old_id": "01-02", "title": "검색", "status": "draft", "text": "검색"}], source=SOURCE)
    store.save(snapshots.KIND_SRS, DAY2.date().isoformat(), [{"id": "VP-11", "old_id": "01-02", "title": "검색 화면", "status": "draft", "text": "검색"}], source=SOURCE)
    outcome = h.run(DAY3 + timedelta(hours=1), since=DAY1.date(), until=DAY2.date())
    assert [e["event_type"] for e in h.events(outcome["run_id"])] == ["SRS_UPDATED"]
    assert outcome["stages"]["collect_issues"]["status"] == "failed"              # 이슈 스냅샷이 없다
    assert outcome["status"] == "PARTIAL"


def test_past_period_launches_without_polarion_but_today_run_does_not(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "data").mkdir(parents=True)
    cfg = make_settings(root, tmp_path / "ws", polarion=PolarionSettings(host="", token="", project_id="", srs_query="",
                                                                          issue_query="", request_interval_seconds=0))
    monkeypatch.setattr(scheduled_jobs, "load", lambda product=None: cfg)

    class Process:
        pid = 4321

    launched = []
    popen = lambda command, **kwargs: launched.append(command) or Process()  # noqa: E731
    assert scheduled_jobs.launch_detached(trigger="manual", popen=popen)["status"] == "not_configured"
    result = scheduled_jobs.launch_detached(trigger="manual", popen=popen, since="2026-08-31", until="2026-09-22")
    assert result["status"] == "launched" and "--until" in launched[0]


def test_period_without_snapshots_is_refused_with_oldest_date(tmp_path):
    root = tmp_path / "root"
    (root / "data").mkdir(parents=True)
    cfg = make_settings(root, tmp_path / "ws")
    assert dash.period_snapshot_check(cfg, "2026-09-22") == ("저장된 SRS 스냅샷이 없습니다.", "")
    snapshots.for_settings(cfg).save(snapshots.KIND_SRS, "2026-09-25", [{"id": "VP-1", "title": "", "old_id": "", "status": "", "text": ""}])
    refusal, _ = dash.period_snapshot_check(cfg, "2026-09-22")
    assert refusal == "2026-09-22 이전에 저장된 SRS 스냅샷이 없어 이 기간은 분석할 수 없습니다. 가장 오래된 스냅샷: 2026-09-25"
    assert dash.period_snapshot_check(cfg, "2026-09-26") == ("", "이 기간에는 저장된 이슈 스냅샷이 없어 SRS 변경만 분석합니다.")
    assert dash.period_snapshot_check(cfg, "") == ("", "")
    assert dash.run_period_defaults(min_day=dash.oldest_snapshot_day(cfg))["min"] == "2026-09-25"
