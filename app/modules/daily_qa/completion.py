"""Completion is stricter than valid JSON. Missing work stays visible and uncached."""
from app.modules.daily_qa.change_events import FIXED_ISSUE, NEW_ISSUE, SPEC_COVERAGE, SPEC_DECISION, COMMENT


def missing_reviews(checked, kind, axes):
    required_gates = {'G1', 'G2', 'G7'}
    if kind == FIXED_ISSUE:
        required_gates.add('G4')
    if kind == SPEC_COVERAGE:
        required_gates.add('G3')
    missing = [f'Gate {gate}' for gate in sorted(required_gates) if checked.result.gate_status.get(gate) != 'PASS']
    for finding in checked.accepted:
        sections = finding.sections
        def section(key):
            value = sections.get(key)
            return value if isinstance(value, dict) else {}
        needed = {NEW_ISSUE: ('duplicate', 'specification', 'historical', 'recommendation'),
                  FIXED_ISSUE: ('root_cause_review', 'resolution_review', 'specification', 'change_scope', 'regression_risk'),
                  SPEC_DECISION: ('rd_claim', 'specification', 'historical', 'qa_analysis'),
                  SPEC_COVERAGE: ('change', 'issue_coverage', 'checklist_coverage'),
                  COMMENT: ('checked',)}.get(kind, ())
        for key in needed:
            if not isinstance(sections.get(key), dict) or not sections[key]:
                missing.append(f'{finding.subject}: {key}')
        for key in ('root_cause_review', 'resolution_review', 'specification', 'change_scope', 'rd_claim', 'change'):
            if key in needed and not any(isinstance(section(key).get(field), str) and section(key)[field].strip()
                                         for field in ('summary', 'assessment')):
                missing.append(f'{finding.subject}: {key} 설명')
        if section('checked').get('unconfirmed') or sections.get('tc_hold'):
            missing.append(f'{finding.subject}: 미확인 자료')
        if kind == FIXED_ISSUE:
            given = {row.get('axis'): row for row in (section('regression_risk').get('axes') or []) if isinstance(row, dict)}
            for axis in axes:
                row = given.get(axis, {})
                if not isinstance(row.get('applicable'), bool) or not str(row.get('reason') or '').strip():
                    missing.append(f'{finding.subject}: 회귀 축 {axis}')
        coverage = section('checklist_coverage').get('tcs') if kind == SPEC_COVERAGE else sections.get('tc_coverage')
        if kind in (NEW_ISSUE, FIXED_ISSUE, SPEC_DECISION, SPEC_COVERAGE, COMMENT):
            if not isinstance(coverage, list):
                missing.append(f'{finding.subject}: TC 비교')
            if not coverage and not str(section('tc_assessment').get('summary') or '').strip():
                missing.append(f'{finding.subject}: 관련 TC 없음의 확인 근거와 보완안')
        if kind == SPEC_COVERAGE:
            for field in ('issues',):
                if not isinstance(section('issue_coverage').get(field), list):
                    missing.append(f'{finding.subject}: 관련 이슈 검토')
            if not section('change').get('changed_requirements'):
                missing.append(f'{finding.subject}: 변경 요구사항')
    return missing
