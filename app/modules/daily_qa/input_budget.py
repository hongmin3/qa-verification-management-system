"""Lossless candidate sharing and size-aware batches (REQ-QAINTEL-040)."""
from __future__ import annotations

import copy
import json
from collections import Counter
from dataclasses import replace

from app.core.security_filter import mask_payload


def _key(candidate: dict) -> str:
    return json.dumps({k: v for k, v in candidate.items() if k != 'match'},
                      ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def compact_payload(payload: dict) -> dict:
    """Keep per-target order/match, share only identical complete evidence bodies."""
    result = copy.deepcopy(payload)
    if 'shared_candidates' in result:
        return result
    candidates = [candidate for item in result.get('items', [])
                  for values in item.get('candidates', {}).values() for candidate in values
                  if isinstance(candidate, dict) and 'shared_candidate' not in candidate]
    counts = Counter(_key(candidate) for candidate in candidates)
    keys, shared = {}, {}
    for candidate in candidates:
        key = _key(candidate)
        if counts[key] < 2 or len(key) < 160:
            continue
        if key not in keys:
            reference = f'c{len(keys) + 1}'
            keys[key] = reference
            shared[reference] = {k: v for k, v in candidate.items() if k != 'match'}
        match = {'match': candidate['match']} if 'match' in candidate else {}
        candidate.clear()
        candidate.update(shared_candidate=keys[key], **match)
    if shared:
        result['shared_candidates'] = shared
    return result


def input_text(payload: dict) -> str:
    # Large single-item tasks use Read rather than inline input. Preserve the
    # line structure so that one truncated CLI line cannot hide the whole file.
    return json.dumps(compact_payload(payload), ensure_ascii=False, indent=1)


def split_tasks(tasks: list, max_chars: int) -> list:
    """Split only between items; a single large item keeps all evidence."""
    result = []
    numbers: Counter = Counter()
    for task in tasks:
        chunks, chunk = [], []
        for item in task.payload.get('items', []):
            proposed = {**task.payload, 'items': [*chunk, item]}
            masked, _ = mask_payload(proposed)
            if chunk and len(input_text(masked)) > max(1, max_chars):
                chunks.append(chunk)
                chunk = []
            chunk.append(item)
        if chunk:
            chunks.append(chunk)
        if not chunks:
            result.append(task)
            continue
        prefix = task.task_id.rsplit('-', 1)[0]
        for items in chunks:
            numbers[prefix] += 1
            result.append(replace(task, task_id=f'{prefix}-{numbers[prefix]:03d}',
                                  payload={**task.payload, 'items': items}))
    return result


def select_backlog(events: list[dict], today: str, maximum: int) -> tuple[list[dict], dict]:
    """Shared by execution and the read-only preview; today is YYYYMMDD."""
    fresh = [e for e in events if str(e.get('run_id') or '')[:8] >= today]
    backlog = sorted((e for e in events if str(e.get('run_id') or '')[:8] < today), key=lambda e: e['id'])
    taken = backlog[:max(maximum, 0)]
    return taken + fresh, {'waiting': len(backlog), 'taken': len(taken)}
