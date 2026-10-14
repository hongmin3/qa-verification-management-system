"""Validates: REQ-QAINTEL-015, REQ-QAINTEL-020, REQ-QAINTEL-033 (TEST-QAINTEL-025)."""
import json

import pytest

from app.modules.daily_qa.change_events import COMMENT
from app.modules.daily_qa.evidence_validation import validate_finding
from app.modules.daily_qa.intelligence import build_item
from app.modules.daily_qa.product_adapter import normalize_comment
from tests.test_qa_intel_analysis import cfg_with, corpus_of, mk_issue, mk_srs, target
from tests.test_qa_intel_dashboard import world  # noqa: F401
from tests.qa_intel_harness import DAY1, DAY2, Harness
from tests.daily_qa_fixtures import comment, issue_item, srs_item


def comment_event(text='VP-580 사양을 수정했습니다.', author='author-secret'):
    return {'id': 95, 'event_type': 'ISSUE_COMMENT_ADDED', 'after': {
        'comments': [{'id': 'c2', 'created': '2026-10-12T04:24:00Z', 'text': text, 'author': author}],
        'significant_ids': ['c2']}}


def test_comment_author_is_preserved_from_polarion_relationship():
    raw = comment('c2', '원인은 설정 누락입니다.')
    raw['relationships'] = {'author': {'data': {'id': 'users/qa-user', 'type': 'users'}}}
    assert normalize_comment(raw)['author'] == 'qa-user'
    assert normalize_comment(comment('c3', '알려 주세요'))['author'] == ''


def test_comment_input_has_exact_linked_srs_and_no_authors(tmp_path):
    issue = mk_issue('VP-7237', linked=('VP-12', 'VP-13'))
    issue['comments'] = [{'id': 'c1', 'text': '이전 댓글', 'author': 'earlier-secret'}]
    corpus = corpus_of([issue], [mk_srs('VP-580', text='x' * 2000), mk_srs('VP-12'), mk_srs('VP-13'), mk_srs('VP-5800')])
    item = build_item(target(COMMENT, 'VP-7237', [comment_event('VP-580과 VP-404를 수정했습니다.')]),
                      corpus, corpus.known(), cfg_with(tmp_path))
    assert [s['id'] for s in item['candidates']['srs']] == ['VP-580', 'VP-12']
    assert len(item['candidates']['srs'][0]['text']) <= 1500
    assert item['candidates']['srs'][0]['truncated'] is True
    assert item['missing_srs_ids'] == []
    assert item['unresolved_reference_ids'] == ['VP-404']
    assert item['omitted_srs_ids'] == ['VP-13']
    encoded = json.dumps(item)
    assert 'author-secret' not in encoded and 'earlier-secret' not in encoded
    assert 'author' not in encoded
    assert issue['comments'][0]['author'] == 'earlier-secret'


def test_comment_mention_does_not_match_partial_srs_id(tmp_path):
    corpus = corpus_of([mk_issue('VP-7237')], [mk_srs('VP-580')])
    item = build_item(target(COMMENT, 'VP-7237', [comment_event('VP-5800을 수정했습니다.')]),
                      corpus, corpus.known(), cfg_with(tmp_path))
    assert item['candidates']['srs'] == []
    assert item['missing_srs_ids'] == []
    assert item['unresolved_reference_ids'] == ['VP-5800']


def test_pipeline_comment_input_uses_previous_summary_scoped_to_issue_and_product(tmp_path):
    h = Harness(tmp_path)
    h.srs = [srs_item('VP-580', '01-01', '전송 설정')]
    h.issues = [issue_item('VP-7237', '2026-09-28T00:00:00Z', ['VP-580'], review='', comment_ids=['c1'])]
    h.comments = {'VP-7237': [comment('c1', '원인 확인 요청')]}
    first = h.run(DAY1)
    for product, subject, summary in [('vxvue', 'VP-7237', '이전 분석 ' + 'x' * 900),
                                       ('vxvue', 'VP-999', '다른 이슈 요약'), ('other', 'VP-7237', '다른 제품 요약')]:
        h.store.add_finding(first['run_id'], 'qa-new-issue-analysis', 'prior-' + product + subject,
                            {'subject': subject, 'verdict': 'SPEC_UNDEFINED', 'summary': summary,
                             'analysis_type': 'NEW_ISSUE'}, product=product)
    h.issues[0] = issue_item('VP-7237', '2026-09-29T00:00:00Z', ['VP-580'], review='', comment_ids=['c1', 'c2'])
    raw = comment('c2', 'VP-580 사양에 전송 조건을 추가했습니다.')
    raw['relationships'] = {'author': {'data': {'id': 'users/private-user'}}}
    h.comments['VP-7237'].append(raw)
    h.run(DAY2)
    payload = next(p for p in h.payloads if p.get('analysis_type') == COMMENT)
    item = payload['items'][0]
    assert item['previous_analysis']['summary'].startswith('이전 분석')
    assert len(item['previous_analysis']['summary']) <= 600
    assert item['candidates']['srs'][0]['id'] == 'VP-580'
    assert 'private-user' not in json.dumps(payload)
    event = h.store.list_events(product='vxvue', event_types=('ISSUE_COMMENT_ADDED',))[0]
    assert event['after']['comments'][0]['author'] == 'private-user'


