"""Validates: REQ-DAILY-002 (TEST-DAILY-002)."""

from __future__ import annotations

import ast
import inspect

import pytest

from app.modules.daily_qa import polarion
from app.modules.daily_qa.polarion import PolarionError, ReadOnlyPolarionClient, normalize_issue, normalize_srs
from app.modules.daily_qa.settings import PolarionSettings
from app.modules.daily_qa.srs_snapshot import diff_snapshots, previous_snapshot, save_snapshot
from tests.daily_qa_fixtures import issue_item, srs_item

CONFIG = PolarionSettings(host="https://alm.example", token="pat", project_id="VXvue", srs_query="type:srs",
                          issue_query="type:issue", page_size=2, request_interval_seconds=0)


def test_client_module_only_issues_get_requests():
    """수집 코드에 GET 이외의 HTTP 호출이 없다 — 소스를 직접 읽어 확인한다."""
    tree = ast.parse(inspect.getsource(polarion))
    http_calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name) and node.func.value.id == "httpx"
    }
    assert http_calls == {"get"}
    public = {name for name, _ in inspect.getmembers(ReadOnlyPolarionClient, inspect.isfunction)}
    assert not any(word in name for name in public for word in ("post", "patch", "put", "delete", "create", "update"))


def test_iter_workitems_pages_until_no_next_link():
    pages = {
        1: {"data": [srs_item("VP-1", "01-10-01", "A"), srs_item("VP-2", "01-10-02", "B")], "links": {"next": "x"}},
        2: {"data": [srs_item("VP-3", "01-10-03", "C")], "links": {}},
    }
    seen_params = []

    def transport(url, params):
        seen_params.append(params)
        assert url.endswith("/polarion/rest/v1/projects/VXvue/workitems")
        return pages[params["page[number]"]]

    client = ReadOnlyPolarionClient(CONFIG, transport=transport)
    ids = [item["attributes"]["id"] for item in client.iter_workitems("type:srs")]
    assert ids == ["VP-1", "VP-2", "VP-3"]
    assert [params["page[number]"] for params in seen_params] == [1, 2]


def test_client_refuses_without_credentials():
    with pytest.raises(PolarionError):
        ReadOnlyPolarionClient(PolarionSettings(host="", token="", project_id="VXvue", srs_query="", issue_query=""))


def test_normalize_srs_and_issue_fields():
    srs = normalize_srs(srs_item("VP-1108", "03-10-05", "목록 화면", text="기능 요약"))
    assert srs["id"] == "VP-1108" and srs["old_id"] == "03-10-05" and "기능 요약" in srs["text"]
    issue = normalize_issue(issue_item("VP-6669", "2026-09-27T10:00:00Z", ["VP-767"]))
    assert issue["lab_review_result"] == "lab_fixed"
    assert issue["linked_ids"] == ["VP-767"]
    assert "1. 실행" in issue["reproduction_step"]


def _item(item_id, text="a", title="T", old="01-10-01"):
    return {"id": item_id, "old_id": old, "title": title, "status": "draft", "text": text, "is_category": False}


def test_diff_classifies_added_removed_modified():
    before = [_item("VP-1"), _item("VP-2"), _item("VP-3", text="old")]
    after = [_item("VP-1"), _item("VP-3", text="new"), _item("VP-4")]
    diff = diff_snapshots(before, after)
    assert [item["id"] for item in diff.added] == ["VP-4"]
    assert [item["id"] for item in diff.removed] == ["VP-2"]
    assert [item["id"] for item in diff.modified] == ["VP-3"]
    assert diff.modified[0]["before"] == {"text": "old"} and diff.modified[0]["after"] == {"text": "new"}


def test_first_run_is_baseline_only():
    diff = diff_snapshots(None, [_item("VP-1")])
    assert diff.baseline_only and not diff.changed_ids


def test_previous_snapshot_excludes_same_day(tmp_path):
    save_snapshot(tmp_path, "2026-09-25", [_item("VP-1")])
    save_snapshot(tmp_path, "2026-09-28", [_item("VP-1")])
    assert previous_snapshot(tmp_path, "2026-09-28").stem == "2026-09-25"
    assert previous_snapshot(tmp_path, "2026-09-25") is None
