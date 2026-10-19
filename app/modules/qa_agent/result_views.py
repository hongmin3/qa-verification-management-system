"""Read-only result search, independent of AI execution and collection."""
from collections import defaultdict
from urllib.parse import urlencode
from app.modules.qa_agent import dashboard as dash
from app.modules.qa_agent import issue_body

PAGE_SIZE = 50
STATES = {'open': '미처리', 'in_progress': '진행 중', 'done': '확인 완료', 'hold': '보류'}
SORTS = {'updated': '이슈 업데이트 최신순', 'author': '작성자별 · 업데이트 최신순', 'number': '이슈 번호 내림차순', 'analyzed': 'AI 분석 최신순'}
#: 빠른 칩. 값이 `quick` 이면 같은 이름의 조건, 값이 `category` 면 분석 종류 조건이다 (REQ-QAINTEL-046).
QUICKS = {'review': '검토 필요', 'open': '미처리', 'changed': '분석 이후 변경'}
CATEGORIES = {'issues': '이슈', 'spec': '사양', 'legacy': '이전 형식'}
LEVEL_HELP = {
    '사양과 다름': '사양 또는 이전 판단 근거와 충돌하는 결과입니다.',
    '검토 필요': '부분 일치·사양 미정 등 QA의 추가 판단이 필요한 결과입니다. 분류되지 않은 판정도 포함합니다.',
    '근거 부족': '판단에 필요한 자료가 부족합니다. 자료 확인 전 결론을 확정하지 않습니다.',
    '참고': '원인·조치·재현 등 대응에 참고할 정보입니다. 별도 조치가 필요할 수도 있습니다.',
    '문제 없음': 'AI가 사양 일치·검증 범위 충족·QA 영향 없음으로 판단했습니다. 실제 검증 PASS를 뜻하지 않습니다.'}
#: 목록 필터 중 주소에 실리는 값. 쓰지 않는 값은 빈 문자열이다.
FILTER_KEYS = ('category', 'subject', 'subject_id', 'reporter', 'q', 'state', 'level', 'start', 'end', 'run_id', 'sort', 'view', 'quick', 'group')


def _freshness_inputs(choice, items):
    """카드마다 최신성을 판정한다. 대기 이벤트는 제품 전체를 한 번만 읽고 이슈 원문은 저장된 스냅샷에서 읽는다 (REQ-QAINTEL-020)."""
    pending = defaultdict(list)
    for event in choice.store.list_events(product=choice.cfg.slug, statuses=dash.PENDING_EVENT_STATUSES):
        pending[event.get('entity_id')].append(event)
    result = {}
    for item in items:
        if item.get('analysis_type') in dash.SRS_SUBJECT_KINDS:
            result[item['id']] = dash.freshness(item, None, [])
            continue
        issue = issue_body.load(choice.cfg.slug, item['subject'], snapshot_dir=choice.cfg.issue_snapshot_dir)
        result[item['id']] = dash.freshness(item, issue, pending.get(item['subject'], []))
    return result


def _triggers(choice, items):
    """카드마다 `왜 분석했나` 한 줄의 재료. 쪽에 보이는 결과의 이벤트만 한 번에 읽는다."""
    ids = sorted({event_id for item in items for event_id in item.get('event_ids') or []})
    events = {event['id']: event for event in choice.store.list_events(ids=ids)} if ids else {}
    return {item['id']: dash.trigger_line([events[i] for i in item.get('event_ids') or [] if i in events]) for item in items}


def author_groups(cards):
    """작성자 정렬로 가져온 카드를 작성자별로 묶는다. 작성자 ID가 없는 카드는 `ID 미확인` 으로 맨 뒤에 모인다."""
    groups = []
    for item in cards:
        person = item.get('reporter_id') or ''
        if not groups or groups[-1]['key'] != person:
            groups.append({'key': person, 'id': person or 'ID 미확인', 'name': item.get('reporter_name') or '이름 미확인', 'cards': []})
        groups[-1]['cards'].append(item)
    return groups


