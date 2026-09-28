"""실행 요약과 메일 본문 (SPEC REQ-DAILY-008).

메일에는 건수와 상태만 넣는다. SRS·이슈 본문, AI 판단 문장은 넣지 않는다 — 메일은 사내
시스템 밖으로 전달되기 쉽고, 자세한 내용은 검토 화면에서 본다.
"""

from __future__ import annotations

import html
from collections import Counter

from app.modules.daily_qa.schema import SKILL_LABELS

STAGE_LABELS = {
    "preflight": "사전 점검",
    "collect_srs": "SRS 수집",
    "collect_issues": "이슈 수집",
    "tc_index": "TC 색인",
    "B": "B 사양 변경 → TC 영향",
    "C": "C 이슈 → 수정확인 초안",
    "E": "E 추적 공백",
    "F": "F 매뉴얼 누락 후보",
}

STATUS_LABELS = {
    "ok": "완료",
    "skipped": "건너뜀",
    "failed": "실패",
    "rules": "규칙 판 불일치",
    "partial": "일부 실패",
    "not_due": "오늘은 대상 아님",
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


def build_email(run_id: str, status_label: str, stages: dict, summary: dict, review_url: str, rules_warning: str) -> tuple[str, str, str]:
    subject = f"[QA 일일 점검] {run_id} {status_label} · Finding {summary.get('findings', 0)}건 · 질문 {summary.get('questions', 0)}건"
    text = []
    if rules_warning:
        text += [f"※ {rules_warning}", ""]
    text += [f"실행 ID: {run_id}", f"결과: {status_label}", "", "[단계]"]
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
    body = (
        f"<div style='font-family:sans-serif'>{warning}<h3>QA 일일 점검 {html.escape(run_id)} — {html.escape(status_label)}</h3>"
        f"<table border='1' cellpadding='4' cellspacing='0'><tr><th>단계</th><th>상태</th><th>비고</th></tr>{rows}</table>"
        f"<h4>Skill 별 Finding</h4><ul>{skills}</ul>"
        f"<p>답이 필요한 질문: <b>{summary.get('questions', 0)}</b>건</p>"
        f"<p><a href='{html.escape(review_url)}'>검토 화면 열기</a></p>"
        "<p style='color:#666'>모든 결과는 AI 초안입니다. 최종 판정과 반영은 QA 가 합니다.</p></div>"
    )
    return subject, "\n".join(text), body
