"""AI-free prepared inputs with product, source and expiration checks."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.modules.daily_qa import holidays
from app.modules.daily_qa.change_events import (
    ANALYSIS_LABELS, EVENT_LABELS, SPEC_COVERAGE, group_targets, merge_comment_targets)

TTL = timedelta(minutes=30)


def _hash(path):
    path = Path(path)
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    if path.is_dir():
        parts = [(p.relative_to(path).as_posix(), _hash(p)) for p in sorted(path.rglob('*')) if p.is_file()]
        return hashlib.sha256(json.dumps(parts).encode()).hexdigest()
    return 'missing'


def create(storage, product, targets, data, paths, policy, *, now=None, scope='all', basis=None):
    now = now or datetime.now(timezone.utc)
    plan = {'id': uuid.uuid4().hex, 'product': product, 'created_at': now.isoformat(),
            'expires_at': (now + TTL).isoformat(), 'scope': scope, 'basis': basis or {},
            'targets': targets, 'data': data, 'policy': policy,
            'files': {str(path): _hash(path) for path in paths}}
    storage.qa_save_plan(plan)
    return plan


def validate(plan, product, policy, *, now=None):
    now = now or datetime.now(timezone.utc)
    if not plan or plan.get('product') != product:
        raise ValueError('해당 제품의 준비된 대상이 없습니다.')
    if now >= datetime.fromisoformat(plan['expires_at']):
        raise ValueError('대상 준비 후 30분이 지났습니다. 다시 확인해 주세요.')
    if plan['policy'] != policy or any(_hash(path) != value for path, value in plan['files'].items()):
        raise ValueError('자료·지침·검증 버전 또는 설정이 바뀌었습니다. 대상을 다시 확인해 주세요.')
    return plan


def select(storage, plan_id, product, keys, policy, *, now=None, review=False):
    plan = validate(storage.qa_get_plan(plan_id, product), product, policy, now=now)
    allowed = {target['key'] for target in plan['targets']}
    if not keys or len(keys) != len(set(keys)) or not set(keys) <= allowed:
        raise ValueError('준비된 대상에서 하나 이상 선택해 주세요.')
    if review and len(keys) != 1:
        raise ValueError('Opus 재검토는 대상 하나를 선택해 주세요.')
    return storage.qa_save_selection(plan_id, product, {'plan_id': plan_id, 'keys': keys, 'review': bool(review)})


def _kst(value, suffix=' (한국 시간)'):
    """저장된 ISO 시각을 `2026-10-14 17:00 (한국 시간)`으로 바꾼다. 없거나 읽지 못하면 빈 문자열이다."""
    if not value:
        return ''
    try:
        moment = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return ''
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(holidays.KST).strftime('%Y-%m-%d %H:%M') + suffix


def _reasons(events):
    """대상을 고른 변경 이벤트의 이름. 같은 이름은 한 번만, 처음 나온 순서대로 쓴다."""
    names = [EVENT_LABELS.get(event.get('event_type'), event.get('event_type') or '') for event in events]
    return ', '.join(dict.fromkeys(name for name in names if name))


def _rows(plan, reporters):
    """선택 화면의 행. 작성자·업데이트는 이슈 대상에만 있고, 없는 값은 지어내지 않는다."""
    data = plan.get('data') or {}
    grouped = group_targets(data.get('events') or [])
    merge_comment_targets(grouped)
    issues = {item.get('id'): item for item in data.get('issues') or []}
    rows = []
    for target in plan['targets']:
        kind, subject = target.get('kind', ''), target.get('subject', '')
        row = {**target, 'kind_label': ANALYSIS_LABELS.get(kind, kind).removesuffix(' 분석'),
               'reason': _reasons(grouped.get(kind, {}).get(subject, [])),
               'reporter_name': '', 'issue_updated_kst': ''}
        if kind != SPEC_COVERAGE:
            item, saved = issues.get(subject) or {}, (reporters or {}).get(subject) or {}
            reporter_id = item.get('reporter_id') or saved.get('reporter_id') or ''
            name = item.get('reporter_name') or saved.get('reporter_name') or ''
            row['reporter_name'] = name or (f'이름 미확인 ({reporter_id})' if reporter_id else '')
            row['issue_updated_kst'] = _kst(item.get('updated')) or _kst(saved.get('issue_updated_at'))
        rows.append(row)
    return rows


def public(plan, now=None, reporters=None):
    """화면에 내보내는 준비 목록. 원문 자료와 파일 경로는 넣지 않는다.

    `expired`는 실행이 거절하는 시점(생성 30분 뒤)과 같다. `reporters`는 저장된 이슈 작성자
    (이슈 번호 → 작성자·업데이트)이며 준비 때 이름을 못 읽은 행만 채운다.
    """
    if not plan:
        return None
    now = now or datetime.now(timezone.utc)
    return {**{key: plan[key] for key in ('id', 'product', 'created_at', 'expires_at', 'scope', 'basis')},
            'expired': now >= datetime.fromisoformat(plan['expires_at']),
            'created_kst': _kst(plan['created_at'], ''), 'targets': _rows(plan, reporters)}


def policy(cfg, selection=None):
    return {'model': cfg.analysis_model, 'review_model': cfg.review_model, 'effort': 'medium',
            'version': selection, 'per_run': cfg.savings_per_run, 'per_day': cfg.savings_per_day,
            'input_chars': cfg.intelligence.max_input_chars, 'savings': cfg.savings_enabled}
