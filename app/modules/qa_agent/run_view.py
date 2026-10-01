"""실행 상세 화면에 보일 내용 만들기 (SPEC REQ-QAINTEL-020).

실행 기록(`qa_agent_runs`)의 내부 값(`total`, `type_SRS_UPDATED`, `dry-run`, `disabled`)을 처음 보는 사람이
읽을 수 있는 문장·숫자 카드·흐름으로 바꾼다. 템플릿은 이 결과만 반복해 그린다.
"""

from __future__ import annotations

import re

from app.modules.daily_qa.change_events import ANALYSIS_LABELS, EVENT_LABELS
from app.modules.daily_qa.report import STAGE_LABELS, STATUS_LABELS

#: 진행 흐름 네 칸과 그 칸에 드는 단계.
FLOW = (
    ("① 자료 모으기", ("preflight", "collect_srs", "collect_issues", "tc_index")),
    ("② 바뀐 것 찾기", ("events",)),
    ("③ AI 분석", (*ANALYSIS_LABELS.keys(), "E", "F", "B", "C")),
)
#: 상태 → 색 이름(템플릿 클래스).
TONE = {"ok": "good", "not_due": "idle", "skipped": "idle", "partial": "warn", "rules": "warn", "limit": "warn", "failed": "bad"}
_TONE_ORDER = {"idle": 0, "good": 1, "warn": 2, "bad": 3}

COUNT_LABELS = {
    "total": "전체", "added": "추가", "removed": "삭제", "modified": "변경", "comments_fetched": "댓글을 읽은 이슈",
    "comments_failed": "댓글 읽기 실패", "files": "파일", "rows": "TC 행", "analysis_required": "AI 분석 필요",
    "stored": "새로 저장", "detected": "감지", "tasks": "작업", "done": "끝난 작업", "failed": "실패한 작업",
    "accepted": "채택한 결과", "rejected": "근거 부족으로 뺀 결과", "srs_seen": "본 SRS", "srs_examined": "검사한 SRS",
    "tc_rows": "TC 행", "found": "찾은 항목", "new": "새 항목", "targets": "분석 대상",
}
MAIL_LABELS = {"disabled": "메일 보내지 않음", "sent": "메일 보냄", "failed": "메일 보내기 실패", "skipped": "메일 건너뜀", "": "-"}
FILE_LABELS = {
    "audit.json": "실행 감사 기록",
    "change_events.json": "변경 이벤트 목록",
    "srs_diff.json": "SRS 변경 내용",
    "issue_diff.json": "이슈 변경 내용",
    "findings.json": "분석 결과 목록",
}
_DAYS = {"mon": "월요일", "tue": "화요일", "wed": "수요일", "thu": "목요일", "fri": "금요일", "sat": "토요일", "sun": "일요일"}
TRIGGER_LABELS = {"scheduled": "예약 실행", "manual": "[지금 실행]", "manual_cli": "명령줄 실행", "catchup": "한도 초기화 뒤 다시 실행"}


def plain_note(note: str) -> str:
    """단계 메모 속 내부 낱말을 바꾼다."""
    text = str(note or "")
    text = re.sub(r"\bdry-run\b", "시험 실행", text)
    text = re.sub(r"매주 (mon|tue|wed|thu|fri|sat|sun)\b(?: (?=에))?", lambda m: f"매주 {_DAYS[m.group(1)]}", text)
    return text


def _label_counts(counts: dict) -> list[tuple[str, object]]:
    rows = []
    for key, value in (counts or {}).items():
        if key.startswith("type_"):
            name = EVENT_LABELS.get(key[5:], key[5:])
            rows.append((f"종류: {name}", value))
        else:
            rows.append((COUNT_LABELS.get(key, key), value))
    return rows


def _sentence(key: str, stage: dict, dry_run: bool) -> str:
    """단계 숫자를 한 문장으로."""
    c = stage.get("counts") or {}
    status = stage.get("status", "")
    if key == "collect_srs" and "total" in c:
        changed = c.get("added", 0) + c.get("removed", 0) + c.get("modified", 0)
        return f"SRS {c['total']:,}건을 읽었고 {changed:,}건이 바뀌었습니다(추가 {c.get('added', 0)} · 삭제 {c.get('removed', 0)} · 변경 {c.get('modified', 0)})."
    if key == "collect_issues" and "total" in c:
        text = f"이슈 {c['total']:,}건을 읽었습니다."
        if c.get("comments_fetched"):
            text += f" 그중 {c['comments_fetched']:,}건의 댓글을 새로 읽었습니다."
        if c.get("comments_failed"):
            text += f" 댓글 읽기 실패 {c['comments_failed']}건."
        return text
    if key == "tc_index" and "rows" in c:
        return f"TC 파일 {c.get('files', 0)}개에서 {c['rows']:,}행을 읽었습니다."
    if key == "events" and "total" in c:
        kinds = [f"{EVENT_LABELS.get(name[5:], name[5:])} {value}" for name, value in c.items() if name.startswith("type_")]
        text = f"바뀐 것 {c['total']:,}건을 찾았고, 그중 AI 분석이 필요한 것은 {c.get('analysis_required', 0):,}건입니다."
        return text + (f" ({', '.join(kinds)})" if kinds else "")
    if "tasks" in c:
        if dry_run:
            return f"작업 {c['tasks']}개를 준비했습니다. 시험 실행이라 AI 를 부르지 않았습니다."
        text = f"작업 {c['tasks']}개 가운데 {c.get('done', 0)}개를 끝냈습니다."
        if c.get("failed"):
            text += f" {c['failed']}개는 실패했습니다."
        if c.get("accepted") or c.get("rejected"):
            text += f" 결과 {c.get('accepted', 0)}건을 채택하고 근거가 부족한 {c.get('rejected', 0)}건은 뺐습니다."
        return text
    if key == "E" and "found" in c:
        return f"SRS {c.get('srs_examined', 0):,}건을 TC {c.get('tc_rows', 0):,}행과 맞춰 보고 빠진 연결 {c['found']}건을 찾았습니다."
    if status == "ok":
        return "끝났습니다."
    return ""


