"""실행 상세 화면에 보일 내용 만들기 (SPEC REQ-QAINTEL-020).

실행 기록(`qa_agent_runs`)의 내부 값(`total`, `type_SRS_UPDATED`, `dry-run`, `disabled`)을 처음 보는 사람이
읽을 수 있는 문장·숫자 카드·흐름으로 바꾼다. 템플릿은 이 결과만 반복해 그린다.
"""

from __future__ import annotations

import re
from urllib.parse import quote

from app.modules.daily_qa.change_events import ANALYSIS_LABELS, EVENT_LABELS
from app.modules.daily_qa.report import STAGE_LABELS, STATUS_LABELS
from app.modules.qa_agent.dashboard import sort_cards

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
TRIGGER_LABELS = {"scheduled": "예약 실행", "manual": "[변경사항 점검하기]", "manual_cli": "명령줄 실행", "catchup": "한도 초기화 뒤 다시 실행",
                  "prepare": "대상 준비", "collect": "수집만 실행", "deferred": "밀린 변경 분석"}
COLLECT_REASONS = {"policy_off": "자동 분석 꺼짐", "weekly_not_due": "주간 모드의 분석일 아님"}
CHANGE_MAIL_STATUS = {"sent": "보냄", "skipped": "보내지 않음", "failed": "실패", "not_configured": "보내지 않음(메일 설정 없음)"}


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


def _change_mail_line(mail) -> str:
    """수집·저장 직후 보낸 변경 리포트 메일의 결과 한 줄 (REQ-ALMCHG-004). 보낸 기록이 없으면 빈 문자열."""
    if not isinstance(mail, dict) or not mail.get("status"):
        return ""
    label = CHANGE_MAIL_STATUS.get(mail["status"], mail["status"])
    reason = str(mail.get("reason") or "").strip()
    if reason and not label.endswith(")"):
        label = f"{label}({reason})"
    return f"변경 리포트 메일: {label}"


def _tile(label: str, value, hint: str = "") -> dict:
    return {"label": label, "value": "-" if value is None else (f"{value:,}" if isinstance(value, int) else value), "hint": hint}


