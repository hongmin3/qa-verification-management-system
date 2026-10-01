"""일일 점검 테스트 공용 fixture. 실제 Polarion·Claude·메일·운영 DB 를 쓰지 않는다."""

from __future__ import annotations

import json
from pathlib import Path

from openpyxl import Workbook

from app.modules.daily_qa.product_adapter import load_profile
from app.modules.daily_qa.rules import RulesState
from app.modules.daily_qa.settings import DailyQaSettings, PolarionSettings

REPO_ROOT = Path(__file__).resolve().parents[1]
#: 실제 config/products/vxvue.yaml 로 만든 프로필. 필드 대응이 운영 설정과 같다.
VXVUE_PROFILE = load_profile("VXvue", REPO_ROOT)


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
        profile=VXVUE_PROFILE,
        legacy_product="VXvue",
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


def issue_item(item_id: str, updated: str, linked: list[str], review: str = "lab_fixed", *, title: str = "",
               status: str = "verified", description: str = "설명", repro: str = "1. 실행", cause: str = "", action: str = "",
               comment_ids: list[str] | None = None) -> dict:
    relationships = {"linkedWorkItems": {"data": [{"id": f"VXvue/{item_id}/relates_to/VXvue/{target}"} for target in linked]}}
    if comment_ids is not None:
        relationships["comments"] = {"data": [{"id": value} for value in comment_ids]}
    return {
        "id": f"VXvue/{item_id}",
        "attributes": {
            "id": item_id, "title": title or f"{item_id} 제목", "status": status, "rndReviewResult": review,
            "updated": updated, "created": "2026-09-01T00:00:00Z",
            "description": {"type": "text/html", "value": f"<p>{description}</p>"},
            "reproductionStep": {"type": "text/html", "value": f"<p>{repro}</p>"},
            "occurrenceCause": {"type": "text/html", "value": f"<p>{cause}</p>"},
            "actionDetails": {"type": "text/html", "value": f"<p>{action}</p>"},
        },
        "relationships": relationships,
    }


class FakePolarion:
    """`ReadOnlyPolarionClient` 와 같은 조회 메서드만 가진 가짜."""

    def __init__(self, srs: list[dict], issues: list[dict], comments: dict[str, list[dict]] | None = None,
                 fail_comments: set[str] | None = None, srs_marker: str = "srs") -> None:
        #: 조회식에 이 글자가 있으면 SRS 조회로 본다 (제품마다 조회식이 다르다).
        self.srs_marker = srs_marker
        self.srs = srs
        self.issues = issues
        self.comments = comments or {}
        self.fail_comments = fail_comments or set()
        self.queries: list[str] = []
        self.comment_calls: list[str] = []

    def iter_workitems(self, query: str, fields: str = "@all", sort: str = "id"):
        self.queries.append(query)
        yield from (self.srs if self.srs_marker in query else self.issues)

    def get_comments(self, workitem_id: str, strict: bool = False):
        from app.modules.daily_qa.polarion import PolarionError

        self.comment_calls.append(workitem_id)
        if workitem_id in self.fail_comments:
            if strict:
                raise PolarionError("comments failed")
            return []
        return self.comments.get(workitem_id, [])


def comment(comment_id: str, text: str, created: str = "2026-09-29T01:00:00Z") -> dict:
    return {"id": comment_id, "attributes": {"created": created, "text": {"type": "text/html", "value": f"<p>{text}</p>"}}}


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
