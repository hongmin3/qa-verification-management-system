"""분석 상세 화면이 읽는 이슈 본문 (TEST-QAINTEL-028).

Validates: REQ-QAINTEL-020
실제 이름·번호 없이 합성한 스냅샷과 작성자 표만 쓴다. AI·Polarion 은 부르지 않는다.
"""
import json
import os
from pathlib import Path

import pytest

from app.core.storage import Storage
from app.modules.qa_agent import issue_body


def snapshot_item(issue_id="VP-7287", **values):
    item = {
        "id": issue_id, "title": "동물용 Sequence 값이 그대로 전송됨", "status": "reviewed", "rd_result": "FIXED",
        "created": "2026-09-21T07:37:00Z", "updated": "2026-10-14T09:09:00Z",
        "reporter_id": "9001001", "reporter_name": "",
        "description": "", "reproduction_step": "[Title] Tag 값을 None 으로 변경\n[Step] 1. Edit Patient",
        "occurrence_cause": "Key 값이 남음", "action_details": "값이 있을 때만 채움",
        "occurred_versions": ["1.2.0"], "target_versions": ["1.3.0"],
        "comments": [
            {"id": "c1", "created": "2026-10-13T08:20:00Z", "author": "9001002", "text": "원인 확인"},
            {"id": "c2", "created": "2026-10-14T09:09:00Z", "author": "9009999", "text": "추가 의견"},
        ],
    }
    item.update(values)
    return item


def write(directory: Path, day: str, items: list[dict]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{day}.json"
    path.write_text(json.dumps({"run_date": day, "items": items}, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def snapshot_dir(tmp_path):
    issue_body.clear_cache()
    return tmp_path / "issues"


@pytest.fixture
def storage(tmp_path):
    db = Storage(db_path=tmp_path / "qa.db")
    db.qa_save_reporters("vxvue", [
        {"id": "VP-1", "reporter_id": "9001001", "reporter_name": "김예시"},
        {"id": "VP-2", "reporter_id": "9001002", "reporter_name": "이예시"},
    ])
    return db


def test_issue_body_reads_reproduction_step(snapshot_dir):
    write(snapshot_dir, "2026-10-15", [snapshot_item()])
    issue = issue_body.load("vxvue", "VP-7287", snapshot_dir=snapshot_dir)
    assert issue.reproduction_step.startswith("[Title]")
    assert issue.description == ""
    assert (issue.id, issue.status, issue.rd_result) == ("VP-7287", "reviewed", "FIXED")
    assert issue.occurred_versions == ["1.2.0"] and issue.target_versions == ["1.3.0"]


def test_issue_body_uses_newest_snapshot(snapshot_dir):
    write(snapshot_dir, "2026-10-13", [snapshot_item(title="옛 제목")])
    write(snapshot_dir, "2026-10-15", [snapshot_item(title="새 제목")])
    assert issue_body.load("vxvue", "VP-7287", snapshot_dir=snapshot_dir).title == "새 제목"


def test_issue_body_missing_everything_returns_none(snapshot_dir):
    assert issue_body.load("vxvue", "VP-7287", snapshot_dir=snapshot_dir) is None
    write(snapshot_dir, "2026-10-15", [snapshot_item("VP-1")])
    assert issue_body.load("vxvue", "VP-7287", snapshot_dir=snapshot_dir) is None
    (snapshot_dir / "2026-10-16.json").write_text("{깨진 파일", encoding="utf-8")
    assert issue_body.load("vxvue", "VP-7287", snapshot_dir=snapshot_dir) is None


def test_issue_body_is_read_once_per_file_modification_time(snapshot_dir, monkeypatch):
    path = write(snapshot_dir, "2026-10-15", [snapshot_item()])
    reads = []
    original = issue_body._read_items
    monkeypatch.setattr(issue_body, "_read_items", lambda target: reads.append(target) or original(target))
    issue_body.load("vxvue", "VP-7287", snapshot_dir=snapshot_dir)
    issue_body.load("vxvue", "VP-7287", snapshot_dir=snapshot_dir)
    assert len(reads) == 1
    write(snapshot_dir, "2026-10-15", [snapshot_item(title="바뀜")])
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 5))
    assert issue_body.load("vxvue", "VP-7287", snapshot_dir=snapshot_dir).title == "바뀜"
    assert len(reads) == 2


def test_comment_author_names_come_from_reporter_table(snapshot_dir, storage):
    write(snapshot_dir, "2026-10-15", [snapshot_item()])
    issue = issue_body.load("vxvue", "VP-7287", snapshot_dir=snapshot_dir, storage=storage)
    assert issue.reporter_name == "김예시"
    first, second = issue.comments
    assert (first["author_id"], first["author_name"]) == ("9001002", "이예시")
    assert (second["author_id"], second["author_name"]) == ("9009999", "이름 미확인 (9009999)")
    assert set(first) == {"id", "created", "author_id", "author_name", "text"}


def test_comment_without_author_has_empty_name(snapshot_dir):
    write(snapshot_dir, "2026-10-15", [snapshot_item(reporter_id="", comments=[{"id": "c3", "created": "", "author": "", "text": "작성자 없음"}])])
    issue = issue_body.load("vxvue", "VP-7287", snapshot_dir=snapshot_dir)
    assert issue.comments[0]["author_name"] == "" and issue.reporter_name == ""


def test_comments_missing_in_snapshot_are_an_empty_list(snapshot_dir):
    write(snapshot_dir, "2026-10-15", [snapshot_item(comments=None)])
    assert issue_body.load("vxvue", "VP-7287", snapshot_dir=snapshot_dir).comments == []