def _actions(run: dict) -> list[dict]:
    """실패 기록을 확인된 원인에 따른 QA 담당자 할 일로 바꾼다."""
    stages = run.get("stages") or {}
    summary = run.get("summary") or {}
    actions = []
    seen = set()

    def add(kind, message, todo):
        if kind not in seen:
            actions.append({"kind": kind, "message": message, "todo": todo})
            seen.add(kind)

    def add_claude_problem():
        kind = (summary.get("claude_limit") or {}).get("kind")
        if kind == "AUTH":
            add("auth", "Claude 인증을 확인하지 못해 분석을 끝내지 못했습니다.",
                "관리자에게 Claude 로그인과 서버 인증 설정 확인을 요청하고 해결 후 다시 실행하세요.")
        elif kind == "CLI_MISSING":
            add("cli", "Claude 실행 도구가 없어 분석을 끝내지 못했습니다.",
                "관리자에게 서버의 Claude CLI 설치와 실행 경로 확인을 요청하세요.")
        else:
            weekly = (summary.get("automation_policy") or {}).get("mode") == "weekly"
            add("limit", "Claude 사용량 한도에 걸려 남은 분석은 대기입니다.",
                "한도가 풀린 뒤 다시 요청하세요." if "on_demand" in stages else
                "남은 대상은 다음 예정 점검 또는 수동 선택 분석에서 이어 갑니다. 한도 초기화 직후 자동 재실행하지 않습니다." if summary.get('savings_enabled') else
                "다음 주 첫 업무일의 예정 점검이나 [변경사항 점검하기]에서 이어 갑니다." if weekly else
                "사용량 초기화 시각을 확인하세요. 한도가 풀리면 남은 분석을 이어서 합니다.")

    for key, stage in stages.items():
        state = stage.get("status")
        if state not in {"failed", "partial", "rules", "limit"}:
            continue
        note = str(stage.get("note") or "").lower()
        collection = key in {"collect_srs", "collect_issues"}
        if key in {"email", "mail"}:
            add("mail", "요약 메일을 보내지 못했습니다.", "화면에서 결과를 확인하고 관리자에게 메일 설정과 전송 오류 확인을 요청하세요.")
        elif re.search(r"\b(?:401|403)\b", note) or any(marker in note for marker in (
                "unauthorized", "forbidden", "login required", "not logged", "invalid api key", "authentication_error", "please run /login", "인증", "로그인")):
            service = "Polarion 토큰" if collection else "Claude 로그인"
            add("auth", f"{service} 인증을 확인하지 못해 점검을 끝내지 못했습니다.",
                f"관리자에게 {service} 확인을 요청하고 해결 후 다시 실행하세요.")
        elif collection:
            dns = any(marker in note for marker in ("name or service not known", "getaddrinfo failed", "temporary failure in name resolution", "nodename nor servname", "주소 풀이", "dns"))
            add("dns" if dns else "polarion", "Polarion 자료를 수집하지 못해 오늘 변경을 확인하지 못했습니다.",
                "관리자에게 서버 DNS와 Polarion 주소 확인을 요청하고 해결 후 다시 실행하세요." if dns else
                "관리자에게 서버의 Polarion 연결·주소·네트워크 확인을 요청하고 해결 후 다시 실행하세요.")
        elif state == "rules" or (key == "preflight" and "규칙" in note):
            add("rules", "QA 규칙 판을 확인하지 못해 분석을 끝내지 못했습니다.",
                "관리자에게 지원하는 QA 규칙 판과 파일 등록 확인을 요청하세요.")
        elif state == "limit":
            add_claude_problem()
        else:
            name = STAGE_LABELS.get(key, key)
            add("stage", f"{name} 단계에서 점검을 끝내지 못했습니다.", f"아래 실패한 단계의 기록을 확인하고 관리자에게 {name} 오류 확인을 요청하세요.")
    if summary.get("claude_limit"):
        add_claude_problem()
    if run.get("email_status") == "failed":
        add("mail", "요약 메일을 보내지 못했습니다.", "화면에서 결과를 확인하고 관리자에게 메일 설정과 전송 오류 확인을 요청하세요.")
    if not actions and run.get("status") in {"FAILED", "PARTIAL"}:
        add("stage", "점검을 끝까지 마치지 못했습니다.", "아래 실행 기록을 확인하고 관리자에게 오류 확인을 요청하세요.")
    return actions