def _tile(label: str, value, hint: str = "") -> dict:
    return {"label": label, "value": "-" if value is None else (f"{value:,}" if isinstance(value, int) else value), "hint": hint}


def build(run: dict, events: list[dict], cards: list[dict], files: list[str]) -> dict:
    stages: dict = run.get("stages") or {}
    summary: dict = run.get("summary") or {}
    dry_run = bool(run.get("dry_run"))

    def counts(key: str) -> dict:
        return (stages.get(key) or {}).get("counts") or {}

    srs, issues, tc, ev = counts("collect_srs"), counts("collect_issues"), counts("tc_index"), counts("events")
    changed_srs = (srs.get("added", 0) + srs.get("removed", 0) + srs.get("modified", 0)) if srs else None
    tokens = (summary.get("token_usage") or {}).get("total_tokens")

    rows = []
    for key, stage in stages.items():
        status = stage.get("status", "")
        rows.append({
            "key": key, "label": STAGE_LABELS.get(key, key), "status": STATUS_LABELS.get(status, status),
            "tone": TONE.get(status, "idle"), "sentence": _sentence(key, stage, dry_run),
            "note": plain_note(stage.get("note", "")), "details": _label_counts(stage.get("counts") or {}),
        })
    by_key = {row["key"]: row for row in rows}

    flow = []
    for title, keys in FLOW:
        members = [by_key[key] for key in keys if key in by_key]
        # 칸 색은 그 안에서 가장 나쁜 단계를 따른다. 모두 건너뛰었으면 회색이다.
        tone = max((row["tone"] for row in members), key=_TONE_ORDER.__getitem__, default="idle")
        flow.append({"title": title, "tone": tone, "steps": members})
    flow.append({"title": "④ 결과", "tone": "good" if cards else "idle",
                 "steps": [{"label": f"분석 결과 {len(cards)}건 · 변경 이벤트 {len(events)}건", "status": "", "tone": "idle"}]})

    parts = []
    if srs or issues:
        read = " · ".join(text for text in (f"SRS {srs['total']:,}건" if "total" in srs else "",
                                           f"이슈 {issues['total']:,}건" if "total" in issues else "") if text)
        parts.append(f"Polarion 에서 {read}을 읽었습니다." if read else "")
    elif (stages.get("collect_srs") or {}).get("status") == "skipped":
        parts.append("Polarion 을 새로 읽지 않고 저장된 스냅샷을 썼습니다.")
    if summary.get("issue_audit"):
        audit_info = summary["issue_audit"]
        parts.append(f"이슈 변경 기록이 없어 현재 상태 기준으로 점검 대상 {audit_info.get('targets', 0):,}건을 골랐습니다"
                     f"(대상 아님 {audit_info.get('not_target', 0):,}건).")
    elif ev:
        parts.append(f"바뀐 것 {ev.get('total', 0):,}건을 찾았고 AI 분석이 필요한 것은 {ev.get('analysis_required', 0):,}건입니다.")
    if dry_run:
        parts.append("시험 실행이라 저장·AI 분석·메일은 하지 않았습니다.")
    elif summary.get("claude_limit"):
        parts.append("Claude 사용량 한도에 걸려 남은 분석은 대기로 남겼고, 한도가 풀리면 이어서 합니다.")
    elif cards:
        parts.append(f"분석 결과 {len(cards)}건을 만들었습니다.")
    headline = " ".join(part for part in parts if part)
    audit = summary.get("issue_audit") or {}
    audit_box = None
    if audit:
        # 현재 상태 기준 점검 알림과 묶음별 수 (REQ-QAINTEL-030 결과).
        audit_box = {"notice": audit.get("notice", ""), "since": audit.get("since", ""), "until": audit.get("until", ""),
                     "rows": [("이슈 전체", audit.get("issues_seen", 0)), ("기간 안 생성", audit.get("created_in_period", 0)),
                              ("기간 안 수정", audit.get("updated_in_period", 0)), ("기간 안 바뀐 SRS", audit.get("changed_srs", 0)),
                              *[(name, count) for name, count in (audit.get("by_category") or {}).items()],
                              ("대상 아님", audit.get("not_target", 0)), ("참고만(검증 전 상태)", audit.get("reference_only", 0))]}

    tiles = [
        _tile("읽은 SRS", srs.get("total")), _tile("바뀐 SRS", changed_srs),
        _tile("읽은 이슈", issues.get("total")), _tile("TC 행", tc.get("rows")),
        _tile("AI 분석이 필요한 변경", ev.get("analysis_required") if ev else None),
        _tile("분석 결과", len(cards)),
        _tile("Claude 호출", summary.get("claude_calls"), f"토큰 {tokens:,}" if isinstance(tokens, int) and tokens else ""),
    ]
    downloads = [{"name": name, "label": FILE_LABELS.get(name, "검증 TC 초안(Excel)" if name.endswith(".xlsx") else name)} for name in files]
    return {
        "headline": headline, "tiles": tiles, "flow": flow, "stages": rows, "downloads": downloads,
        "mail": MAIL_LABELS.get(run.get("email_status") or "", run.get("email_status") or "-"),
        "trigger": TRIGGER_LABELS.get(summary.get("trigger", ""), summary.get("trigger", "") or "-"),
        "dry_run": dry_run,
        "audit": audit_box,
        "audit_remaining": summary.get("issue_audit_remaining"),
    }
