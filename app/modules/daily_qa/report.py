"""실행 요약과 메일 본문 (SPEC REQ-DAILY-008).

메일에는 건수와 상태만 넣는다. SRS·이슈 본문, AI 판단 문장은 넣지 않는다 — 메일은 사내
시스템 밖으로 전달되기 쉽고, 자세한 내용은 검토 화면에서 본다.
"""

from __future__ import annotations

import html
from collections import Counter

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
                product: str = "") -> tuple[str, str, str]:
    if summary.get('savings_enabled'):
        return _workflow_email(run_id, status_label, stages, summary, review_url, rules_warning, product)
    prefix = f"[QA Agent] {product} " if product else "[QA Agent] "
    subject = f"{prefix}{run_id} {status_label} · Finding {summary.get('findings', 0)}건 · 질문 {summary.get('questions', 0)}건"
    limit = summary.get("claude_limit") or {}
    collection_failed = any((stages.get(key) or {}).get("status") in {"failed", "partial"}
                            for key in ("collect_srs", "collect_issues"))
    failures = [f"{STAGE_LABELS.get(key, key)}: {stage.get('note') or STATUS_LABELS.get(stage.get('status'), '')}"
                for key, stage in stages.items() if stage.get("status") in {"failed", "partial", "rules"}]
    title = (limit.get("label") or claude_limits.KIND_LABELS.get(limit.get("kind"), "AI 분석 중단")) if limit else (
        "수집 실패" if collection_failed else "점검 실패" if failures else "")
    if title:
        subject = f"{prefix}{title} · {run_id} {status_label} · Finding {summary.get('findings', 0)}건 · 질문 {summary.get('questions', 0)}건"
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
    return subject, "\n".join(text), body


def _workflow_email(run_id, status_label, stages, summary, review_url, rules_warning, product):
    from urllib.parse import urlencode, urlsplit
    version = (summary.get('verification_sheet') or {}).get('sheet') or '버전 미선택'
    targets = summary.get('scope_targets') or {}
    pending = summary.get('pending_analysis_events', 0)
    reused = summary.get('target_reused') or []
    parsed = urlsplit(review_url)
    base = f'{parsed.scheme}://{parsed.netloc}' if parsed.netloc else ''
    def link(category):
        return base + '/qa-agent/results?' + urlencode({'product': product, 'run_id': run_id, 'category': category})
    basis = summary.get('basis') or {}
    period = summary.get('period') or {}
    lines = [f'{product} · 검증 버전 {version} · {status_label}',
             f"실제 비교 기준: 사양 {basis.get('srs') or '기준 없음'} / 이슈 {basis.get('issues') or '기준 없음'}",
             f"요청 종료일: {period.get('until') or run_id[:8]}",
             f'미분석·대기 변경 {pending}건 · 이번 실행의 미완료 대상을 포함한 제품 전체 대기 건수입니다.',
             'AI 판정과 QA 처리 완료는 별도입니다.', '',
             f"[사양 분석] 대상 {targets.get('spec', 0)}항목 · 기존 결과 {sum(r['kind'] == 'SPEC_COVERAGE' for r in reused)}항목", link('spec'),
             f"[이슈 분석] 대상 {targets.get('issues', 0)}항목 · 기존 결과 {sum(r['kind'] != 'SPEC_COVERAGE' for r in reused)}항목", link('issues'), '']
    for kind, coverage in (summary.get('coverage') or {}).items():
        label = '사양' if kind == 'srs' else '이슈'
        lines += [f"{label} 확인 가능한 자료: {coverage.get('start') or '기준 없음'} ~ {coverage.get('end') or '자료 없음'}"]
        lines.extend('미확인 기간: ' + warning for warning in coverage.get('warnings', []))
    from app.modules.qa_agent.dashboard import VERDICT_LEVEL
    counts = Counter()
    for verdicts in summary.get('by_verdict', {}).values():
        for verdict, count in verdicts.items():
            counts[VERDICT_LEVEL.get(verdict, '검토 필요')] += count
    lines += ['[이번에 생성된 결과의 AI 판정] ' + (' · '.join(f'{name} {count}건' for name, count in counts.items()) or '새 결과 없음')]
    lines += _issue_author_lines(summary.get('issue_results') or [], base)
    if rules_warning:
        lines.append('지침 확인 필요: ' + rules_warning)
    for label, status, note in _stage_lines(stages):
        if status not in ('완료', '건너뜀', '오늘은 대상 아님'):
            lines.append(f'{label}: {status} {note}')
    if summary.get('call_budget_exhausted'):
        lines.append('새 AI 호출 예산을 모두 사용했습니다. 남은 항목은 완료로 처리하지 않았습니다.')
    lines += ['', f"새 Claude 호출 {summary.get('claude_calls', 0)}회 · 기존 결과 {len(reused)}항목 재사용",
              '변경과 새 분석 대상이 없으면 AI를 호출하지 않습니다.', '점검 기록: ' + review_url]
    subject = f'[QA] {product} {version} · 사양 {targets.get("spec", 0)} / 이슈 {targets.get("issues", 0)} · 미분석·대기 {pending}'
    body = '<div style="font-family:sans-serif;max-width:760px">'
    for line in lines:
        if line in (link('spec'), link('issues'), '점검 기록: ' + review_url) or line.startswith(base + '/qa-agent/findings/'):
            url = review_url if line.startswith('점검 기록: ') else line
            body += f'<p><a href="{html.escape(url, quote=True)}">결과 열기</a></p>'
        else:
            body += '<p>' + html.escape(line) + '</p>'
    return subject, '\n'.join(lines), body + '</div>'


def _issue_author_lines(rows, base):
    from datetime import datetime, timezone
    from app.modules.qa_agent.dashboard import VERDICT_LEVEL
    groups = {}
    for row in rows:
        person = str(row.get('reporter_id') or '')
        group = groups.setdefault(person, {'name': row.get('reporter_name') or '이름 미확인', 'issues': {}})
        issue = group['issues'].setdefault(row['subject'], {'updated': row.get('issue_updated_at') or '', 'results': []})
        issue['results'].append(row)
    def moment(value):
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return datetime.min.replace(tzinfo=timezone.utc)
    lines = []
    for person, group in sorted(groups.items(), key=lambda pair: (not bool(pair[0]), pair[1]['name'], pair[0])):
        lines += ['', f"[이슈 작성자: {group['name']} ({person or 'ID 미확인'})] {len(group['issues'])}건"]
        for subject, issue in sorted(group['issues'].items(), key=lambda pair: (moment(pair[1]['updated']), pair[0]), reverse=True):
            date = moment(issue['updated'])
            stamp = date.astimezone(claude_limits.KST).strftime('%Y-%m-%d %H:%M') if date.year > 1 else '미확인'
            verdicts = ' · '.join(dict.fromkeys(VERDICT_LEVEL.get(row.get('verdict'), '검토 필요') for row in issue['results']))
            lines.append(f'{subject} · 이슈 업데이트 {stamp} (한국 시간) · {verdicts}')
            lines.extend(base + '/qa-agent/findings/' + str(row['id']) for row in issue['results'])
    return lines
