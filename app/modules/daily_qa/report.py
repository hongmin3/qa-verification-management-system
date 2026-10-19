"""실행 요약과 메일 본문 (SPEC REQ-DAILY-008).

메일에는 건수와 상태만 넣는다. SRS·이슈 본문, AI 판단 문장은 넣지 않는다 — 메일은 사내
시스템 밖으로 전달되기 쉽고, 자세한 내용은 검토 화면에서 본다.
"""

from __future__ import annotations

import html
import re
from collections import Counter
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app.core.notifier import mail_subject

from app.modules.daily_qa import claude_limits
from app.modules.daily_qa.change_events import ANALYSIS_LABELS, EVENT_LABELS
from app.modules.daily_qa.schema import SKILL_B, SKILL_C, SKILL_E, SKILL_F, SKILL_LABELS

STAGE_LABELS = {
    "preflight": "사전 점검",
    "collect_srs": "SRS 수집",
    "collect_issues": "이슈 수집",
    "tc_index": "TC 색인",
    "email": "메일",
    "events": "변경 감지",
    "cancel": "실행 중단",
    **ANALYSIS_LABELS,
    "E": SKILL_LABELS[SKILL_E],
    "F": SKILL_LABELS[SKILL_F],
    # 개편 전 실행 기록의 단계
    "B": SKILL_LABELS[SKILL_B],
    "C": SKILL_LABELS[SKILL_C],
}

STATUS_LABELS = {
    "ok": "완료",
    "skipped": "건너뜀",
    "failed": "실패",
    "rules": "규칙 판 불일치",
    "partial": "일부 실패",
    "not_due": "오늘은 대상 아님",
    "limit": "Claude 사용량 한도",
    "cancelled": "사용자 중단",
}


def summarize(findings: list[dict], questions: int) -> dict:
    by_skill = Counter(item["skill"] for item in findings)
    by_verdict: dict[str, Counter] = {}
    for item in findings:
        by_verdict.setdefault(item["skill"], Counter())[item["verdict"]] += 1
    return {
        "findings": len(findings),
        "by_skill": dict(by_skill),
        "by_verdict": {skill: dict(counter) for skill, counter in by_verdict.items()},
        "questions": questions,
    }


def _stage_lines(stages: dict) -> list[tuple[str, str, str]]:
    lines = []
    for key, label in STAGE_LABELS.items():
        stage = stages.get(key)
        if not stage:
            continue
        lines.append((label, STATUS_LABELS.get(stage.get("status", ""), stage.get("status", "")), str(stage.get("note", ""))[:200]))
    return lines


def _event_line(summary: dict) -> str:
    events = summary.get("events") or {}
    if not events:
        return "변경 없음"
    return ", ".join(f"{EVENT_LABELS.get(name, name)} {count}" for name, count in sorted(events.items()))


def _audit_line(audit: dict) -> str:
    parts = ", ".join(f"{name} {count}" for name, count in (audit.get("by_category") or {}).items())
    return (f"{audit.get('since', '')} ~ {audit.get('until', '')} · 이슈 전체 {audit.get('issues_seen', 0)}건 · "
            f"기간 안 생성 {audit.get('created_in_period', 0)}건 · 수정 {audit.get('updated_in_period', 0)}건 · "
            f"점검 대상 {audit.get('targets', 0)}건({parts}) · 대상 아님 {audit.get('not_target', 0)}건")


