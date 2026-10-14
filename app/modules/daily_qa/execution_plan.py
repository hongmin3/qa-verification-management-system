"""AI-free prepared inputs with product, source and expiration checks."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


def public(plan):
    if not plan:
        return None
    return {key: plan[key] for key in ('id', 'product', 'created_at', 'expires_at', 'scope', 'basis', 'targets')}


def policy(cfg, selection=None):
    return {'model': cfg.analysis_model, 'review_model': cfg.review_model, 'effort': 'medium',
            'version': selection, 'per_run': cfg.savings_per_run, 'per_day': cfg.savings_per_day,
            'input_chars': cfg.intelligence.max_input_chars, 'savings': cfg.savings_enabled}