@pytest.mark.parametrize('checked', [None, {}, {'confirmed': '아무 자료', 'unconfirmed': []},
                                    {'confirmed': ['', 3], 'unconfirmed': ['VP-580 본문 미확인']}])
def test_comment_missing_or_invalid_checked_is_visible_in_validation(checked):
    corpus = corpus_of([mk_issue('VP-7237')])
    known = corpus.known()
    known.comment_ids.add('c2')
    finding = {'subject': 'VP-7237', 'verdict': 'SPEC_CLAIM', 'summary': '사양 추가 댓글', 'confidence': 'Review Needed',
               'evidence': [{'source_type': 'comment', 'ref': 'c2', 'location': 'c2'}],
               'sections': {'comments': [{'comment_id': 'c2', 'classification': 'SPEC_CLAIM'}]}}
    if checked is not None:
        finding['sections']['checked'] = checked
    result, error = validate_finding(finding, COMMENT, known, {'VP-7237'})
    assert not error
    assert result['sections']['validation']['notes']
    assert isinstance(result['sections']['checked']['confirmed'], list)
    assert all(isinstance(s, str) and s.strip() for s in result['sections']['checked']['confirmed'])


def test_old_comment_explanation_shows_original_and_limit_before_details(world):
    store = world['store']
    finding = store.list_findings(run_id=world['changed']['run_id'], analysis_type=COMMENT)[0]
    # 저장된 구형 결과에는 checked가 없다. 내용을 추가해 이전 결과 호환도 확인한다.
    fid = store.add_finding(finding['run_id'], 'qa-comment-analysis', 'old-explanation', {
        'subject': 'VP-102', 'subject_title': '전송 설정 오류', 'analysis_type': COMMENT,
        'event_ids': finding['event_ids'], 'verdict': 'SPEC_CLAIM', 'confidence': 'Review Needed',
        'summary': '댓글에서 사양 변경을 설명했습니다.', 'detail': 'VP-580 본문은 입력에 없어 대조하지 못했습니다.',
        'sections': {'comments': [{'comment_id': 'c2', 'summary': '사양 변경을 설명함',
                                   'classification': 'SPEC_CLAIM', 'impact': '기대 결과 후보'}]}}, product='vxvue')
    response = world['client'].get(f'/qa-agent/findings/{fid}')
    assert response.status_code == 200
    page = response.text
    top = page.split('id="evidence-panel"')[0]
    assert '판단과 할 일' in top
    assert '원인은 캐시 초기화 누락으로 확인되었습니다.' in top
    assert 'VP-580 본문은 입력에 없어 대조하지 못했습니다.' in top
    assert '작성자 정보 없음' in top
    assert '신뢰도' in top and '>확인 필요</span>' in top
    assert '확인 자료 구분이 저장되지 않은 결과' in top
    assert '사양이라는 댓글 주장' in top
    assert 'Classification' not in page and '>SPEC_CLAIM<' not in page
    assert store.get_finding(fid)['detail'] == 'VP-580 본문은 입력에 없어 대조하지 못했습니다.'


def test_latest_analysis_excludes_current_run_and_empty_legacy_kind(world):
    store = world['store']
    old = store.latest_issue_analysis('VP-102', 'vxvue', exclude_run_id='new-run')
    store.add_finding('new-run', 'qa-comment-analysis', 'current-run', {
        'subject': 'VP-102', 'analysis_type': COMMENT, 'verdict': 'SPEC_CLAIM', 'summary': '이번 실행'}, product='vxvue')
    store.add_finding('other-run', 'qa-trace-gap', 'legacy-kind', {
        'subject': 'VP-102', 'verdict': 'TC 없음', 'summary': '옛 형식'}, product='vxvue')
    assert store.latest_issue_analysis('VP-102', 'vxvue', exclude_run_id='new-run')['id'] == old['id']