def build_email(run_id: str, status_label: str, stages: dict, summary: dict, review_url: str, rules_warning: str,
                product: str = "", usage: dict | None = None) -> tuple[str, str, str]:
    """(제목, 글 본문, HTML 본문). 제목은 `mail_subject` 에서만 만든다 (REQ-MAIL-001).

    `usage` 는 `claude_usage.reading_for_run` 의 값이다. 절약 모드 메일(REQ-QAINTEL-048)에서만 쓴다.
    """
    if summary.get('savings_enabled'):
        return _workflow_email(run_id, status_label, stages, summary, review_url, rules_warning, product, usage)
    collection_only = bool(summary.get("collection_only"))
    lead = "변경 목록 · AI 미분석 · " if collection_only else ""
    subject = mail_subject(product, f"{lead}{run_id} {status_label} · Finding {summary.get('findings', 0)}건 · 질문 {summary.get('questions', 0)}건")
    limit = summary.get("claude_limit") or {}
    collection_failed = any((stages.get(key) or {}).get("status") in {"failed", "partial"}
                            for key in ("collect_srs", "collect_issues"))
    failures = [f"{STAGE_LABELS.get(key, key)}: {stage.get('note') or STATUS_LABELS.get(stage.get('status'), '')}"
                for key, stage in stages.items() if stage.get("status") in {"failed", "partial", "rules"}]
    title = (limit.get("label") or claude_limits.KIND_LABELS.get(limit.get("kind"), "AI 분석 중단")) if limit else (
        "수집 실패" if collection_failed else "점검 실패" if failures else "")
    if title:
        subject = mail_subject(product, f"{lead}{title} · {run_id} {status_label} · Finding {summary.get('findings', 0)}건 · 질문 {summary.get('questions', 0)}건")
    change = "수집 실패로 변경을 확인하지 못했습니다" if collection_failed else _event_line(summary)
    notices = list(failures[:4])
    if limit:
        notices.insert(0, limit.get("description") or title)
        if limit.get("kind") in {claude_limits.KIND_AUTH, claude_limits.KIND_CLI_MISSING}:
            notices.append("Claude 설치·로그인 설정을 확인한 뒤 다시 실행하세요.")
        elif summary.get("automatic_resume_enabled") is False:
            notices.append("자동 예약이 꺼져 있습니다. 한도가 풀린 뒤 [지금 실행]에서 이어 가세요.")
        elif (summary.get("automation_policy") or {}).get("mode") == "weekly":
            notices.append("남은 분석은 다음 주 첫 업무일의 예정 점검이나 [지금 실행]에서 이어 갑니다.")
        else:
            notices.append("한도가 풀린 뒤 자동 재개합니다. 화면에서 [지금 실행]으로도 이어 갈 수 있습니다.")
    if title:
        notices.append(f"대기 변경 {summary.get('pending_analysis_events', 0)}건 · 실행 상세에서 오류와 저장된 결과를 확인하세요.")
    text = []
    text += [*notices, ""] if notices else []
    if rules_warning:
        text += [f"※ {rules_warning}", ""]
    audit = summary.get("issue_audit") or {}
    if audit:
        # 이슈 기록이 없어 현재 상태 기준으로 점검했다는 알림 (REQ-QAINTEL-030 결과).
        text += [f"※ {audit.get('notice', '')}", _audit_line(audit), ""]
    text += [f"실행 ID: {run_id}", f"결과: {status_label}", f"오늘 변경: {change}",
             f"새 Claude 호출 {summary.get('claude_calls', 0)}회 · 기존 분석 재사용 {summary.get('result_cache_hits', 0)}묶음", "", "[단계]"]
    for label, status, note in _stage_lines(stages):
        text.append(f"- {label}: {status}" + (f" ({note})" if note else ""))
    text += ["", "[Skill 별 Finding]"]
    for skill, count in summary.get("by_skill", {}).items():
        verdicts = ", ".join(f"{name} {number}" for name, number in summary.get("by_verdict", {}).get(skill, {}).items())
        text.append(f"- {SKILL_LABELS.get(skill, skill)}: {count}건 ({verdicts})")
    if not summary.get("by_skill"):
        text.append("- 새 Finding 없음")
    text += ["", f"답이 필요한 질문: {summary.get('questions', 0)}건", "", f"검토 화면: {review_url}",
             "", "모든 결과는 AI 초안입니다. 최종 판정과 반영은 QA 가 합니다."]

    rows = "".join(
        f"<tr><td>{html.escape(label)}</td><td>{html.escape(status)}</td><td>{html.escape(note)}</td></tr>"
        for label, status, note in _stage_lines(stages)
    )
    skills = "".join(
        f"<li>{html.escape(SKILL_LABELS.get(skill, skill))}: <b>{count}</b>건</li>"
        for skill, count in summary.get("by_skill", {}).items()
    ) or "<li>새 Finding 없음</li>"
    warning = f"<p style='color:#b00020'><b>※ {html.escape(rules_warning)}</b></p>" if rules_warning else ""
    warning += "".join(f"<p style='color:#b00020'>{html.escape(str(notice))}</p>" for notice in notices)
    if audit:
        warning += f"<p style='color:#92400e'><b>※ {html.escape(audit.get('notice', ''))}</b><br>{html.escape(_audit_line(audit))}</p>"
    body = (
        f"<div style='font-family:sans-serif'>{warning}<h3>QA Agent {html.escape(product)} {html.escape(run_id)} — {html.escape(status_label)}</h3>"
        f"<p>오늘 변경: {html.escape(change)}</p>"
        f"<p>새 Claude 호출 {summary.get('claude_calls', 0)}회 · 기존 분석 재사용 {summary.get('result_cache_hits', 0)}묶음</p>"
        f"<table border='1' cellpadding='4' cellspacing='0'><tr><th>단계</th><th>상태</th><th>비고</th></tr>{rows}</table>"
        f"<h4>Skill 별 Finding</h4><ul>{skills}</ul>"
        f"<p>답이 필요한 질문: <b>{summary.get('questions', 0)}</b>건</p>"
        f"<p><a href='{html.escape(review_url)}'>검토 화면 열기</a></p>"
        "<p style='color:#666'>모든 결과는 AI 초안입니다. 최종 판정과 반영은 QA 가 합니다.</p></div>"
    )
    if collection_only:
        text.insert(0, COLLECTION_ONLY_NOTICE)
        body = f"<p><b>AI 미분석</b> · {html.escape(COLLECTION_ONLY_HINT)}</p>" + body
    return subject, "\n".join(text), body