def search(choice, filters, path='/qa-agent/results'):
    page = max(1, int(filters.get('page') or 1))
    category = filters.get('category', '')
    state = filters.get('state', '')
    level = filters.get('level', '')
    quick = filters.get('quick', '')
    group = filters.get('group', '')
    view = filters.get('view') or ('all' if filters.get('run_id') else 'issue')
    if (category not in ('', *CATEGORIES) or state not in ('', *STATES) or level not in ('', *dash.LEVEL_ORDER)
            or quick not in ('', *QUICKS) or view not in ('issue', 'all') or group not in ('', 'author')):
        raise ValueError('검색 조건을 확인해 주세요.')
    # 빠른 칩은 같은 뜻의 자세한 조건보다 앞선다. 주소를 손으로 고쳐 둘이 어긋나면 칩을 따른다.
    if quick == 'review':
        level = '검토 필요'
    elif quick == 'open':
        state = 'open'
    since = until = ''
    if filters.get('start') or filters.get('end'):
        first, last = dash.parse_period(filters.get('start', ''), filters.get('end', ''))
        since, until = dash._utc_bounds(first, last)
    verdicts = [v for v, label in dash.VERDICT_LEVEL.items() if label == level] if level else None
    if level == '검토 필요':
        verdicts = [v for v, label in dash.VERDICT_LEVEL.items() if label != level]
    sort = 'author' if group == 'author' else filters.get('sort') or 'updated'
    storage = choice.store.storage
    query = dict(category=category, subject=filters.get('subject', '').strip().upper(), subject_exact=filters.get('subject_id', '').strip(), reporter=filters.get('reporter', ''),
                 query=filters.get('q', '').strip(), state=state, verdicts=verdicts, exclude_verdicts=level == '검토 필요',
                 since=since, until=until, run_id=filters.get('run_id', ''), include_legacy=choice.cfg.is_legacy_product,
                 sort=sort, latest_only=view == 'issue')
    if quick == 'changed':
        # 최신성은 이슈 스냅샷을 읽어야 알 수 있어 SQL 조건으로 쓰지 못한다. 나머지 조건을 DB 에서 먼저 적용한 모든 행을
        # 파이썬에서 판정하고, 결과 수와 페이지는 그 판정 뒤의 값으로 나눈다 (REQ-QAINTEL-046).
        found = storage.qa_search_findings(choice.cfg.slug, limit=None, **query)
        fresh = _freshness_inputs(choice, found['items'])
        kept = [item for item in found['items'] if fresh[item['id']]['state'] == 'changed_after']
        found = {**found, 'items': kept[(page - 1) * PAGE_SIZE:page * PAGE_SIZE], 'total': len(kept)}
    else:
        found = storage.qa_search_findings(choice.cfg.slug, page=page, limit=PAGE_SIZE, **query)
        fresh = _freshness_inputs(choice, found['items'])
    triggers = _triggers(choice, found['items'])
    cards = [dash.card(item, freshness=fresh[item['id']], trigger=triggers[item['id']]) for item in found['items']]
    pages = max(1, (found['total'] + PAGE_SIZE - 1) // PAGE_SIZE)
    params = {key: value for key, value in filters.items() if value and key != 'page'}
    params['product'] = choice.cfg.product

    def link(**changes):
        """지금 조건에서 몇 가지만 바꾼 주소. 값이 빈 문자열이면 그 조건을 뺀다. 쪽은 늘 처음으로 돌아간다."""
        merged = {**params, **changes}
        return path + '?' + urlencode({key: value for key, value in merged.items() if value})

    chips = [{'label': name, 'active': quick == code, 'url': link(quick='' if quick == code else code, level='' if code == 'review' else params.get('level', ''),
                                                              state='' if code == 'open' else params.get('state', ''))} for code, name in QUICKS.items()]
    chips += [{'label': name, 'active': category == code, 'url': link(category='' if category == code else code)} for code, name in CATEGORIES.items()]
    for card_ in cards:
        card_['history_url'] = link(subject_id=card_['subject'], view='all', quick='', category='', q='', level='', state='', start='', end='', reporter='', subject='', run_id='')
    return {**found, 'cards': cards, 'sorts': SORTS, 'states': STATES, 'level_help': LEVEL_HELP, 'levels': dash.LEVEL_ORDER,
            'quicks': QUICKS, 'categories': CATEGORIES, 'filters': {**filters, 'view': view},
            'groups': author_groups(cards) if group == 'author' else [], 'path': path, 'chips': chips,
            'view': view, 'group': group, 'view_url': link(view='all' if view == 'issue' else 'issue'),
            'group_url': link(group='' if group == 'author' else 'author'), 'reset_url': f"{path}?" + urlencode({'product': choice.cfg.product}),
            'page': page, 'pages': pages,
            'prev_url': '?' + urlencode({**params, 'page': page - 1}),
            'next_url': '?' + urlencode({**params, 'page': page + 1})}
