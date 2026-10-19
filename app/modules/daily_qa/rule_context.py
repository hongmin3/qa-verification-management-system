"""Read required numbered guide sections without changing their text."""
from __future__ import annotations

import json
import re


def guide_context(task, run, *, strict=False) -> list[tuple[str, str]]:
    mapping = run.root / '.claude' / 'skills' / run.product_skill / 'guide-sections.json'
    guide = run.root / 'rules' / 'qa-guide.md'
    if guide.is_file():
        from app.core.qa_rule_source import UNIFIED_MARKER, unified_parts
        text = guide.read_text(encoding='utf-8-sig')
        if UNIFIED_MARKER in text:
            from app.core.qa_rules import RuleSet, parse_rule_document
            operations, details = unified_parts(text)
            skills = {
                'qa-new-issue-analysis': ('S01', 'S02'),
                'qa-fixed-issue-analysis': ('S04', 'S05', 'S09'),
                'qa-verification-tc-draft': ('S03', 'S04', 'S05', 'S09'),
                'qa-spec-decision-analysis': ('S01', 'S02'),
                'qa-comment-analysis': ('S01', 'S02'),
                'qa-spec-change-summary': ('S02', 'S05'),
                'qa-spec-coverage-analysis': ('S02', 'S03'),
                'qa-issue-spec-audit': ('S01', 'S02'),
                'qa-manual-completeness': ('S07',),
            }.get(task.skill, ())
            if any(item.get('verification_mode') for item in task.payload.get('items', [])):
                skills = tuple(dict.fromkeys((*skills, 'S03', 'S04', 'S05')))
            rules = RuleSet('VXvue', [parse_rule_document(details, 'QA 상세 규칙', 'qa_rules')])
            chosen = {}
            for skill in skills:
                for section in rules.slice(skill=skill, char_budget=24000):
                    chosen[id(section)] = section
            excerpt = '\n\n'.join(section.render() for section in chosen.values()) if chosen else details
            notice = '\n상세 규칙은 업무 주제로 발췌했다. 필요한 근거·Gate·출력 기준이 부족하면 rules/qa-guide.md에서 제목으로 추가 확인한다. 절 번호는 고정 기준이 아니다.\n'
            return [(run.rel(guide) + ' (운영 지침과 업무별 상세 규칙)', operations + excerpt + notice)]
    if not run.product_skill or not mapping.is_file() or not guide.is_file():
        if strict:
            raise ValueError('제품 규칙의 절 연결 또는 QA 가이드 원문이 없습니다.')
        return []
    try:
        sections = json.loads(mapping.read_text(encoding='utf-8'))
        wanted = sections.get(task.skill, [])
        if not isinstance(wanted, list) or not all(isinstance(s, str) for s in wanted):
            raise ValueError('규칙 절 연결 형식 오류')
        if any(item.get('verification_mode') for item in task.payload.get('items', [])):
            extra = sections.get('qa-verification-tc-draft', []) if task.skill == 'qa-fixed-issue-analysis' else ['17']
            if not isinstance(extra, list) or not all(isinstance(s, str) for s in extra):
                raise ValueError('TC 규칙 절 연결 형식 오류')
            wanted = list(dict.fromkeys([*wanted, *extra]))
    except (ValueError, AttributeError):
        if strict:
            raise ValueError('제품 규칙의 절 연결을 읽지 못했습니다.') from None
        return []  # Product without a usable mapping follows the existing lookup path.
    lines = guide.read_text(encoding='utf-8').splitlines(keepends=True)
    headings, fence = [], ''
    for index, line in enumerate(lines):
        marker = re.match(r'^\s*(`{3,}|~{3,})', line)
        if marker:
            value = marker.group(1)
            if not fence:
                fence = value
            elif value[0] == fence[0] and len(value) >= len(fence):
                fence = ''
            continue
        if fence:
            continue
        heading = re.match(r'^(#{1,6})\s+(.+)', line)
        if heading:
            number = re.match(r'(?:\*\*)?§?(\d+(?:\.\d+)*)(?:[.\s]|$)', heading.group(2))
            headings.append((index, len(heading.group(1)), number.group(1) if number else ''))
    spans, found = [], set()
    for position, (start, level, number) in enumerate(headings):
        if number not in wanted:
            continue
        end = next((i for i, depth, _ in headings[position + 1:] if depth <= level), len(lines))
        spans.append((start, end))
        found.add(number)
    # A parent section already contains its children. Never repeat those lines.
    selected = sorted({i for start, end in spans for i in range(start, end)})
    excerpt = ''.join(lines[i] for i in selected)
    missing = [number for number in wanted if number not in found]
    if strict and (not wanted or missing):
        raise ValueError('필수 QA 규칙 절이 없습니다: ' + ', '.join(missing or [task.skill]))
    notice = ('\n미리 넣지 못한 절: ' + ', '.join(missing) + '. 원문에서 추가 확인한다.\n') if missing else ''
    return [(run.rel(guide) + ' (선택한 절)', excerpt + notice)] if excerpt or notice else []
