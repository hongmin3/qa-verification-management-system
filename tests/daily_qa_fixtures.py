"""일일 점검 테스트 공용 fixture. 실제 Polarion·Claude·메일·운영 DB 를 쓰지 않는다."""

from __future__ import annotations

import json
from pathlib import Path

from openpyxl import Workbook

from app.modules.daily_qa.rules import RulesState
from app.modules.daily_qa.settings import DailyQaSettings, PolarionSettings


def make_settings(root: Path, workspace: Path, **overrides) -> DailyQaSettings:
    values = dict(
        root=root,
        product="VXvue",
        workspace_dir=workspace,
        claude_command="claude",
        claude_token="tok-secret-123",
        task_timeout_seconds=30,
        max_tasks_per_run=10,
        batch_size=5,
        tc_candidate_limit=10,
        weekly_day="mon",
        polarion=PolarionSettings(host="https://alm.example", token="pat", project_id="VXvue",
                                  srs_query="type:srs", issue_query="type:issue", request_interval_seconds=0),
        email_to=("qa@example.com",),
        review_base_url="http://qa.example",
    )
    values.update(overrides)
    return DailyQaSettings(**values)


def rules_ok(tmp: Path) -> RulesState:
    guide = tmp / "guide_Rev1.17.md"
    guide.write_text("# 가이드\n§61 신규 TC", encoding="utf-8")
    return RulesState(True, "1.17", "", guide, None)


def srs_item(item_id: str, old_id: str, title: str, text: str = "본문", status: str = "draft", category: bool = False) -> dict:
    return {
        "id": f"VXvue/{item_id}",
        "attributes": {
            "id": item_id, "oldId": old_id, "title": title, "status": status, "type": "srs",
            "isCategory": category, "updated": "2026-09-28T00:00:00Z",
            "descriptionKR": {"type": "text/html", "value": f"<p>{text}</p>"},
        },
    }


def issue_item(item_id: str, updated: str, linked: list[str], review: str = "lab_fixed") -> dict:
    return {
        "id": f"VXvue/{item_id}",
        "attributes": {
            "id": item_id, "title": f"{item_id} 제목", "status": "verified", "rndReviewResult": review,
            "updated": updated, "description": {"type": "text/html", "value": "<p>설명</p>"},
            "reproductionStep": {"type": "text/html", "value": "<p>1. 실행</p>"},
        },
        "relationships": {"linkedWorkItems": {"data": [{"id": f"VXvue/{item_id}/relates_to/VXvue/{target}"} for target in linked]}},
    }


class FakePolarion:
    """`ReadOnlyPolarionClient` 와 같은 조회 메서드만 가진 가짜."""

    def __init__(self, srs: list[dict], issues: list[dict]) -> None:
        self.srs = srs
        self.issues = issues
        self.queries: list[str] = []

    def iter_workitems(self, query: str, fields: str = "@all", sort: str = "id"):
        self.queries.append(query)
        yield from (self.srs if "srs" in query else self.issues)

    def get_comments(self, workitem_id: str):
        return [{"attributes": {"created": "2026-09-27", "text": {"value": "<p>수정 완료</p>"}}}]


def make_tc_workbook(path: Path, rows: list[tuple[str, str, str]]) -> Path:
    """rows: (tc_id, srs_ref, title)."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Viewer"
    sheet.append(["STC Category", "Old ID", "Old SRS ID", "Title", "Precondition", "Step Description", "Expected Reuslt"])
    for tc_id, srs_ref, title in rows:
        sheet.append(["Full Function", tc_id, srs_ref, title, "1. 로그인", "1. 실행한다.", "1. 표시된다."])
    workbook.save(path)
    return path


def write_result(run, task, findings: list[dict], questions: list[dict] | None = None, skill: str | None = None) -> None:
    payload = {
        "skill": skill or task.skill,
        "task_id": task.task_id,
        "gate_status": {"G1": "PASS"},
        "findings": findings,
        "open_questions": questions or [],
        "human_review_required": True,
    }
    target = run.out_dir / f"{task.task_id}.json"
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
