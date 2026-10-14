"""Read-only result search, independent of AI execution and collection."""
from urllib.parse import urlencode
from app.modules.qa_agent import dashboard as dash

STATES = {'open': '미처리', 'in_progress': '진행 중', 'done': '확인 완료', 'hold': '보류'}
SORTS = {'updated': '이슈 업데이트 최신순', 'author': '작성자별 · 업데이트 최신순', 'number': '이슈 번호 내림차순', 'analyzed': 'AI 분석 최신순'}
LEVEL_HELP = {
    '사양과 다름': '사양 또는 이전 판단 근거와 충돌하는 결과입니다.',
    '검토 필요': '부분 일치·사양 미정 등 QA의 추가 판단이 필요한 결과입니다. 분류되지 않은 판정도 포함합니다.',
    '근거 부족': '판단에 필요한 자료가 부족합니다. 자료 확인 전 결론을 확정하지 않습니다.',
    '참고': '원인·조치·재현 등 대응에 참고할 정보입니다. 별도 조치가 필요할 수도 있습니다.',
    '문제 없음': 'AI가 사양 일치·검증 범위 충족·QA 영향 없음으로 판단했습니다. 실제 검증 PASS를 뜻하지 않습니다.'}


def search(choice, filters):
    page = max(1, int(filters.get('page') or 1))
    category = filters.get('category', '')
    state = filters.get('state', '')
    level = filters.get('level', '')
    if category not in ('', 'spec', 'issues') or state not in ('', *STATES) or level not in ('', *dash.LEVEL_ORDER):
        raise ValueError('검색 조건을 확인해 주세요.')
    since = until = ''
    if filters.get('start') or filters.get('end'):
        first, last = dash.parse_period(filters.get('start', ''), filters.get('end', ''))
        since, until = dash._utc_bounds(first, last)
    verdicts = [v for v, label in dash.VERDICT_LEVEL.items() if label == level] if level else None
    if level == '검토 필요':
        verdicts = [v for v, label in dash.VERDICT_LEVEL.items() if label != level]
    found = choice.store.storage.qa_search_findings(choice.cfg.slug, category=category,
        subject=filters.get('subject', '').strip().upper(), reporter=filters.get('reporter', ''),
        query=filters.get('q', '').strip(), state=state, verdicts=verdicts, exclude_verdicts=level == '검토 필요',
        since=since, until=until, run_id=filters.get('run_id', ''), page=page, include_legacy=choice.cfg.is_legacy_product,
        sort=filters.get('sort') or 'updated')
    pages = max(1, (found['total'] + 49) // 50)
    params = {key: value for key, value in filters.items() if value and key != 'page'}
    params['product'] = choice.cfg.product
    return {**found, 'cards': [dash.card(item) for item in found['items']], 'sorts': SORTS,
            'states': STATES, 'level_help': LEVEL_HELP, 'levels': dash.LEVEL_ORDER, 'filters': filters,
            'page': page, 'pages': pages,
            'prev_url': '?' + urlencode({**params, 'page': page - 1}),
            'next_url': '?' + urlencode({**params, 'page': page + 1})}