NO_LINK = "서버 주소 미설정 · 설정 탭에서 입력"
COLLECTION_ONLY_HINT = "대상 목록에서 필요한 항목을 선택하세요."
COLLECTION_ONLY_NOTICE = "AI 분석을 실행하지 않았습니다. " + COLLECTION_ONLY_HINT
TONES = {"ok": {"color": "#1f8a4c"}, "warn": {"color": "#d97706"}, "fail": {"color": "#c62828"}}
LEVEL_COLORS = {"사양과 다름": "#c62828", "검토 필요": "#d97706", "근거 부족": "#6b7280", "참고": "#2563eb", "문제 없음": "#1f8a4c"}
#: 메일 본문에 내보내지 않는 내부 표현. 점검 기록 화면에서만 본다 (REQ-QAINTEL-048).
_INTERNAL_WORDS = re.compile(r"[\w.-]*\.json\b|Gate\s*G\d+|상한 초과로 미룸|묶음|task[-_]\w+", re.IGNORECASE)
_NOT_PROBLEM_LEVELS = ("문제 없음", "참고")
_TEMPLATES = Environment(loader=FileSystemLoader(str(Path(__file__).parent / "templates")), autoescape=True)


def _plain(text: object, limit: int = 160) -> str:
    """내부 표현을 지운 한 줄. 지우고 남은 말이 없으면 빈 문자열이다."""
    cleaned = re.sub(r"\s+", " ", _INTERNAL_WORDS.sub("", str(text or ""))).strip(" ·,;")
    return cleaned[:limit]


def _moment(value):
    from datetime import datetime, timezone
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return datetime.min.replace(tzinfo=timezone.utc)


def _kst_stamp(value) -> str:
    moment = _moment(value)
    return moment.astimezone(claude_limits.KST).strftime("%Y-%m-%d %H:%M") if moment.year > 1 else "미확인"


