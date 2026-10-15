from app.modules.daily_qa.report import build_email


def test_two_sections_with_version_pending_and_no_source_body():
    summary = {'savings_enabled': True, 'verification_sheet': {'sheet': 'V1.1.0'},
               'scope_targets': {'spec': 2, 'issues': 3}, 'pending_analysis_events': 2,
               'basis': {'srs': '2026-10-13', 'issues': '2026-10-12'}, 'claude_calls': 2,
               'target_reused': [{'kind': 'SPEC_COVERAGE', 'subject': 'VP-1'}],
               'findings': 1, 'by_skill': {'qa-fixed-issue-analysis': 1},
               'by_verdict': {'qa-fixed-issue-analysis': {'CONTRADICTS_SPEC': 1}}}
    subject, text, body = build_email('20261014-040000-vxvue', '일부 실패', {}, summary,
                                     'https://qa.example/qa-agent/runs/one', '', 'VXvue')
    assert 'V1.1.0' in subject and '미분석' in subject
    for value in ('사양 분석', '이슈 분석', '기존 결과', '2026-10-13', '2026-10-12'):
        assert value in text and value in body
    assert 'category=spec' in body and 'category=issues' in body
    assert '자동 재개' not in text


def test_email_groups_original_reporter_and_orders_updated_time():
    summary = {'savings_enabled': True, 'issue_results': [
        {'id': 1, 'subject': 'VP-1', 'reporter_id': 'a', 'reporter_name': '가 이름', 'issue_updated_at': '2026-10-12T01:00:00Z', 'verdict': 'SPEC_VIOLATION'},
        {'id': 2, 'subject': 'VP-2', 'reporter_id': 'b', 'reporter_name': '나 이름', 'issue_updated_at': '2026-10-14T01:00:00Z', 'verdict': 'SPEC_VIOLATION'},
        {'id': 3, 'subject': 'VP-3', 'reporter_id': 'a', 'reporter_name': '가 이름', 'issue_updated_at': '2026-10-13T01:00:00Z', 'verdict': 'SPEC_VIOLATION'}]}
    _, text, body = build_email('run', '완료', {}, summary, 'https://qa.example/qa-agent/runs/run', '', 'VXvue')
    assert '가 이름' in text and '나 이름' in body
    assert text.index('VP-3') < text.index('VP-1') < text.index('나 이름')
    assert '2026-10-13 10:00' in text and '/findings/3' in body


def test_mail_shows_actual_snapshot_end_and_missing_period():
    summary = {'savings_enabled': True, 'period': {'until': '2026-10-05'}, 'coverage': {
        'issues': {'start': '2026-09-29', 'end': '2026-09-30', 'warnings': ['2026-09-30 이후 ~ 2026-10-05 자료 없음']}}}
    _, text, body = build_email('run', '일부 완료', {}, summary, '/qa-agent/runs/run', '', 'VXvue')
    assert '이슈 확인 가능한 자료: 2026-09-29 ~ 2026-09-30' in text
    assert '미확인 기간:' in body and '요청 종료일: 2026-10-05' in text
