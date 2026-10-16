"""기간 안 스냅샷을 하루씩 비교한 변경 목록 (SPEC REQ-ALMCHG-002).

저장된 스냅샷만 읽는다. Polarion·AI·DB 를 쓰지 않고, 같은 입력이면 같은 결과가 나온다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from app.modules.daily_qa import change_events as ev
from app.modules.daily_qa.holidays import KST
from app.modules.daily_qa.snapshots import KIND_ISSUES, KIND_SRS, SnapshotStore
from app.modules.daily_qa.srs_snapshot import diff_snapshots

KIND_LABELS = {
    "created": "신규 등록", "status": "상태 변경", "assignee": "담당자 변경", "comment": "댓글 추가",
    "content": "내용 수정", "rd_result": "연구소 결과 변경", "cause_action": "발생 원인·조치 내용 변경",
    "other": "기타 속성 변경", "removed": "조회에서 빠짐",
    "srs_created": "SRS 신규", "srs_modified": "SRS 수정", "srs_removed": "SRS 조회에서 빠짐",
}
FIELD_LABELS = {
    "title": "제목", "description": "설명", "reproduction_step": "재현 절차", "occurrence_cause": "발생 원인",
    "action_details": "조치 내용", "severity": "심각도", "occurred_versions": "발생 버전",
    "target_versions": "목표 버전", "linked_ids": "연결 항목", "old_id": "Legacy 번호", "status": "상태",
}
UNREAD_DETAIL = "작성 정보 미수집"
NO_RECORDS = "이 기간에 저장된 기록이 없습니다."


@dataclass(frozen=True)
class ChangeLine:
    day: str
    time: str
    entity_type: str
    entity_id: str
    title: str
    kind: str
    field: str = ""
    before: str = ""
    after: str = ""
    detail: str = ""
    comment_id: str = ""
    comment_text: str = ""


@dataclass
class EntityChanges:
    entity_type: str
    entity_id: str
    title: str
    link: str
    lines: list[ChangeLine] = field(default_factory=list)
    net: dict[str, tuple[str, str]] = field(default_factory=dict)
    reporter_id: str = ""        # 이슈를 처음 등록한 사람(SRS 는 비어 있다). 메일이 작성자별로 묶는다 (REQ-ALMCHG-004)
    reporter_name: str = ""


@dataclass
class ChangeReport:
    product: str
    since: str
    until: str
    compared_days: dict[str, list[str]] = field(default_factory=dict)
    issues: list[EntityChanges] = field(default_factory=list)
    srs: list[EntityChanges] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    notices: list[str] = field(default_factory=list)
    empty_reason: str = ""


def kst_day(iso: str) -> str:
    moment = _kst(iso)
    return moment.strftime("%Y-%m-%d") if moment else ""


def _kst(iso: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(iso or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).astimezone(KST)


def _text(value) -> str:
    return ", ".join(str(item) for item in value) if isinstance(value, (list, tuple)) else str(value or "")


def _user_names(snapshots: list[list[dict]]) -> dict[str, str]:
    names: dict[str, str] = {}
    for items in snapshots:
        for item in items:
            names.update({str(k): str(v) for k, v in (item.get("user_names") or {}).items() if v})
            if item.get("reporter_id") and item.get("reporter_name"):
                names[str(item["reporter_id"])] = str(item["reporter_name"])
    return names


def _reporters(snapshots: list[list[dict]], names: dict[str, str]) -> dict[str, tuple[str, str]]:
    """이슈 번호 → (작성자 번호, 이름). 뒤 기록이 앞 기록을 덮어서, 조회에서 빠진 이슈는 마지막으로 본 기록의 값이다."""
    result: dict[str, tuple[str, str]] = {}
    for items in snapshots:
        for item in items:
            person = str(item.get("reporter_id") or "")
            name = str(item.get("reporter_name") or "") or names.get(person, "")
            if person or name:
                result[item["id"]] = (person, name)
    return result


def _link(cfg, entity_id: str) -> str:
    host, project = str(cfg.polarion.host or "").rstrip("/"), cfg.polarion.project_id
    return f"{host}/polarion/redirect/project/{project}/workitem?id={entity_id}" if host and project else ""


class _Collector:
    """대상별 변경 줄. 같은 댓글 번호는 한 줄이고, 미수집 줄은 뒤 기록에서 읽히면 그 자리에서 바뀐다."""

    def __init__(self) -> None:
        self.lines: dict[str, list[ChangeLine]] = {}
        self.comments: dict[tuple[str, str], int] = {}     # (대상, 댓글 번호) → lines 위치
        self.unread: set[tuple[str, str]] = set()

    def add(self, line: ChangeLine) -> None:
        bucket = self.lines.setdefault(line.entity_id, [])
        if line.kind == "comment" and line.comment_id:
            key = (line.entity_id, line.comment_id)
            if key in self.comments:
                if key in self.unread and line.detail != UNREAD_DETAIL:
                    bucket[self.comments[key]] = line
                    self.unread.discard(key)
                return
            self.comments[key] = len(bucket)
            if line.detail == UNREAD_DETAIL:
                self.unread.add(key)
        bucket.append(line)


def _issue_lines(day: str, entity_id: str, before: dict, after: dict, events: list, names: dict[str, str]):
    title = (after or before).get("title", "")

    def make(kind, **values):
        return ChangeLine(day, values.pop("time", ""), "issue", entity_id, title, kind, **values)

    def people(ids):
        return ", ".join(names.get(str(i), str(i)) for i in ids or [])

    grouped: dict[str, list[str]] = {"content": [], "cause_action": []}
    for event in events:
        kind, changed = event.event_type, event.changed_fields
        if kind == ev.ISSUE_CREATED:
            yield make("created")
        elif kind == ev.ISSUE_REMOVED:
            yield make("removed")
        elif kind == ev.ISSUE_STATUS_ONLY_CHANGED:
            yield make("status", field="status", before=_text(event.before.get("status")), after=_text(event.after.get("status")))
        elif kind == ev.ISSUE_METADATA_CHANGED:
            for name in changed:
                old, new = event.before.get(name), event.after.get(name)
                if name == "status":
                    yield make("status", field=name, before=_text(old), after=_text(new))
                elif name == "assignees":
                    yield make("assignee", field=name, before=people(old), after=people(new))
                else:
                    yield make("other", field=name, before=_text(old), after=_text(new), detail=FIELD_LABELS.get(name, name))
        elif kind in (ev.ISSUE_CONTENT_UPDATED, ev.ISSUE_REPRODUCTION_UPDATED):
            grouped["content"] += changed
        elif kind == ev.ISSUE_RD_RESULT_CHANGED:
            yield make("rd_result", field="rd_result_raw", before=_text(event.before.get("rd_result_raw")),
                       after=_text(event.after.get("rd_result_raw")))
        elif kind in (ev.ISSUE_ROOT_CAUSE_CHANGED, ev.ISSUE_ACTION_DETAILS_CHANGED):
            grouped["cause_action"] += changed
        elif kind == ev.ISSUE_COMMENT_ADDED:
            for comment in event.after.get("comments") or []:
                moment = _kst(comment.get("created"))
                author = str(comment.get("author") or "")
                yield ChangeLine(moment.strftime("%Y-%m-%d") if moment else day,
                                 moment.strftime("%H:%M") if moment else "", "issue", entity_id, title, "comment",
                                 detail=names.get(author, author), comment_id=str(comment.get("id") or ""),
                                 comment_text=str(comment.get("text") or ""))
    for kind, fields in grouped.items():
        if fields:
            yield make(kind, field=fields[0] if len(fields) == 1 else "",
                       detail=", ".join(FIELD_LABELS.get(name, name) for name in fields))
    if after and ev.comments_unread(after):
        known = set((before or {}).get("comment_ids") or []) | {str(c.get("id")) for c in (before or {}).get("comments") or []}
        for comment_id in sorted(set(after.get("comment_ids") or []) - known):
            yield make("comment", detail=UNREAD_DETAIL, comment_id=str(comment_id))


def _srs_lines(day: str, before: list[dict], after: list[dict]):
    diff = diff_snapshots(before, after)

    def make(item, kind, **values):
        return ChangeLine(day, "", "srs", item["id"], item.get("title", ""), kind, **values)

    for item in diff.added:
        yield make(item, "srs_created")
    for item in diff.removed:
        yield make(item, "srs_removed")
    for entry in diff.modified:
        parts = []
        for name in entry["fields"]:
            if name == "text":
                parts.append(f"본문(더해진 문장 {len(entry.get('added_sentences', []))}, 빠진 문장 {len(entry.get('removed_sentences', []))})")
            else:
                parts.append(FIELD_LABELS.get(name, name))
        single = entry["fields"][0] if len(entry["fields"]) == 1 and entry["fields"] != ["text"] else ""
        yield make(entry, "srs_modified", detail=", ".join(parts), field=single,
                   before=_text(entry["before"].get(single)) if single else "",
                   after=_text(entry["after"].get(single)) if single else "")


def _net(first: list[dict], last: list[dict], names: dict[str, str]) -> dict[str, dict[str, tuple[str, str]]]:
    start = {item["id"]: item for item in first}
    result: dict[str, dict[str, tuple[str, str]]] = {}
    for item in last:
        old = start.get(item["id"])
        if old is None:
            continue
        net = {}
        if ev.collapse(old.get("status")) != ev.collapse(item.get("status")):
            net["status"] = (_text(old.get("status")), _text(item.get("status")))
        if "assignees" in old and "assignees" in item and ev.collapse(old["assignees"]) != ev.collapse(item["assignees"]):
            net["assignees"] = tuple(", ".join(names.get(str(i), str(i)) for i in side) for side in (old["assignees"], item["assignees"]))
        if net:
            result[item["id"]] = net
    return result


def _entities(cfg, entity_type: str, collector: _Collector, net: dict) -> list[EntityChanges]:
    result = []
    for entity_id, lines in collector.lines.items():
        lines = sorted(lines, key=lambda line: (line.day, line.time))    # 정렬은 안정적이라 같은 시각은 발견 순서
        result.append(EntityChanges(entity_type, entity_id, lines[-1].title, _link(cfg, entity_id), lines, net.get(entity_id, {})))
    result.sort(key=lambda e: (e.lines[-1].day, e.lines[-1].time, e.entity_id), reverse=True)
    return result


def _gap_notices(cfg, schedule: dict, days: list[str], since: str, until: str) -> list[str]:
    from app.modules.daily_qa.automation_policy import is_business_day

    have = set(days)
    business = []
    day, end = date.fromisoformat(since), date.fromisoformat(until)
    while day <= end:
        if day.isoformat() >= days[0] and is_business_day(cfg, schedule, day):
            business.append(day.isoformat())
        day += timedelta(days=1)
    runs: list[list[str]] = []
    for item in business:
        if item in have:
            runs.append([])
        elif runs:
            runs[-1].append(item)
        else:
            runs.append([item])
    notices = []
    for run in (r for r in runs if r):
        later = [d for d in days if d > run[-1]]
        if later:
            notices.append(f"{run[0]} ~ {run[-1]} 기록 없음: 이 사이 변경은 {later[0]}에 합쳐서 보입니다.")
    return notices


def build_change_report(cfg, snapshots: SnapshotStore, since: str, until: str, *, intel=None, noise_patterns=(),
                        schedule: dict | None = None) -> ChangeReport:
    if schedule is None:
        from app.modules.daily_qa.scheduled_jobs import schedule_settings
        schedule = schedule_settings()
    intel = intel or cfg.intelligence
    report = ChangeReport(cfg.product, since, until)
    loaded: dict[str, list[tuple[str, list[dict], dict]]] = {}
    for kind, key in ((KIND_ISSUES, "issues"), (KIND_SRS, "srs")):
        paths = snapshots.between(kind, since, until)
        loaded[key] = [(path.stem, snapshots.load(path) or [], snapshots.meta(path)) for path in paths]
        report.compared_days[key] = [stem for stem, _, _ in loaded[key]]
    counts = [len(v) for v in loaded.values()]
    if not any(counts):
        report.empty_reason = NO_RECORDS
        return report
    if max(counts) == 1:
        only = next(v[0][0] for v in loaded.values() if len(v) == 1)
        report.empty_reason = f"비교할 기록이 하나뿐입니다({only}). 다음 기록부터 변경이 보입니다."

    names = _user_names([items for _, items, _ in loaded["issues"]])
    issue_log, srs_log = _Collector(), _Collector()
    records = loaded["issues"]
    for (_, old, meta), (day, new, _) in zip(records, records[1:]):
        before, after = {i["id"]: i for i in old}, {i["id"]: i for i in new}
        events = ev.detect_issue_events(old, new, previous_collected_at=meta.get("collected_at", ""),
                                        comment_min_chars=intel.comment_min_chars, noise_patterns=tuple(noise_patterns))
        by_id: dict[str, list] = {}
        for event in events:
            by_id.setdefault(event.entity_id, []).append(event)
        for entity_id in sorted(set(by_id) | {i for i, item in after.items() if i in before and ev.comments_unread(item)}):
            for line in _issue_lines(day, entity_id, before.get(entity_id, {}), after.get(entity_id, {}),
                                     by_id.get(entity_id, []), names):
                issue_log.add(line)
    records = loaded["srs"]
    for (_, old, _), (day, new, _) in zip(records, records[1:]):
        for line in _srs_lines(day, old, new):
            srs_log.add(line)

    issue_net = _net(loaded["issues"][0][1], loaded["issues"][-1][1], names) if len(loaded["issues"]) > 1 else {}
    report.issues = _entities(cfg, "issue", issue_log, issue_net)
    reporters = _reporters([items for _, items, _ in loaded["issues"]], names)
    for entity in report.issues:
        entity.reporter_id, entity.reporter_name = reporters.get(entity.entity_id, ("", ""))
    report.srs = _entities(cfg, "srs", srs_log, {})
    for entity in (*report.issues, *report.srs):
        for line in entity.lines:
            report.counts[line.kind] = report.counts.get(line.kind, 0) + 1

    for key, rows in loaded.items():
        if not rows:
            continue
        days = report.compared_days[key]
        if days[0] > since:
            note = f"{days[0]} 전의 변경은 확인할 기록이 없습니다."
            if note not in report.notices:
                report.notices.append(note)
        for note in _gap_notices(cfg, schedule, days, since, until):
            if note not in report.notices:
                report.notices.append(note)
    return report