def _percent(value) -> str:
    return f"{value:g}%"


def _tone(status_label: str) -> str:
    if status_label == "실패":
        return "fail"
    if "일부" in status_label or "중단" in status_label:
        return "warn"
    return "ok"


def _review_counts(summary: dict) -> tuple[int, int]:
    """(새 결과, 검토 필요). 검토 필요는 결론 단계가 `문제 없음`·`참고`가 아닌 새 결과다."""
    from app.modules.qa_agent.dashboard import VERDICT_LEVEL
    total = review = 0
    for verdicts in (summary.get("by_verdict") or {}).values():
        for verdict, count in verdicts.items():
            total += count
            if VERDICT_LEVEL.get(verdict, "검토 필요") not in _NOT_PROBLEM_LEVELS:
                review += count
    return (total or int(summary.get("findings") or 0)), review


def _subject_date(run_id: str, summary: dict) -> str:
    from datetime import datetime
    try:
        return datetime.strptime(run_id[:8], "%Y%m%d").strftime("%m/%d")
    except ValueError:
        return ""


def _kst_time(value: str):
    """`2026-10-15 04:00` 또는 실행 번호 머리(`20261015-040000`)를 한국 시간으로 읽는다. 못 읽으면 None."""
    from datetime import datetime
    text = str(value or "").strip()
    for pattern, size in (("%Y-%m-%d %H:%M", 16), ("%Y%m%d-%H%M%S", 15)):
        try:
            return datetime.strptime(text[:size], pattern).replace(tzinfo=claude_limits.KST)
        except ValueError:
            continue
    return None


def _five_hour_text(before: dict, after: dict, run_id: str) -> tuple[str, str]:
    """(5시간 사용량 문장, 증가량). 증가량은 실행 전 값이 실행 시작 5시간 안에 측정됐고 같은 창일 때만 쓴다.

    이유: 5시간 창이 그 사이 초기화됐으면 두 값을 빼도 이번 실행이 쓴 양이 아니다. 값을 지어내지 않는다.
    """
    from datetime import timedelta
    old, new = before.get("five_hour"), after.get("five_hour")
    measured = str(before.get("measured_at") or "")
    head = ("실행 전 " + _percent(old) + (f" ({measured} 측정)" if measured else "")) if old is not None else "실행 전 값 미확인"
    if new is None:
        return (head + " → 실행 후 값 없음") if old is not None else "사용률 미확인", ""
    text = head + " → 실행 후 " + _percent(new)
    if old is None:
        return text, ""
    started, taken = _kst_time(run_id), _kst_time(measured)
    reset_before = _kst_time(before.get("five_hour_resets_kst"))
    reset_after = _kst_time(after.get("five_hour_resets_kst"))
    if new < old or (reset_before and started and reset_before <= started) or (reset_before and reset_after and reset_before != reset_after):
        return text + " (그 사이 초기화됨)", ""
    if started and taken and timedelta(minutes=-5) <= started - taken <= timedelta(hours=5):
        delta = f"+{new - old:g}%p"
        return f"{text} ({delta})", delta
    return text, ""


