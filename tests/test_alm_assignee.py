"""담당자·사용자 이름 수집과 담당자 변경 감지.

# Validates: REQ-QAINTEL-002, REQ-QAINTEL-005
"""

from __future__ import annotations

from app.modules.daily_qa.change_events import ISSUE_METADATA_CHANGED, ISSUE_STATUS_ONLY_CHANGED, detect_issue_events
from app.modules.daily_qa.polarion import ReadOnlyPolarionClient
from app.modules.daily_qa.product_adapter import normalize_issue
from app.modules.daily_qa.settings import PolarionSettings
from tests.daily_qa_fixtures import VXVUE_PROFILE, issue_item
from tests.test_qa_intel_events import issue

CONFIG = PolarionSettings(host="https://alm.example", token="pat", project_id="VXvue", srs_query="type:srs",
                          issue_query="type:issue", page_size=5, request_interval_seconds=0)


def test_normalize_issue_reads_assignees_and_names():
    raw = issue_item("VP-1", "2026-10-01", [], assignees=(("u-assignee", "담당자A"),), author=("u-author", "작성자A"))
    item = normalize_issue(raw, VXVUE_PROFILE)
    assert item["assignees"] == ["u-assignee"]
    assert item["user_names"] == {"u-assignee": "담당자A", "u-author": "작성자A"}


def test_absent_assignee_relation_writes_no_assignees_key():
    item = normalize_issue(issue_item("VP-1", "2026-10-01", []), VXVUE_PROFILE)
    assert "assignees" not in item
    assert item["user_names"] == {}


def test_present_empty_assignee_relation_is_empty_list():
    for data in ([], None):
        raw = issue_item("VP-1", "2026-10-01", [])
        raw["relationships"]["assignee"] = {"data": data}
        assert normalize_issue(raw, VXVUE_PROFILE)["assignees"] == []


def test_assignee_relation_name_comes_from_profile_relations():
    from app.modules.daily_qa.product_adapter import STANDARD_RELATIONS

    assert STANDARD_RELATIONS["assignees"] == "assignee"


def test_previous_assignees_then_relation_omitted_is_no_event():
    before = issue(assignees=["a"])
    after = normalize_issue(issue_item("VP-1", "2026-10-01", []), VXVUE_PROFILE)
    after = {**issue(), **{k: v for k, v in after.items() if k == "user_names"}}
    assert "assignees" not in after
    assert detect_issue_events([before], [after]) == []


def test_previous_assignees_then_present_empty_is_metadata_event():
    events = detect_issue_events([issue(assignees=["a"])], [issue(assignees=[])])
    assert [(e.event_type, e.changed_fields) for e in events] == [(ISSUE_METADATA_CHANGED, ["assignees"])]


def test_assignee_change_is_metadata_event():
    events = detect_issue_events([issue(assignees=["a"])], [issue(assignees=["b"])])
    assert [(e.event_type, e.changed_fields) for e in events] == [(ISSUE_METADATA_CHANGED, ["assignees"])]


def test_status_and_assignee_change_is_metadata_not_status_only():
    events = detect_issue_events([issue(assignees=["a"])], [issue(assignees=["b"], status="closed")])
    assert [e.event_type for e in events] == [ISSUE_METADATA_CHANGED]
    assert [e.event_type for e in detect_issue_events([issue(assignees=["a"])], [issue(assignees=["a"], status="closed")])] == [ISSUE_STATUS_ONLY_CHANGED]


def test_user_names_change_alone_is_not_an_event():
    before = [issue(assignees=["a"], user_names={"a": "이전 이름"})]
    after = [issue(assignees=["a"], user_names={"a": "바뀐 이름"})]
    assert detect_issue_events(before, after) == []


def test_old_snapshot_without_assignees_is_not_a_change():
    assert detect_issue_events([issue()], [issue(assignees=["b"])]) == []
    events = detect_issue_events([issue()], [issue(assignees=["b"], status="closed")])
    assert [e.event_type for e in events] == [ISSUE_STATUS_ONLY_CHANGED]


def test_polarion_query_includes_assignee_and_attaches_user_names():
    seen = []

    def transport(url, params):
        seen.append(params)
        return {"data": [{"id": "VXvue/VP-1", "attributes": {"id": "VP-1"},
                          "relationships": {"author": {"data": {"type": "users", "id": "u1"}},
                                            "assignee": {"data": [{"type": "users", "id": "u2"}]}}}],
                "included": [{"type": "users", "id": "u1", "attributes": {"name": "작성자"}},
                             {"type": "users", "id": "u2", "attributes": {"name": "담당자"}},
                             {"type": "users", "id": "u3", "attributes": {"name": "무관"}}],
                "links": {}}

    client = ReadOnlyPolarionClient(CONFIG, transport=transport)
    items = list(client.iter_workitems("type:issue"))
    assert seen[0]["include"] == "author,assignee"
    assert items[0]["_user_names"] == {"u1": "작성자", "u2": "담당자"}
    assert items[0]["_reporter_name"] == "작성자"
