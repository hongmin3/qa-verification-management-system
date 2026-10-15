"""Validates: REQ-MAIL-001, REQ-QAINTEL-048 (메일 제목·본문 구성).

개인 이름·주소는 모두 합성 값이다. 운영 실행 20261015-040000-vxvue 의 요약 모양을 따른다.
"""
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
    assert subject.startswith('[QA Agent] VXvue ') and '대기 2건' in subject
    for value in ('사양 분석', '이슈 분석', '기존 결과', '2026-10-13', '2026-10-12', 'V1.1.0'):
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


# --- 제목·본문 구성 (REQ-MAIL-001, REQ-QAINTEL-048) ----------------------------------

RUN_ID = '20261015-040000-vxvue'
BASE = 'http://10.13.0.222:24357'
REVIEW = f'{BASE}/qa-agent/runs/{RUN_ID}'
STAGES = {
    'collect_srs': {'status': 'ok', 'note': ''},
    'FIXED_ISSUE': {'status': 'partial', 'note': '필수 검토·근거·출력 확인이 남아 있습니다. audit.json Gate G1 상한 초과로 미룸 묶음'},
    'email': {'status': 'ok', 'note': ''},
}
SAVINGS_SUMMARY = {
    'savings_enabled': True, 'product': 'vxvue', 'findings': 2,
    'verification_sheet': {'sheet': 'V1.1.0'}, 'scope_targets': {'spec': 0, 'issues': 2},
    'pending_analysis_events': 12, 'claude_calls': 2, 'next_run': '2026-10-16 04:00',
    'basis': {'srs': '2026-10-14', 'issues': '2026-10-14'}, 'period': {'until': '2026-10-15'},
    'by_skill': {'qa-issue-analysis': 2},
    'by_verdict': {'qa-issue-analysis': {'PARTIALLY_COVERED': 1, 'REQUIREMENT_INFORMATION': 1}},
    'token_usage': {'total_tokens': 552697}, 'ai_settings': {'analysis_model': 'claude-sonnet-5-5'},
    'issue_results': [
        {'id': 101, 'subject': 'VP-580', 'subject_title': '가상 이슈 제목 가', 'reporter_id': 'writer-a', 'reporter_name': '가상 작성자 가',
         'issue_updated_at': '2026-10-14T05:30:00Z', 'verdict': 'PARTIALLY_COVERED', 'analysis_type': 'FIXED_ISSUE'},
        {'id': 102, 'subject': 'VP-7257', 'subject_title': '가상 이슈 제목 나', 'reporter_id': 'writer-b', 'reporter_name': '가상 작성자 나',
         'issue_updated_at': '2026-10-15T01:10:00Z', 'verdict': 'REQUIREMENT_INFORMATION', 'analysis_type': 'COMMENT'}],
    'spec_results': [
        {'id': 201, 'subject': 'VP-10', 'subject_title': '가상 사양 제목', 'verdict': 'INSUFFICIENT_EVIDENCE', 'analysis_type': 'SPEC_COVERAGE'}],
}
USAGE_AFTER_ONLY = {
    'before': {'five_hour': None, 'seven_day': None, 'measured_at': ''},
    'after': {'five_hour': 7.0, 'seven_day': 76.0, 'five_hour_resets_kst': '2026-10-15 09:00', 'seven_day_resets_kst': '2026-10-18 01:00'},
    'calls': 2, 'total_tokens': 552697, 'model': 'claude-sonnet-5-5',
    'by_target': [{'subject': 'VP-580', 'kind': 'FIXED_ISSUE', 'tokens': 319574},
                  {'subject': 'VP-7257', 'kind': 'COMMENT', 'tokens': 233123}],
}


def _mail(status='일부 실패', summary=None, review=REVIEW, usage=None, stages=None, rules_warning=''):
    return build_email(RUN_ID, status, STAGES if stages is None else stages, SAVINGS_SUMMARY if summary is None else summary,
                       review, rules_warning, product='VXvue', usage=usage)


def test_every_subject_starts_with_qa_agent():
    subject, _, _ = _mail()
    assert subject.startswith('[QA Agent] VXvue ')
    plain, _, _ = build_email(RUN_ID, '성공', {}, {'findings': 1, 'questions': 0}, REVIEW, '', product='VXvue')
    assert plain.startswith('[QA Agent] VXvue ')
    no_product, _, _ = build_email(RUN_ID, '성공', {}, {'findings': 1, 'questions': 0}, REVIEW, '')
    assert no_product.startswith('[QA Agent] ') and not no_product.startswith('[QA Agent]  ')


def test_workflow_subject_counts_review_and_pending():
    subject, _, _ = _mail()
    assert subject == '[QA Agent] VXvue 10/15 점검 · 검토 필요 2건 · 대기 12건 · 일부 실패'