def _usage_context(usage: dict | None, summary: dict, run_id: str = "") -> dict:
    """사용량 구획. `usage` 가 없으면 기록이 없다고만 쓰고 값을 지어내지 않는다."""
    if not usage:
        return {"empty": "사용량 기록 없음 (이번 실행의 호출 기록을 읽지 못했습니다)", "lines": []}
    before, after = usage.get("before") or {}, usage.get("after") or {}
    model = usage.get("model") or (summary.get("ai_settings") or {}).get("analysis_model") or "확인 못 함"
    context: dict = {"empty": "", "calls": int(usage.get("calls") or 0), "tokens": f"{int(usage.get('total_tokens') or 0):,}", "model": model}
    if not context["calls"]:
        context["empty"] = "이번 실행은 AI를 호출하지 않았습니다."
    five, delta = _five_hour_text(before, after, run_id)
    context["five"] = {"text": five, "delta": delta, "resets": after.get("five_hour_resets_kst") or ""}
    week_after = after.get("seven_day")
    context["week"] = {"text": "실행 후 " + _percent(week_after) if week_after is not None else "사용률 미확인",
                       "resets": after.get("seven_day_resets_kst") or ""}
    context["targets"] = [f"{item['subject']} {int(item['tokens']):,}" for item in usage.get("by_target") or []]
    context["lines"] = [
        f"호출 {context['calls']}회 · 총 토큰 {context['tokens']} · 모델 {model}",
        "5시간 사용량: " + context["five"]["text"] + (f" · 초기화 {context['five']['resets']}" if context["five"]["resets"] else ""),
        "주간 사용량: " + context["week"]["text"] + (f" · 초기화 {context['week']['resets']}" if context["week"]["resets"] else ""),
    ]
    if context["targets"]:
        context["lines"].append("대상별 토큰: " + " · ".join(context["targets"]))
    return context


def _issue_groups(rows, link):
    """이슈 결과를 최초 작성자별로 묶는다. 작성자는 이름순(이름 없는 쪽이 뒤), 이슈는 업데이트 최신순이다."""
    from app.modules.qa_agent.dashboard import VERDICT_LEVEL
    groups: dict = {}
    for row in rows:
        person = str(row.get("reporter_id") or "")
        group = groups.setdefault(person, {"name": row.get("reporter_name") or "이름 미확인", "issues": {}})
        issue = group["issues"].setdefault(row["subject"], {"updated": row.get("issue_updated_at") or "", "title": "", "rows": []})
        issue["title"] = issue["title"] or str(row.get("subject_title") or "")
        level = VERDICT_LEVEL.get(row.get("verdict"), "검토 필요")
        issue["rows"].append({"kind": ANALYSIS_LABELS.get(row.get("analysis_type"), "이슈 분석"), "level": level,
                              "color": LEVEL_COLORS[level], "url": link(f"/qa-agent/findings/{row['id']}")})
    result = []
    for person, group in sorted(groups.items(), key=lambda pair: (not bool(pair[0]), pair[1]["name"], pair[0])):
        issues = []
        for subject, issue in sorted(group["issues"].items(), key=lambda pair: (_moment(pair[1]["updated"]), pair[0]), reverse=True):
            issues.append({"subject": subject, "title": issue["title"], "updated": _kst_stamp(issue["updated"]), "rows": issue["rows"]})
        result.append({"name": group["name"], "id": person or "ID 미확인", "issues": issues})
    return result


def _spec_rows(rows, link):
    from app.modules.qa_agent.dashboard import VERDICT_LEVEL
    result = []
    for row in rows:
        level = VERDICT_LEVEL.get(row.get("verdict"), "검토 필요")
        result.append({"subject": row.get("subject") or "", "title": str(row.get("subject_title") or ""),
                       "kind": ANALYSIS_LABELS.get(row.get("analysis_type"), "사양 분석"), "level": level,
                       "color": LEVEL_COLORS[level], "url": link(f"/qa-agent/findings/{row['id']}")})
    return result


def _remaining_lines(stages, summary, rules_warning, limit) -> list[str]:
    lines = []
    if limit:
        lines.append(_plain(limit.get("description") or limit.get("label") or "Claude 사용량 한도에 닿아 분석을 멈췄습니다."))
    if rules_warning:
        lines.append("지침 확인 필요: " + _plain(rules_warning))
    for label, status, note in _stage_lines(stages):
        if status not in ("완료", "건너뜀", "오늘은 대상 아님"):
            plain = _plain(note)
            lines.append(f"{label}: {status}" + (f" · {plain}" if plain else " · 자세한 이유는 점검 기록에서 확인하세요."))
    guard = summary.get("usage_guard") or {}
    if guard:
        resets = f" 초기화 {guard['reset_kst']} 뒤" if guard.get("reset_kst") else " 한도가 풀린 뒤"
        lines.append(f"{guard.get('reason')}로 새 호출을 멈췄습니다. 남은 대상은 대기로 두었고{resets} 자동으로 이어서 분석합니다. 완료로 처리하지 않았습니다.")
    elif summary.get("call_budget_exhausted"):
        lines.append("사용률 기준에 닿아 남은 대상은 대기로 두었습니다. 완료로 처리하지 않았습니다.")
    return lines