def test_comment_evidence_label_and_spec_link_use_saved_ids(world):
    from app.modules.qa_agent.dashboard import finding_explanation
    from app.modules.daily_qa.settings import PolarionSettings
    finding = {'subject': 'VP-7237', 'subject_title': '전송', 'detail': 'VP-580 본문 미확인',
               'sections': {'comments': [{'comment_id': 'c2', 'classification': 'SPEC_CLAIM'}]}}
    polarion = PolarionSettings('https://alm.example', '', 'Sample Project', '', '')
    info = finding_explanation(finding, [comment_event()], polarion=polarion)
    assert info['comment_locations']['c2'] == '댓글 1 (2026-10-12 13:24)'
    assert info['srs_links'] == [{'id': 'VP-580', 'url': 'https://alm.example/polarion/#/project/Sample%20Project/workitem?id=VP-580'}]


def test_comment_explanation_in_http_browser(world):
    """상세 경로의 실제 템플릿·정적 JS·원문 펼치기. 외부 서비스·AI 호출 없음."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path
    from urllib.parse import urlparse
    playwright = pytest.importorskip('playwright.sync_api')
    finding = world['store'].list_findings(run_id=world['changed']['run_id'], analysis_type=COMMENT)[0]
    static_root = Path(__file__).resolve().parents[1] / 'app' / 'web'
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            path = urlparse(self.path).path
            if path.startswith('/static/'):
                file = static_root / path.lstrip('/')
                body = file.read_bytes() if file.is_file() else b''
                kind = 'text/javascript' if file.suffix == '.js' else 'text/css'
            elif path == '/qa-agent/status':
                body = b'{"running":false,"current_run_id":null}'
                kind = 'application/json'
            else:
                response = world['client'].get(path)
                body, kind = response.content, 'text/html; charset=utf-8'
            self.send_response(200)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                for width in (390, 1920):
                    page = browser.new_page(viewport={'width': width, 'height': 1080})
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(f'http://127.0.0.1:{server.server_port}/qa-agent/findings/{finding["id"]}',
                              wait_until='domcontentloaded', timeout=120000)
                    explanation = page.locator('#analysis-explanation')
                    assert '판단과 할 일' in explanation.inner_text()
                    explanation.locator('details > summary').first.click()
                    original = explanation.locator('.comment-original').first
                    original.locator('summary').click()
                    assert original.get_attribute('open') is not None
                    assert '원인은 캐시 초기화 누락' in original.inner_text()
                    page.get_by_role('tab', name='근거 자료', exact=True).click()
                    assert page.locator('#evidence-panel').is_visible()
                    assert not explanation.is_visible()
                    page.get_by_role('tab', name='근거 자료', exact=True).press('ArrowRight')
                    assert page.locator('#processing').is_visible()
                    page.get_by_role('tab', name='분석 기록·처리 상태', exact=True).press('Home')
                    assert explanation.is_visible()
                    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                    assert not errors
                    page.close()
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize('kind', ['FIXED_ISSUE','SPEC_DECISION'])
def test_all_issue_analysis_inputs_exclude_display_only_author(tmp_path, kind):
    raw = [{'id': 'c2', 'created': '', 'text': '조치 내용', 'author': 'display-only-author'}]
    corpus = corpus_of([mk_issue('VP-7237', rd='FIXED')])
    item = build_item(target(kind, 'VP-7237', [comment_event(author='event-author')]),
                      corpus, corpus.known(), cfg_with(tmp_path), comments_loader=lambda _: raw)
    encoded = json.dumps(item)
    assert 'display-only-author' not in encoded and 'event-author' not in encoded
    assert raw[0]['author'] == 'display-only-author'


def test_collected_short_and_long_prefix_srs_ids_are_exact_inputs(tmp_path):
    corpus = corpus_of([mk_issue('VP-7237', linked=('VP-1','LONGPRODUCT-1234567'))],
                       [mk_srs('VP-1'),mk_srs('LONGPRODUCT-1234567')])
    item = build_item(target(COMMENT,'VP-7237',[comment_event('VP-1과 LONGPRODUCT-1234567 사양 참고')]),
                      corpus,corpus.known(),cfg_with(tmp_path))
    assert [row['id'] for row in item['candidates']['srs']] == ['VP-1','LONGPRODUCT-1234567']


def test_known_issue_and_unknown_reference_are_not_missing_specifications(tmp_path):
    corpus = corpus_of([mk_issue('VP-7237',linked=('VP-800','VP-404')),mk_issue('VP-800')])
    item = build_item(target(COMMENT,'VP-7237',[comment_event('VP-800 중복 이슈 참고')]),
                      corpus,corpus.known(),cfg_with(tmp_path))
    assert item['missing_srs_ids'] == []
    assert item['unresolved_reference_ids'] == ['VP-404']