def build(run: dict, events: list[dict], cards: list[dict], files: list[str]) -> dict:
    stages: dict = run.get("stages") or {}
    summary: dict = run.get("summary") or {}
    dry_run = bool(run.get("dry_run"))

    def counts(key: str) -> dict:
        return (stages.get(key) or {}).get("counts") or {}

    srs, issues, tc, ev = counts("collect_srs"), counts("collect_issues"), counts("tc_index"), counts("events")
    changed_srs = (srs.get("added", 0) + srs.get("removed", 0) + srs.get("modified", 0)) if srs else None
    tokens = (summary.get("token_usage") or {}).get("total_tokens")
    unknown_calls = (summary.get("token_usage") or {}).get("usage_unknown_calls", 0)
    cache = summary.get("result_cache") or {}
    cache_hits = cache.get("hits", summary.get("result_cache_hits", 0))
    cache_sources = [{**source, "url": f"/qa-agent/runs/{quote(str(source['run_id']), safe='')}"}
                     for source in cache.get("sources", []) if source.get("run_id")]
    token_hint = f"토큰 {tokens:,}" if isinstance(tokens, int) and tokens else ""
    if unknown_calls:
        recorded = f"기록된 토큰 {tokens:,} · " if isinstance(tokens, int) else ""
        token_hint = f"{recorded}사용량을 확인하지 못한 중단 호출 {unknown_calls}회"

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
        parts.append("Claude 사용량 한도에 걸려 요청 점검을 수행하지 못했습니다. 한도가 풀린 뒤 다시 요청하세요."
                     if "on_demand" in stages else
                     "Claude 사용량 한도에 걸려 남은 분석은 대기로 남겼고, 한도가 풀리면 이어서 합니다.")
    elif cards:
        parts.append(f"분석 결과 {len(cards)}건을 만들었습니다.")
    headline = " ".join(part for part in parts if part)
    actions = _actions(run)
    if actions:
        headline = " ".join(action["message"] for action in actions)
        if any(action["kind"] == "limit" for action in actions) and "on_demand" in stages:
            headline += " 한도가 풀린 뒤 다시 요청하세요."
    if not headline:
        headline = "이번 실행에서 확인한 변경이 없습니다." if run.get("status") == "NO_CHANGE" else "실행 기록을 확인하세요."
    if run.get("status") == "CANCELLED":
        headline = "사용자 요청으로 이번 점검을 중단했습니다. 이미 저장한 결과를 확인하세요."
    elif summary.get('run_kind') == 'collect':
        reason = COLLECT_REASONS.get(summary.get('collect_reason', ''), '')
        headline = (headline + ' ' if actions else '') + '수집만 실행했습니다' + (f'({reason})' if reason else '') + '. AI 분석은 하지 않았습니다.'
    elif summary.get('collection_only'):
        count = summary.get('prepared_targets')
        prepared = f'분석 대상 {count}개를 준비했습니다.' if count is not None else '분석 대상을 준비했습니다.'
        headline = (headline + ' ' if actions else '') + prepared + ' 아직 AI 분석을 실행하지 않았습니다. 점검 화면에서 대상을 선택하세요.'
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
        _tile("Claude 호출", summary.get("claude_calls"), token_hint),
    ]
    downloads = [{"name": name, "label": FILE_LABELS.get(name, "검증 TC 초안(Excel)" if name.endswith(".xlsx") else name)} for name in files]
    ordered = sort_cards(cards)
    attention = [item for item in ordered if item["level"] in {"사양과 다름", "검토 필요", "근거 부족"}]
    collection_failed = any((stages.get(key) or {}).get("status") in {"failed", "partial"} for key in ("collect_srs", "collect_issues"))
    analyzed = 0 if dry_run else sum((stage.get("counts") or {}).get("done", 0)
                                   for stage in stages.values() if "tasks" in (stage.get("counts") or {}))
    analyzed = max(0, analyzed - cache_hits)
    return {
        "headline": headline, "tiles": tiles, "flow": flow, "stages": rows, "downloads": downloads,
        "tone": "warn" if run.get("status") == "CANCELLED" else "bad" if run.get("status") == "FAILED" or any(row["tone"] == "bad" for row in rows) or run.get("email_status") == "failed" else "warn" if actions else "good",
        "actions": actions, "failed_stages": [row for row in rows if row["tone"] == "bad"],
        "key_tiles": [_tile("바뀐 것", None if collection_failed else ev.get("total", len(events))),
                      _tile("AI가 분석한 것", analyzed, f"재사용 {cache_hits}묶음" if cache_hits else ""), _tile("확인 필요", len(attention))],
        "cache": {"hits": cache_hits, "reused_findings": cache.get("reused_findings", 0), "sources": cache_sources},
        "priority_cards": attention[:10], "attention_count": len(attention), "remaining": max(0, len(attention) - 10),
        "other_counts": [(name, sum(item["level"] == name for item in cards)) for name in ("참고", "문제 없음")],
        "ai_settings": summary.get("ai_settings") or {},
        "mail": MAIL_LABELS.get(run.get("email_status") or "", run.get("email_status") or "-"),
        "trigger": "요청 점검" if "on_demand" in stages else TRIGGER_LABELS.get(summary.get("trigger", ""), summary.get("trigger", "") or "-"),
        "dry_run": dry_run,
        "change_mail": _change_mail_line(summary.get("change_mail")),
        "audit": audit_box,
        "audit_remaining": summary.get("issue_audit_remaining"),
    }