def _workflow_context(run_id, status_label, stages, summary, review_url, rules_warning, product, usage) -> dict:
    from urllib.parse import urlencode, urlsplit
    parsed = urlsplit(review_url)
    base = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else ""

    def link(path: str) -> str:
        return base + path if base else ""

    def results_link(category: str) -> str:
        return link("/qa-agent/results?" + urlencode({"product": product, "run_id": run_id, "category": category}))

    targets = summary.get("scope_targets") or {}
    reused = summary.get("target_reused") or []
    pending = summary.get("pending_analysis_events", 0)
    new_results, review_needed = _review_counts(summary)
    limit = summary.get("claude_limit") or {}
    guard = summary.get("usage_guard") or {}
    tone = _tone(status_label)
    collection_only = bool(summary.get("collection_only"))
    title = " · ".join(part for part in (
        "변경 목록 · AI 미분석" if collection_only else "",
        f"{_subject_date(run_id, summary)} 점검".strip(), f"검토 필요 {review_needed}건", f"대기 {pending}건",
        status_label if status_label in ("일부 실패", "실패") else "",
        (limit.get("label") or claude_limits.KIND_LABELS.get(limit.get("kind"), "AI 분석 중단")) if limit else str(guard.get("reason") or "")) if part)
    headline = {
        "ok": f"점검을 마쳤습니다. 새 결과 {new_results}건 가운데 검토 필요가 {review_needed}건입니다.",
        "warn": f"점검을 일부만 마쳤습니다. 새 결과 {new_results}건, 검토 필요 {review_needed}건입니다. 아래 내용을 확인하세요.",
        "fail": "점검을 마치지 못했습니다. 아래 내용을 확인하세요.",
    }[tone]
    if limit:
        headline = "Claude 사용량 한도에 닿아 분석을 멈췄습니다. " + headline
    elif guard:
        headline = f"{guard.get('reason')}로 분석을 멈췄습니다. " + headline
    coverage, coverage_gaps = [], []
    for kind, item in (summary.get("coverage") or {}).items():
        coverage.append(f"{'사양' if kind == 'srs' else '이슈'} 확인 가능한 자료: {item.get('start') or '기준 없음'} ~ {item.get('end') or '자료 없음'}")
        coverage_gaps.extend("미확인 기간: " + warning for warning in item.get("warnings", []))
    basis, period = summary.get("basis") or {}, summary.get("period") or {}
    issue_all = results_link("issues")
    spec_all = results_link("spec")
    return {
        "title": mail_subject(product, title), "product": product, "run_id": run_id, "no_link_notice": NO_LINK, "has_links": bool(base),
        "band": {"label": "완료" if status_label == "성공" else status_label, "color": TONES[tone]["color"], "headline": headline},
        "numbers": [("새 결과", new_results), ("검토 필요", review_needed), ("대기", pending), ("AI 호출", summary.get("claude_calls", 0))],
        "number_note": f"사양 분석 대상 {targets.get('spec', 0)}항목 · 이슈 분석 대상 {targets.get('issues', 0)}항목 · 기존 결과 {len(reused)}항목 재사용",
        "issue_groups": _issue_groups(summary.get("issue_results") or [], link), "issue_all": issue_all,
        "spec_rows": _spec_rows(summary.get("spec_results") or [], link), "spec_all": spec_all,
        "notice": COLLECTION_ONLY_NOTICE if collection_only else "",
        "remaining": {"pending": pending, "next_run": "" if summary.get("next_run") is None else (summary.get("next_run") or "예정 없음"), "select_url": link("/qa-agent/select?" + urlencode({"product": product})),
                      "lines": _remaining_lines(stages, summary, rules_warning, limit), "gaps": coverage_gaps},
        "usage": _usage_context(usage, summary, run_id),
        "footer": {"basis": f"비교 기준일: 사양 {basis.get('srs') or '기준 없음'} / 이슈 {basis.get('issues') or '기준 없음'}",
                   "coverage": coverage, "until": f"요청 종료일: {period.get('until') or run_id[:8]}",
                   "version": (summary.get("verification_sheet") or {}).get("sheet") or "버전 미선택",
                   "run_url": review_url if base else "",
                   "notice": "AI 판정과 QA 처리 완료는 별도입니다. 모든 결과는 AI 초안이며 최종 판정과 반영은 QA가 합니다."},
    }


