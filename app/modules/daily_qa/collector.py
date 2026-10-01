"""Polarion 수집기: SRS 전체·이슈 전체 → 스냅샷 → 비교 (SPEC REQ-QAINTEL-003·004·008·009).

수집이 믿을 수 없으면(요청 실패, 0건, 이슈 급감) 스냅샷을 저장하지 않는다. 그래야 다음 실행이
마지막 정상 스냅샷과 비교한다. 저장은 호출자(`pipeline.py`)가 이벤트 저장과 묶어서 한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.modules.daily_qa.change_events import comments_unread
from app.modules.daily_qa.polarion import PolarionError
from app.modules.daily_qa.product_adapter import ProductProfile, normalize_comment, normalize_issue, normalize_srs
from app.modules.daily_qa.srs_snapshot import SrsDiff, diff_snapshots


class CollectionError(RuntimeError):
    """수집 결과를 믿을 수 없다. 스냅샷 기준을 옮기지 않는다."""


@dataclass
class SrsCollection:
    items: list[dict]
    previous: list[dict] | None
    diff: SrsDiff

    @property
    def baseline_only(self) -> bool:
        return self.previous is None


@dataclass
class IssueCollection:
    items: list[dict]
    previous: list[dict] | None
    previous_collected_at: str = ""
    comments_fetched: int = 0
    comments_failed: list[str] = field(default_factory=list)
    comments_deferred: int = 0

    @property
    def baseline_only(self) -> bool:
        return self.previous is None


def collect_srs(client, profile: ProductProfile, previous: list[dict] | None) -> SrsCollection:
    items = [normalize_srs(item, profile) for item in client.iter_workitems(profile.srs_query)]
    if not items:
        # 빈 결과를 스냅샷으로 저장하면 다음 날 모든 SRS 가 '삭제'로 보인다.
        raise CollectionError("SRS 조회 결과가 0건입니다. 조회식·권한을 확인하세요.")
    return SrsCollection(items=items, previous=previous, diff=diff_snapshots(previous, items))


def _needs_comments(current: dict, previous: dict | None) -> bool:
    if previous is None or comments_unread(previous):
        return True
    prev_comments = previous.get("comments")
    if prev_comments is None:
        return True
    if current.get("comment_ids") is not None:
        known = {item.get("id") for item in prev_comments} | set(previous.get("comment_ids") or [])
        return bool(set(current["comment_ids"]) - known)
    return current.get("updated") != previous.get("updated")


def collect_issues(
    client,
    profile: ProductProfile,
    previous: list[dict] | None,
    previous_meta: dict | None = None,
    *,
    comment_fetch_limit: int = 300,
    drop_ratio: float = 0.5,
) -> IssueCollection:
    """이슈 전체를 읽고 필요한 이슈만 댓글을 읽는다 (REQ-QAINTEL-004)."""
    items = [normalize_issue(item, profile) for item in client.iter_workitems(profile.issue_query)]
    if not items:
        raise CollectionError("이슈 조회 결과가 0건입니다. 조회식·권한을 확인하세요.")
    if previous and drop_ratio > 0 and len(items) < len(previous) * (1 - drop_ratio):
        raise CollectionError(
            f"이슈 수가 어제 {len(previous)}건에서 오늘 {len(items)}건으로 급감했습니다. "
            "조회 결과를 믿을 수 없어 기준을 옮기지 않습니다."
        )
    result = IssueCollection(items=items, previous=previous, previous_collected_at=(previous_meta or {}).get("collected_at", ""))
    old = {item["id"]: item for item in previous or []}
    for issue in items:
        before = old.get(issue["id"])
        if previous is None:
            # 기준 스냅샷 실행은 댓글 본문을 읽지 않는다. 번호를 알면 번호만 남긴다.
            issue["comments"] = [{"id": comment_id, "created": "", "text": ""} for comment_id in issue["comment_ids"]] \
                if issue.get("comment_ids") is not None else None
            continue
        if not _needs_comments(issue, before):
            issue["comments"] = before.get("comments") if before else None
            continue
        if result.comments_fetched >= comment_fetch_limit:
            # 상한을 넘은 이슈는 어제 값을 옮겨 적고 다음 실행에서 읽는다.
            issue["comments"] = before.get("comments") if before else None
            issue["comments_deferred"] = True
            result.comments_deferred += 1
            continue
        try:
            raw = client.get_comments(issue["id"], strict=True)
        except PolarionError:
            issue["comments"] = before.get("comments") if before else None
            issue["comments_error"] = True
            result.comments_failed.append(issue["id"])
            continue
        result.comments_fetched += 1
        issue["comments"] = [normalize_comment(comment) for comment in raw]
    return result


def fetch_all_comments(client, issue_id: str, limit: int = 20) -> tuple[list[dict], bool]:
    """분석 입력에 넣을 댓글 전체(최근 `limit` 개). (댓글, 읽기 성공)."""
    try:
        raw = client.get_comments(issue_id, strict=True)
    except PolarionError:
        return [], False
    comments = [normalize_comment(comment) for comment in raw]
    comments.sort(key=lambda item: item.get("created") or "")
    return comments[-limit:], True