def test_subject_has_no_failure_suffix_when_complete_and_adds_limit_name():
    assert _mail('완료')[0] == '[QA Agent] VXvue 10/15 점검 · 검토 필요 2건 · 대기 12건'
    assert _mail('실패')[0].endswith('대기 12건 · 실패')
    limited = {**SAVINGS_SUMMARY, 'claude_limit': {'kind': 'weekly', 'label': 'Claude 주간 사용량 한도', 'reset_kst': '2026-10-18 01:00'}}
    assert _mail('일부 실패', limited)[0].endswith('대기 12건 · 일부 실패 · Claude 주간 사용량 한도')


def test_review_needed_count_excludes_no_problem_and_reference():
    summary = {**SAVINGS_SUMMARY, 'by_verdict': {'qa-issue-analysis': {
        'CONSISTENT_WITH_SPEC': 3, 'ROOT_CAUSE_INFORMATION': 1, 'SPEC_VIOLATION': 1, 'INSUFFICIENT_EVIDENCE': 1, 'PARTIALLY_COVERED': 2}}}
    subject, text, _ = _mail('완료', summary)
    assert '검토 필요 4건' in subject
    assert '새 결과 8' in text


def test_workflow_body_order_and_plain_language():
    _, text, _ = _mail()
    assert text.index('확인할 이슈') < text.index('확인할 사양') < text.index('남은 일') < text.index('Claude 사용량')
    for internal in ('audit.json', 'Gate G', '묶음', '상한 초과로 미룸'):
        assert internal not in text


def test_html_has_same_sections_in_order_and_no_internal_words():
    _, _, html = _mail()
    assert html.index('확인할 이슈') < html.index('확인할 사양') < html.index('남은 일') < html.index('Claude 사용량')
    for internal in ('audit.json', 'Gate G', '묶음', '상한 초과로 미룸'):
        assert internal not in html


def test_html_is_table_based_with_inline_styles_only():
    _, _, html = _mail()
    assert '<table' in html and 'style="' in html
    for forbidden in ('<style', 'display:flex', 'display: flex', 'display:grid', 'display: grid', 'class='):
        assert forbidden not in html


def test_html_escapes_values_from_issue_data():
    summary = {**SAVINGS_SUMMARY, 'issue_results': [{**SAVINGS_SUMMARY['issue_results'][0], 'reporter_name': '<script>x</script>',
                                                     'subject_title': '<img src=x>'}]}
    _, _, html = _mail('완료', summary)
    assert '<script>' not in html and '<img src=x>' not in html and '&lt;script&gt;' in html


def test_issue_and_spec_rows_show_the_requested_columns_with_absolute_links():
    _, text, html = _mail()
    for value in ('가상 작성자 가', 'VP-580', '가상 이슈 제목 가', '수정 완료 이슈 분석', '검토 필요',
                  'VP-7257', '새 댓글 분석', '2026-10-14 14:30'):
        assert value in text and value in html, value
    for value in ('VP-10', '가상 사양 제목', '사양 변경 분석', '근거 부족'):
        assert value in text and value in html, value
    assert f'href="{BASE}/qa-agent/findings/101"' in html and f'href="{BASE}/qa-agent/findings/201"' in html
    assert f'{BASE}/qa-agent/findings/102' in text


def test_numbers_remaining_work_and_footer():
    _, text, _ = _mail()
    for value in ('새 결과 2', '검토 필요 2', '대기 12', 'AI 호출 2', '2026-10-16 04:00', f'{BASE}/qa-agent/select?product=VXvue',
                  '비교 기준일', '2026-10-14', 'V1.1.0', RUN_ID, f'{BASE}/qa-agent/runs/{RUN_ID}', 'AI 초안'):
        assert value in text, value


def test_internal_note_words_are_replaced_by_plain_sentence():
    _, text, _ = _mail()
    line = next(row for row in text.splitlines() if row.startswith('- 수정 완료 이슈 분석'))
    assert '일부 실패' in line and 'audit.json' not in line and 'Gate' not in line


def test_links_are_absolute_or_flagged():
    _, _, html = _mail('완료')
    assert 'href="/qa-agent' not in html
    _, text, html = _mail('완료', review='/qa-agent/runs/R')
    assert '서버 주소 미설정 · 설정 탭에서 입력' in text and '서버 주소 미설정 · 설정 탭에서 입력' in html
    assert 'href="/qa-agent' not in html and 'href=""' not in html and '/qa-agent/findings/' not in text


def test_status_band_color_follows_result():
    assert '#1f8a4c' in _mail('완료')[2]
    assert '#d97706' in _mail('일부 실패')[2]
    assert '#c62828' in _mail('실패')[2]


