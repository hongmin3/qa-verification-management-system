"""변경 감지와 분석 대상 고르기 (SPEC REQ-QAINTEL-005·007·010).

어제·오늘 공통 모델(`product_adapter.py`)을 필드별로 비교해 변경 이벤트를 만든다. 제품 고유 필드
이름을 모른다 — 공통 모델의 필드 이름(`rd_result_raw`, `occurrence_cause` …)만 본다.

AI 를 부를지는 여기서 코드로 정한다. 상태만 바뀐 이슈, 의미 없는 댓글만 달린 이슈는 이벤트만 남기고
분석하지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field

from app.modules.daily_qa.product_adapter import RD_FIXED, RD_SPEC_LIKE, collapse
from app.modules.daily_qa.srs_snapshot import SrsDiff

ENTITY_ISSUE = "issue"
ENTITY_SRS = "srs"

ISSUE_CREATED = "ISSUE_CREATED"
ISSUE_CONTENT_UPDATED = "ISSUE_CONTENT_UPDATED"
ISSUE_REPRODUCTION_UPDATED = "ISSUE_REPRODUCTION_UPDATED"
ISSUE_RD_RESULT_CHANGED = "ISSUE_RD_RESULT_CHANGED"
ISSUE_ROOT_CAUSE_CHANGED = "ISSUE_ROOT_CAUSE_CHANGED"
ISSUE_ACTION_DETAILS_CHANGED = "ISSUE_ACTION_DETAILS_CHANGED"
ISSUE_COMMENT_ADDED = "ISSUE_COMMENT_ADDED"
ISSUE_STATUS_ONLY_CHANGED = "ISSUE_STATUS_ONLY_CHANGED"
ISSUE_METADATA_CHANGED = "ISSUE_METADATA_CHANGED"
ISSUE_REMOVED = "ISSUE_REMOVED"
SRS_CREATED = "SRS_CREATED"
SRS_UPDATED = "SRS_UPDATED"
SRS_REMOVED = "SRS_REMOVED"
#: 이슈 기록이 없는 기간의 현재 상태 기준 점검 대상 (REQ-QAINTEL-030).
ISSUE_AUDIT_TARGET = "ISSUE_AUDIT_TARGET"

EVENT_LABELS = {
    ISSUE_CREATED: "신규 이슈",
    ISSUE_CONTENT_UPDATED: "이슈 본문 변경",
    ISSUE_REPRODUCTION_UPDATED: "재현 절차 변경",
    ISSUE_RD_RESULT_CHANGED: "연구소 결과 변경",
    ISSUE_ROOT_CAUSE_CHANGED: "발생 원인 변경",
    ISSUE_ACTION_DETAILS_CHANGED: "조치 내용 변경",
    ISSUE_COMMENT_ADDED: "댓글 추가",
    ISSUE_STATUS_ONLY_CHANGED: "상태만 변경",
    ISSUE_METADATA_CHANGED: "속성 변경",
    ISSUE_REMOVED: "이슈 조회 안 됨",
    SRS_CREATED: "SRS 신규",
    SRS_UPDATED: "SRS 변경",
    SRS_REMOVED: "SRS 삭제",
    ISSUE_AUDIT_TARGET: "현재 상태 점검 대상",
}

#: 분석 종류. 값은 저장·화면에 그대로 쓴다.
NEW_ISSUE = "NEW_ISSUE"
FIXED_ISSUE = "FIXED_ISSUE"
SPEC_DECISION = "SPEC_DECISION"
COMMENT = "COMMENT"
SPEC_COVERAGE = "SPEC_COVERAGE"
ISSUE_AUDIT = "ISSUE_AUDIT"
#: 사람이 버튼으로 요청한 분석의 종류. 이벤트가 아니라 Finding 에만 쓴다 (REQ-QAINTEL-016·032·034).
TC_CHECK = "TC_CHECK"
TC_DRAFT = "TC_DRAFT"
MANUAL_CHECK = "MANUAL_CHECK"
#: 실행 순서 (REQ-QAINTEL-010).
ANALYSIS_ORDER = (NEW_ISSUE, FIXED_ISSUE, SPEC_DECISION, COMMENT, SPEC_COVERAGE, ISSUE_AUDIT)
ANALYSIS_LABELS = {
    NEW_ISSUE: "신규 이슈 분석",
    FIXED_ISSUE: "수정 완료 이슈 분석",
    SPEC_DECISION: "Spec 판정 이슈 분석",
    COMMENT: "새 댓글 분석",
    SPEC_COVERAGE: "사양 변경 분석",
    ISSUE_AUDIT: "이슈 정합성 점검(현재 상태 기준)",
    TC_CHECK: "TC 점검(요청)",
    TC_DRAFT: "검증 TC 초안(요청)",
    MANUAL_CHECK: "매뉴얼 점검(요청)",
}

CONTENT_FIELDS = ("title", "description")
REPRODUCTION_FIELDS = ("reproduction_step",)
METADATA_FIELDS = ("status", "severity", "occurred_versions", "target_versions", "linked_ids")

#: 진행 상태만 알리는 댓글. 제품 설정 `comment_noise_patterns` 는 이 목록에 더한다. 모양이 문장 전체와 맞아야 한다.
DEFAULT_COMMENT_NOISE = (
    r"(확인|확인했습니다|확인하였습니다|확인 부탁드립니다|확인 요청드립니다|검토 ?중|진행 ?중|처리 ?중|수정 ?중|"
    r"수정 ?완료|반영 ?완료|배포 ?완료|완료|감사합니다|ok|okay|done|checked|noted|thanks?|in progress|fixed)[.!~\s]*",
)

NO_ANALYSIS_REASON = "이 연구소 결과에 맞는 분석이 없어 기록만 남김"

#: 이벤트 `after` 에 함께 싣지만 바뀌기 전 값에서 계산한 것이라 끝 상태 지문에서 빼는 키.
BEFORE_DEPENDENT_AFTER_KEYS = ("added_sentences", "removed_sentences")


@dataclass
class ChangeEvent:
    entity_type: str
    entity_id: str
    event_type: str
    before: dict = field(default_factory=dict)
    after: dict = field(default_factory=dict)
    changed_fields: list[str] = field(default_factory=list)
    analysis_required: bool = False
    reason: str = ""
    #: 이 이벤트로 돌릴 분석 종류 (REQ-QAINTEL-010). 저장해 두면 재시도 때 다시 고르지 않아도 된다.
    analyses: list[str] = field(default_factory=list)
    #: 비교 기준 스냅샷 날짜(`YYYY-MM-DD`). 같은 기준끼리만 같은 이벤트로 본다 (REQ-QAINTEL-006 순서 2).
    basis: str = ""

    @property
    def fingerprint(self) -> str:
        """같은 변경인지 가리는 기준. 같은 날 다시 돌려도(같은 비교 기준) 같은 이벤트를 두 번 저장하지 않는다.

        비교 기준 날짜를 넣어, 다시 열린 이슈가 다시 수정되는 것처럼 같은 전이가 다른 날 또 일어나면 새
        이벤트가 된다.
        """
        parts = [self.entity_type, self.entity_id, self.event_type, self.before, self.after]
        if self.basis:
            parts.append(self.basis)
        payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    @property
    def after_fingerprint(self) -> str:
        """바뀐 뒤 값만의 지문. 기간 분석이 이미 분석한 끝 상태를 다시 분석하지 않게 한다 (REQ-QAINTEL-027).

        더한·뺀 문장은 비교 기준(바뀌기 전 본문)에 따라 달라지는 값이라 끝 상태가 아니다.
        """
        end_state = {key: value for key, value in self.after.items() if key not in BEFORE_DEPENDENT_AFTER_KEYS}
        payload = json.dumps([self.entity_type, self.entity_id, self.event_type, end_state], ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    def as_dict(self) -> dict:
        data = asdict(self)
        data["fingerprint"] = self.fingerprint
        data["after_fingerprint"] = self.after_fingerprint
        return data


def _same(left, right) -> bool:
    return json.dumps(left, ensure_ascii=False, sort_keys=True, default=str) == json.dumps(right, ensure_ascii=False, sort_keys=True, default=str)


def already_known(event: dict, prior: list[dict]) -> bool:
    """기간 분석에서 이 이벤트의 끝 상태를 이미 아는가 (REQ-QAINTEL-027 순서 4).

    `prior` 는 같은 제품·같은 대상의 저장된 이벤트다(오래된 것부터).

    - 신규 이벤트는 같은 대상의 신규 이벤트가 있으면 안다.
    - 삭제·조회 안 됨 이벤트는 그 대상의 마지막 신규·삭제 이벤트가 같은 삭제면 안다.
    - 댓글 이벤트는 새 댓글 번호를 모두 이미 본 댓글 이벤트가 담고 있으면 안다.
    - 그 밖의 필드 이벤트는 바뀐 필드마다 그 대상의 가장 최근 값이 같으면 안다. 이틀에 걸쳐 제목과 본문이
      따로 바뀌어도, 기간 실행의 이벤트 하나(제목·본문)를 매일 실행의 두 이벤트로 알아본다.
    """
    if not prior:
        return False
    event_type = event["event_type"]
    after = event.get("after") or {}
    if event_type in (ISSUE_CREATED, SRS_CREATED):
        return any(item["event_type"] == event_type for item in prior)
    if event_type in (ISSUE_REMOVED, SRS_REMOVED):
        lifecycle = [item for item in prior if item["event_type"] in (ISSUE_CREATED, SRS_CREATED, ISSUE_REMOVED, SRS_REMOVED)]
        return bool(lifecycle) and lifecycle[-1]["event_type"] == event_type
    if event_type == ISSUE_COMMENT_ADDED:
        seen = {str(comment.get("id")) for item in prior if item["event_type"] == ISSUE_COMMENT_ADDED
                for comment in (item.get("after") or {}).get("comments") or [] if comment.get("id")}
        ids = [str(comment.get("id") or "") for comment in after.get("comments") or []]
        return bool(ids) and all(value and value in seen for value in ids)
    latest: dict = {}
    for item in prior:
        for key, value in (item.get("after") or {}).items():
            if key not in BEFORE_DEPENDENT_AFTER_KEYS and key not in ("comments", "significant_ids"):
                latest[key] = value
    fields = [key for key in after if key not in BEFORE_DEPENDENT_AFTER_KEYS]
    return bool(fields) and all(key in latest and _same(latest[key], after[key]) for key in fields)


# -- 댓글 --------------------------------------------------------------------


def comments_unread(item: dict | None) -> bool:
    """그 스냅샷의 댓글이 읽은 값이 아니라 전날 값을 옮겨 적은 것인가 (읽기 실패·상한 미룸, REQ-QAINTEL-004 4번).

    그런 이슈의 `comment_ids` 는 오늘 값이라 비교 기준으로 쓰면 읽지 않은 댓글을 이미 본 것으로 여긴다.
    """
    return bool(item and (item.get("comments_error") or item.get("comments_deferred")))


def new_comments(previous: dict, current: dict, previous_collected_at: str = "") -> list[dict]:
    """어제 없던 댓글. 번호를 알면 번호로, 모르면 어제 수집 시각 뒤에 쓴 댓글로 가린다."""
    if comments_unread(current) or current.get("comments") is None:
        return []
    prev_comments = previous.get("comments")
    prev_ids = {str(item.get("id")) for item in prev_comments or [] if item.get("id")}
    # 어제 댓글을 읽지 못했으면 어제 번호 목록은 읽은 댓글이 아니다. 옮겨 적은 댓글의 번호만 안다.
    listed = None if comments_unread(previous) else previous.get("comment_ids")
    prev_ids |= set(listed or [])
    known = prev_comments is not None or listed is not None
    fresh = []
    for comment in current["comments"]:
        if known:
            if comment.get("id") and comment["id"] not in prev_ids:
                fresh.append(comment)
        elif previous_collected_at and str(comment.get("created") or "") > previous_collected_at:
            fresh.append(comment)
    return fresh


def is_significant(comment: dict, min_chars: int, noise_patterns: tuple[str, ...] = ()) -> bool:
    """진행 상태만 알리는 댓글이 아닌가. 제품 규칙(`comment_noise_patterns`)은 기본 규칙에 더한다."""
    text = collapse(comment.get("text"))
    if len(text) < min_chars:
        return False
    for pattern in (*DEFAULT_COMMENT_NOISE, *(noise_patterns or ())):
        if re.fullmatch(pattern, text, re.IGNORECASE):
            return False
    return True


# -- 이슈 --------------------------------------------------------------------


def _changed(previous: dict, current: dict, names: tuple[str, ...]) -> list[str]:
    return [name for name in names if collapse(previous.get(name)) != collapse(current.get(name))]


def _values(item: dict, names) -> dict:
    return {name: item.get(name) for name in names}


def route_issue(event_type: str, rd_result: str, significant_comment: bool = False) -> tuple[list[str], str]:
    """이벤트 하나를 분석 종류로 나눈다 (REQ-QAINTEL-010 표). (분석 목록, 이유)."""
    fixed, spec_like = rd_result == RD_FIXED, rd_result in RD_SPEC_LIKE
    if event_type == ISSUE_CREATED:
        return [NEW_ISSUE], "새로 등록된 이슈"
    if event_type in (ISSUE_RD_RESULT_CHANGED, ISSUE_ROOT_CAUSE_CHANGED, ISSUE_ACTION_DETAILS_CHANGED):
        if fixed:
            return [FIXED_ISSUE], "연구소 결과가 수정 완료(FIXED)인 이슈의 변경"
        if spec_like:
            return [SPEC_DECISION], "연구소 결과가 사양대로·결함 아님인 이슈의 변경"
        return [], NO_ANALYSIS_REASON
    if event_type in (ISSUE_CONTENT_UPDATED, ISSUE_REPRODUCTION_UPDATED):
        if fixed:
            return [FIXED_ISSUE], "수정 완료 이슈의 본문·재현 절차 변경"
        if spec_like:
            return [SPEC_DECISION], "Spec 판정 이슈의 본문·재현 절차 변경"
        return [NEW_ISSUE], "처리 전 이슈의 본문·재현 절차가 바뀌어 다시 분석"
    if event_type == ISSUE_COMMENT_ADDED:
        if not significant_comment:
            return [], "새 댓글이 진행 상태 같은 의미 없는 내용뿐이라 기록만 남김"
        return ([COMMENT, FIXED_ISSUE] if fixed else [COMMENT]), "의미 있는 새 댓글"
    if event_type == ISSUE_STATUS_ONLY_CHANGED:
        return [], "상태만 바뀌어 AI 분석이 필요 없음"
    if event_type == ISSUE_METADATA_CHANGED:
        return [], "심각도·버전·연결 같은 속성만 바뀌어 기록만 남김"
    if event_type == ISSUE_REMOVED:
        return [], "오늘 조회되지 않아 기록만 남김"
    return [], "기록만 남김"


def _event(entity_id: str, event_type: str, previous: dict, current: dict, fields: list[str], rd_result: str,
           significant: bool = False, extra_after: dict | None = None) -> ChangeEvent:
    analyses, reason = route_issue(event_type, rd_result, significant)
    after = _values(current, fields)
    if extra_after:
        after.update(extra_after)
    return ChangeEvent(ENTITY_ISSUE, entity_id, event_type, _values(previous, fields), after, list(fields),
                       bool(analyses), reason, analyses)


def detect_issue_events(
    before: list[dict] | None,
    after: list[dict],
    *,
    previous_collected_at: str = "",
    comment_min_chars: int = 15,
    noise_patterns: tuple[str, ...] = (),
) -> list[ChangeEvent]:
    """이슈 스냅샷 두 개를 비교한다. 비교할 어제 스냅샷이 없으면 기준만 만든 것이라 이벤트가 없다."""
    if before is None:
        return []
    old = {item["id"]: item for item in before}
    new = {item["id"]: item for item in after}
    events: list[ChangeEvent] = []
    for issue_id in sorted(new.keys() - old.keys()):
        current = new[issue_id]
        events.append(_event(issue_id, ISSUE_CREATED, {}, current, ["title", "status", "rd_result"], current.get("rd_result", "")))
    for issue_id in sorted(old.keys() - new.keys()):
        previous = old[issue_id]
        events.append(ChangeEvent(ENTITY_ISSUE, issue_id, ISSUE_REMOVED, _values(previous, ("title", "status")), {}, [],
                                  False, route_issue(ISSUE_REMOVED, "")[1], []))
    for issue_id in sorted(new.keys() & old.keys()):
        previous, current = old[issue_id], new[issue_id]
        rd_result = current.get("rd_result", "")
        found: list[ChangeEvent] = []
        for event_type, names in (
            (ISSUE_CONTENT_UPDATED, CONTENT_FIELDS),
            (ISSUE_REPRODUCTION_UPDATED, REPRODUCTION_FIELDS),
            (ISSUE_RD_RESULT_CHANGED, ("rd_result_raw",)),
            (ISSUE_ROOT_CAUSE_CHANGED, ("occurrence_cause",)),
            (ISSUE_ACTION_DETAILS_CHANGED, ("action_details",)),
        ):
            changed = _changed(previous, current, names)
            if changed:
                fields = changed + (["rd_result"] if event_type == ISSUE_RD_RESULT_CHANGED else [])
                found.append(_event(issue_id, event_type, previous, current, fields, rd_result))
        fresh = new_comments(previous, current, previous_collected_at)
        if fresh:
            significant = [item for item in fresh if is_significant(item, comment_min_chars, noise_patterns)]
            event = _event(issue_id, ISSUE_COMMENT_ADDED, {}, {}, [], rd_result, bool(significant),
                           {"comments": fresh, "significant_ids": [item["id"] for item in significant]})
            event.changed_fields = ["comments"]
            found.append(event)
        metadata = _changed(previous, current, METADATA_FIELDS)
        if metadata:
            if metadata == ["status"] and not found:
                found.append(_event(issue_id, ISSUE_STATUS_ONLY_CHANGED, previous, current, metadata, rd_result))
            else:
                found.append(_event(issue_id, ISSUE_METADATA_CHANGED, previous, current, metadata, rd_result))
        events.extend(found)
    return events


# -- SRS ---------------------------------------------------------------------


def detect_srs_events(diff: SrsDiff) -> list[ChangeEvent]:
    if diff.baseline_only:
        return []
    events: list[ChangeEvent] = []
    for item in diff.added:
        events.append(ChangeEvent(ENTITY_SRS, item["id"], SRS_CREATED, {},
                                  {name: item.get(name, "") for name in ("old_id", "title", "status", "text")},
                                  ["old_id", "title", "status", "text"], True, "새 SRS", [SPEC_COVERAGE]))
    for item in diff.modified:
        after = dict(item["after"])
        for key in ("added_sentences", "removed_sentences"):
            if key in item:
                after[key] = item[key]
        events.append(ChangeEvent(ENTITY_SRS, item["id"], SRS_UPDATED, dict(item["before"]), after,
                                  list(item["fields"]), True, "SRS 비교 필드가 바뀜", [SPEC_COVERAGE]))
    for item in diff.removed:
        events.append(ChangeEvent(ENTITY_SRS, item["id"], SRS_REMOVED, {name: item.get(name, "") for name in ("old_id", "title")}, {},
                                  [], False, "삭제 SRS 를 가리키는 TC 는 코드가 찾는다(AI 분석 없음)", []))
    return events


def group_targets(events: list[dict]) -> dict[str, dict[str, list[dict]]]:
    """분석 종류 → 대상 번호 → 이벤트 목록. 같은 대상·같은 분석은 한 번만 돈다.

    저장된 이벤트는 아직 끝나지 않은 분석(`remaining_analyses`)만 다시 고른다. 이미 성공한 분석은 다시 돌지 않는다.
    """
    grouped: dict[str, dict[str, list[dict]]] = {kind: {} for kind in ANALYSIS_ORDER}
    for event in events:
        if not event.get("analysis_required"):
            continue
        kinds = event["remaining_analyses"] if "remaining_analyses" in event else event.get("analyses")
        for kind in kinds or []:
            if kind in grouped:
                grouped[kind].setdefault(event["entity_id"], []).append(event)
    return grouped
