"""변경 리포트 메일 (SPEC REQ-ALMCHG-004).

`ChangeReport`(저장된 스냅샷 비교 결과)를 메일 한 통으로 만든다. 번호·제목·변경 종류·이전/이후 값·댓글 작성자와
시각만 싣는다. 댓글 본문, 이슈 설명·재현 절차·원인·조치 문장, SRS 본문 문장, AI 판단은 싣지 않는다.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

from jinja2 import Environment, FileSystemLoader

from app.core.notifier import mail_subject
from app.modules.daily_qa import automation_policy
from app.modules.daily_qa.change_report import KIND_LABELS, ChangeReport, EntityChanges, build_change_report
from app.modules.daily_qa.holidays import KST

CLOSING = "이 메일은 수집 결과를 자동으로 정리한 것입니다. AI 판단은 들어 있지 않습니다."
BASELINE_NOTE = "첫 기록을 저장했습니다. 다음 업무일부터 변경을 보냅니다."
FAILURE_NOTE = "기준을 유지했고 다음 수집에서 다시 비교합니다."
NO_CHANGE = "변경 없음"
UNKNOWN_REPORTER = "작성자 미확인"
NO_GAP = "빠진 기록 없음"
AI_LABELS = {"daily": "켜짐", "weekly": "주간", "off": "꺼짐"}
WEEKDAYS = "월화수목금토일"
ISSUE_SUMMARY = (("created", "신규"), ("status", "상태 변경"), ("assignee", "담당자 변경"), ("comment", "댓글 추가"),
                 ("content", "내용 수정"), ("rd_result", "연구소 결과 변경"), ("cause_action", "발생 원인·조치 내용 변경"),
                 ("other", "기타 속성 변경"), ("removed", "조회에서 빠짐"))
SRS_SUMMARY = (("srs_created", "신규"), ("srs_modified", "수정"), ("srs_removed", "조회에서 빠짐"))
SRS_WORD = {"srs_created": "신규", "srs_modified": "수정", "srs_removed": "빠짐"}
VALUE_LABELS = {"status": "상태", "assignee": "담당자", "rd_result": "연구소 결과"}
#: 값(문장)을 메일에 싣지 않는 필드. 바뀐 곳 이름만 싣는다.
BODY_FIELDS = {"description", "reproduction_step", "occurrence_cause", "action_details"}
_TEMPLATES = Environment(loader=FileSystemLoader(str(Path(__file__).parent / "templates")), autoescape=True)


def day_label(day: str) -> str:
    """`2026-10-16` -> `10-16(금)`. 읽을 수 없으면 받은 값 그대로."""
    try:
        parsed = date.fromisoformat(day)
    except ValueError:
        return day
    return f"{parsed.strftime('%m-%d')}({WEEKDAYS[parsed.weekday()]})"


def page_url(cfg, product: str, since: str, until: str) -> str:
    path = f"/qa-agent/changes?product={quote(product)}&since={since}&until={until}"
    base = (getattr(cfg, "review_base_url", "") or "").rstrip("/")
    return f"{base}{path}"


def ai_state(store, cfg) -> dict:
    """화면과 같은 방식으로 읽은 자동 분석 상태와 밀린 변경 수."""
    mode = automation_policy.get_policy(store, cfg.slug)["mode"]
    return {"label": AI_LABELS.get(mode, mode), "deferred": len(store.list_events(product=cfg.slug, statuses=("deferred",)))}


def period_times(snapshots, report: ChangeReport) -> tuple[str, str]:
    """비교한 첫·마지막 기록을 수집한 한국 시각. 못 읽으면 빈 값."""
    from app.modules.daily_qa.snapshots import KIND_ISSUES, KIND_SRS

    for kind, key in ((KIND_ISSUES, "issues"), (KIND_SRS, "srs")):
        days = report.compared_days.get(key) or []
        if not days:
            continue
        stamps = []
        for day in (days[0], days[-1]):
            paths = snapshots.between(kind, day, day)
            raw = str(snapshots.meta(paths[0]).get("collected_at", "")) if paths else ""
            try:
                moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                stamps.append((moment if moment.tzinfo else moment.replace(tzinfo=KST)).astimezone(KST).strftime("%Y-%m-%d %H:%M"))
            except ValueError:
                stamps.append("")
        if all(stamps):
            return stamps[0], stamps[1]
    return "", ""


def _line_text(line) -> str:
    kind = line.kind
    if kind in ("created", "removed"):
        return KIND_LABELS[kind]
    if kind == "comment":
        return f"댓글 추가 · {line.detail}" if line.detail else "댓글 추가"
    if kind in ("content", "cause_action"):
        return KIND_LABELS[kind] + (f" · 바뀐 곳: {line.detail}" if line.detail else "")
    label = VALUE_LABELS.get(kind) or line.detail or KIND_LABELS.get(kind, kind)
    if kind == "other" and line.field in BODY_FIELDS:
        return f"{KIND_LABELS[kind]} · 바뀐 곳: {label}"
    if line.before or line.after:
        return f"{label} {line.before or '-'} → {line.after or '-'}"
    return f"{label} 변경" if kind in VALUE_LABELS else label


def _counts(report: ChangeReport, table) -> str:
    parts = [f"{label} {report.counts.get(kind, 0)}" for kind, label in table if report.counts.get(kind, 0)]
    return " · ".join(parts) or NO_CHANGE


def _issue_block(entity: EntityChanges) -> dict:
    return {"id": entity.entity_id, "title": entity.title, "link": entity.link,
            "rows": [{"day": day_label(line.day), "time": line.time, "text": _line_text(line)} for line in entity.lines]}


def _issue_groups(entities: list[EntityChanges]) -> list[dict]:
    """이슈를 작성자별로 묶는다. 작성자는 이름순, 이름을 모르면 맨 뒤 묶음이다. 묶음 안은 받은 순서(최근 변경 순)를 지킨다."""
    groups: dict[str, list[dict]] = {}
    for entity in entities:
        groups.setdefault(entity.reporter_name, []).append(_issue_block(entity))
    ordered = sorted(groups.items(), key=lambda pair: (not pair[0], pair[0]))
    return [{"name": name or UNKNOWN_REPORTER, "count": len(blocks), "issues": blocks} for name, blocks in ordered]


def _srs_row(entity: EntityChanges) -> dict:
    kinds = {line.kind for line in entity.lines}
    kind = "srs_created" if "srs_created" in kinds else entity.lines[-1].kind
    where = "; ".join(dict.fromkeys(line.detail for line in entity.lines if line.kind == "srs_modified" and line.detail))
    return {"word": SRS_WORD.get(kind, KIND_LABELS.get(kind, kind)), "id": entity.entity_id, "title": entity.title,
            "link": entity.link, "where": where}


def _pad(row: dict) -> str:
    return f"{row['day']} {row['time'] or '     '}"


def _text_body(view: dict) -> str:
    out: list[str] = []
    if view["mode"] == "failure":
        out += [view["headline"], "", *(f"- {item['name']}: {item['note'] or '이유 미상'}" for item in view["failed"]), "", FAILURE_NOTE]
    elif view["mode"] == "baseline":
        out.append(BASELINE_NOTE)
    else:
        out += [f"기간: {view['period']}", f"AI 분석: {view['ai']}", "",
                "[요약]", f"이슈: {view['issue_summary']}", f"SRS: {view['srs_summary']}", "", "[이슈 변경]"]
        if not view["issue_groups"]:
            out.append(NO_CHANGE)
        for group in view["issue_groups"]:
            out.append(f"■ {group['name']} (이슈 {group['count']}건)")
            for block in group["issues"]:
                out.append(f"  {block['id']} {block['title']}".rstrip())
                out += [f"    {_pad(row)}  {row['text']}" for row in block["rows"]]
                if block["link"]:
                    out.append(f"    Polarion: {block['link']}")
        if view["issue_more"]:
            out.append(f"나머지 {view['issue_more']}건은 화면에서 보기: {view['url']}")
        out += ["", "[SRS 변경]"]
        if not view["srs"]:
            out.append(NO_CHANGE)
        for row in view["srs"]:
            line = f"{row['word']}  {row['id']} {row['title']}".rstrip()
            out.append(line + (f" · 바뀐 곳: {row['where']}" if row["where"] else ""))
        if view["srs_more"]:
            out.append(f"나머지 {view['srs_more']}건은 화면에서 보기: {view['url']}")
        out += ["", f"[확인 범위] {' / '.join(view['notices'])}"]
    if view["url"]:
        out.append(f"전체 보기: {view['url']}")
    out += ["", CLOSING]
    return "\n".join(out)


def _render(view: dict) -> tuple[str, str]:
    html = _TEMPLATES.get_template("change_report_email.html").render(
        view=view, closing=CLOSING, failure_note=FAILURE_NOTE, baseline_note=BASELINE_NOTE, no_change=NO_CHANGE)
    return _text_body(view), html


def build_change_email(report: ChangeReport, *, product: str, page_url: str, ai_state: dict, max_items: int,
                       times: tuple[str, str] = ("", "")) -> tuple[str, str, str]:
    """(제목, 글 본문, HTML 본문)."""
    max_items = max(1, int(max_items))
    subject = mail_subject(product, f"변경 리포트 · {day_label(report.since)}~{day_label(report.until)}"
                                    f" · 이슈 {len(report.issues)}건 · SRS {len(report.srs)}건")
    compared = sorted({day for days in report.compared_days.values() for day in days})
    period = f"{times[0] or report.since} ~ {times[1] or report.until} · 비교한 기록: " + (", ".join(day[5:] for day in compared) or "없음")
    deferred = int(ai_state.get("deferred", 0) or 0)
    ai = f"{ai_state.get('label', '')} · 밀린 변경 {deferred}건" + ("은 화면의 [밀린 변경 분석]으로 분석할 수 있습니다" if deferred else "")
    notices = list(report.notices)
    if report.empty_reason:
        notices.insert(0, report.empty_reason)
    view = {
        "mode": "report", "heading": f"{product} 변경 리포트", "period": period, "ai": ai,
        "issue_summary": _counts(report, ISSUE_SUMMARY), "srs_summary": _counts(report, SRS_SUMMARY),
        "issue_groups": _issue_groups(report.issues[:max_items]),
        "issue_more": max(0, len(report.issues) - max_items),
        "srs": [_srs_row(entity) for entity in report.srs[:max_items]],
        "srs_more": max(0, len(report.srs) - max_items),
        "notices": notices or [NO_GAP], "url": page_url,
    }
    text, html = _render(view)
    return subject, text, html


def build_change_failure_email(product: str, stages: dict, *, kind: str, url: str = "") -> tuple[str, str, str]:
    """`kind`: `collect_failed`(수집 실패) 또는 `baseline`(첫 기록 저장)."""
    from app.modules.daily_qa.report import _plain

    if kind == "baseline":
        view = {"mode": "baseline", "heading": f"{product} 변경 리포트", "url": url}
        subject = mail_subject(product, "변경 리포트 · 첫 기록 저장")
    else:
        names = {"collect_srs": "SRS 수집", "collect_issues": "이슈 수집"}
        failed = [{"name": names[key], "note": _plain((stages.get(key) or {}).get("note", ""))}
                  for key in names if (stages.get(key) or {}).get("status") in ("failed", "skipped")]
        view = {"mode": "failure", "heading": f"{product} 변경 리포트 · 수집 실패", "headline": "수집에 실패했습니다.",
                "failed": failed, "url": url}
        subject = mail_subject(product, "변경 리포트 · 수집 실패")
    text, html = _render(view)
    return subject, text, html


def compose_for_period(cfg, store, snapshots, since: str, until: str) -> tuple[str, str, str, ChangeReport]:
    """저장된 기록만 읽어 기간의 변경 리포트 메일을 만든다. 자동·수동 메일이 같이 쓴다."""
    report = build_change_report(cfg, snapshots, since, until)
    subject, text, html = build_change_email(
        report, product=cfg.product, page_url=page_url(cfg, cfg.product, since, until), ai_state=ai_state(store, cfg),
        max_items=cfg.change_report.max_items, times=period_times(snapshots, report))
    return subject, text, html, report


def sample_report(today: date) -> ChangeReport:
    """시험 발송용 견본. 꺾쇠 안은 실제 값이 들어갈 자리다."""
    from app.modules.daily_qa.change_report import ChangeLine

    day = today.isoformat()

    def line(kind, entity_type="issue", title="<이슈 제목>", **values):
        return ChangeLine(day, values.pop("time", ""), entity_type, "VP-<번호>", title, kind, **values)

    issue = EntityChanges("issue", "VP-<번호>", "<이슈 제목>", "", [
        line("comment", time="<시각>", detail="<댓글 작성자>"),
        line("status", before="<이전>", after="<이후>"),
        line("assignee", before="<이전>", after="<이후>")], reporter_name="<이슈 작성자>")
    srs = EntityChanges("srs", "VP-<번호>", "<SRS 제목>", "", [
        line("srs_modified", "srs", "<SRS 제목>", detail="본문(더해진 문장 <n>, 빠진 문장 <n>)")])
    report = ChangeReport("예시", day, day, {"issues": [day], "srs": [day]}, [issue], [srs])
    report.counts = {"comment": 1, "status": 1, "assignee": 1, "srs_modified": 1}
    return report