def test_usage_block_without_before_value():
    _, text, _ = _mail(usage={**USAGE_AFTER_ONLY})
    line = next(row for row in text.splitlines() if row.startswith('- 5시간 사용량'))
    assert '실행 전 값 미확인 → 실행 후 7%' in line and '%p' not in line and '초기화 2026-10-15 09:00' in line


def test_usage_block_with_before_value_shows_increase():
    usage = {**USAGE_AFTER_ONLY, 'before': {'five_hour': 2.0, 'seven_day': 70.0, 'measured_at': '2026-10-15 03:58'}}
    _, text, html = _mail(usage=usage)
    line = next(row for row in text.splitlines() if row.startswith('- 5시간 사용량'))
    assert '실행 전 2% (2026-10-15 03:58 측정) → 실행 후 7% (+5%p)' in line
    week = next(row for row in text.splitlines() if row.startswith('- 주간 사용량'))
    assert '실행 후 76%' in week and '초기화 2026-10-18 01:00' in week
    assert '+5%p' in html


def test_usage_block_lists_calls_tokens_model_and_targets():
    _, text, _ = _mail(usage=USAGE_AFTER_ONLY)
    for value in ('호출 2회', '552,697', 'claude-sonnet-5-5', 'VP-580 319,574', 'VP-7257 233,123'):
        assert value in text, value


def test_usage_block_when_nothing_was_recorded():
    _, text, _ = _mail(usage=None)
    usage_part = text.split('Claude 사용량')[1].split('비교 기준일')[0]
    assert '사용량 기록 없음' in usage_part and '실행 전' not in usage_part


# --- 검토 보완: 실행 전 값의 나이, 변경 목록 전용 메일 ----------------------------------

def _five_line(before):
    _, text, html = _mail(usage={**USAGE_AFTER_ONLY, 'before': before})
    return next(row for row in text.splitlines() if row.startswith('- 5시간 사용량')), html


def test_stale_before_value_shows_its_time_and_no_increase():
    line, html = _five_line({'five_hour': 2.0, 'seven_day': 70.0, 'measured_at': '2026-10-14 22:00'})   # 실행 6시간 전
    assert '실행 전 2% (2026-10-14 22:00 측정) → 실행 후 7%' in line and '%p' not in line
    assert '%p' not in html and '2026-10-14 22:00 측정' in html


def test_increase_is_not_shown_when_the_window_was_reset_in_between():
    line, _ = _five_line({'five_hour': 60.0, 'seven_day': 70.0, 'measured_at': '2026-10-15 03:58',
                          'five_hour_resets_kst': '2026-10-15 03:59'})            # 실행 직전에 이미 끝난 창
    assert '(그 사이 초기화됨)' in line and '%p' not in line
    line, _ = _five_line({'five_hour': 2.0, 'seven_day': 70.0, 'measured_at': '2026-10-15 03:58',
                          'five_hour_resets_kst': '2026-10-15 08:00'})            # 창이 다르다(끝나는 시각이 다름)
    assert '(그 사이 초기화됨)' in line and '%p' not in line


def test_increase_is_shown_for_a_fresh_reading_of_the_same_window():
    line, _ = _five_line({'five_hour': 2.0, 'seven_day': 70.0, 'measured_at': '2026-10-15 01:00',
                          'five_hour_resets_kst': '2026-10-15 09:00'})
    assert '실행 전 2% (2026-10-15 01:00 측정) → 실행 후 7% (+5%p)' in line


def test_collection_only_mail_keeps_prefix_and_valid_document():
    summary = {**SAVINGS_SUMMARY, 'collection_only': True}
    subject, text, html = _mail('완료', summary)
    assert subject.startswith('[QA Agent] VXvue ') and 'AI 미분석' in subject
    assert html.lstrip().startswith('<!DOCTYPE html>') and 'AI 미분석' in html
    assert text.startswith('AI 분석을 실행하지 않았습니다.')
    plain, plain_text, plain_html = build_email(RUN_ID, '완료', {}, {'collection_only': True, 'findings': 0}, REVIEW, '', product='VXvue')
    assert plain.startswith('[QA Agent] VXvue ') and 'AI 미분석' in plain
    assert plain_text.startswith('AI 분석을 실행하지 않았습니다.') and 'AI 미분석' in plain_html


def test_missing_next_run_drops_only_that_part():
    summary = {k: v for k, v in SAVINGS_SUMMARY.items() if k != 'next_run'}
    _, text, html = _mail('완료', summary)
    assert '다음 자동 실행' not in text and '다음 자동 실행' not in html and '대기 12건' in text