def _workflow_text(ctx: dict) -> str:
    def where(url: str, label: str = "결과 열기") -> str:
        return f"{label}: {url or NO_LINK}"

    lines = ([ctx["notice"]] if ctx["notice"] else []) + [f"상태: {ctx['band']['label']} · {ctx['band']['headline']}",
             " · ".join(f"{name} {value}" for name, value in ctx["numbers"]), ctx["number_note"], ""]
    lines += ["[확인할 이슈]", where(ctx["issue_all"], "이번 실행의 이슈 결과 전체")]
    for group in ctx["issue_groups"]:
        lines += ["", f"[이슈 작성자: {group['name']} ({group['id']})] {len(group['issues'])}건"]
        for issue in group["issues"]:
            for row in issue["rows"]:
                lines += [f"- {issue['subject']} {issue['title']} · {row['kind']} · {row['level']} · 이슈 업데이트 {issue['updated']} (한국 시간)".replace("  ", " "),
                          "  " + where(row["url"])]
    if not ctx["issue_groups"]:
        lines.append("- 이번 실행에서 확인할 이슈 결과가 없습니다.")
    lines += ["", "[확인할 사양]", where(ctx["spec_all"], "이번 실행의 사양 결과 전체")]
    for row in ctx["spec_rows"]:
        lines += [f"- {row['subject']} {row['title']} · {row['kind']} · {row['level']}".replace("  ", " "), "  " + where(row["url"])]
    if not ctx["spec_rows"]:
        lines.append("- 이번 실행에서 확인할 사양 결과가 없습니다.")
    remaining = ctx["remaining"]
    lines += ["", "[남은 일]", f"- 대기 {remaining['pending']}건 (제품 전체에서 아직 분석하지 않은 변경, 이번 실행의 미완료 대상 포함)"
              + (f" · 다음 자동 실행 {remaining['next_run']}" if remaining["next_run"] else ""),
              "- " + where(remaining["select_url"], "상세 분석")]
    lines += [f"- {line}" for line in remaining["lines"] + remaining["gaps"]]
    usage = ctx["usage"]
    lines += ["", "[이번 예약 실행의 Claude 사용량]"]
    lines += [f"- {line}" for line in usage["lines"]] if usage["lines"] else []
    if usage["empty"]:
        lines.append(f"- {usage['empty']}")
    footer = ctx["footer"]
    lines += ["", "[점검 정보]", footer["basis"], *footer["coverage"], footer["until"], f"검증 버전: {footer['version']}", f"실행 번호: {ctx['run_id']}",
              where(footer["run_url"], "점검 기록 열기"), footer["notice"]]
    return "\n".join(lines)


def _workflow_email(run_id, status_label, stages, summary, review_url, rules_warning, product, usage=None):
    ctx = _workflow_context(run_id, status_label, stages, summary, review_url, rules_warning, product, usage)
    return ctx["title"], _workflow_text(ctx), _TEMPLATES.get_template("report_email.html").render(**ctx)
