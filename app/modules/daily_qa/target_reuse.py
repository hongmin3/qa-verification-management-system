"""Conservative per-target links to validated results; full evidence stays in the key."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, is_dataclass
from pathlib import Path
from app.modules.daily_qa.result_cache import _files

PREFIX = 'daily_qa:target:v1:'
DISPLAY_KEYS = {'author', 'reporter_id', 'reporter_name', 'reporter_source', 'reporter_synced_at'}


def semantic(value):
    if isinstance(value, dict):
        return {key: semantic(part) for key, part in value.items() if key not in DISPLAY_KEYS}
    if isinstance(value, (list, tuple)):
        return [semantic(part) for part in value]
    if hasattr(value, 'model_dump'):
        return semantic(value.model_dump())
    if is_dataclass(value):
        return semantic(asdict(value))
    return value


def digest(value):
    return hashlib.sha256(json.dumps(semantic(value), ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), default=str).encode()).hexdigest()


def context(corpus, workspace, cfg, selection, answers):
    code = Path(__file__).parent
    return digest({'issues': corpus.issues, 'srs': corpus.srs, 'chunks': corpus.spec_chunks,
                   'labels': corpus.chunk_labels, 'tcs': corpus.tc_rows, 'manuals': corpus.manuals,
                   'unreadable': corpus.unreadable_documents, 'version': selection,
                   'rules': _files(workspace.root / 'rules'), 'skills': _files(workspace.root / '.claude/skills'),
                   'policy': cfg.intelligence, 'tc_candidate_limit': cfg.tc_candidate_limit,
                   'instructions': [(name, digest((workspace.root / name).read_text(encoding='utf-8')))
                                    for name in ('CLAUDE.md', '.claude/settings.json') if (workspace.root / name).is_file()],
                   'axes': cfg.product_profile.regression_axes,
                   'checklist': cfg.product_profile.checklist, 'answers': answers,
                   'implementation': [(name, digest((code / name).read_text(encoding='utf-8'))) for name in
                                      ('intelligence.py', 'evidence_validation.py', 'schema.py', 'target_reuse.py',
                                       'agent_runner.py', 'rule_context.py', 'pipeline.py', 'result_cache.py', 'completion.py')]})


def key(product, kind, subject, events, full_context):
    # Operational dates and batch IDs do not change a review. Different before/after facts do.
    changes = [{name: event.get(name) for name in ('event_type', 'before', 'after', 'changed_fields')} for event in events]
    changes = sorted({digest(change): change for change in changes}.values(), key=digest)
    return PREFIX + digest([product, kind, subject, changes, full_context])


def _finding_digest(row):
    # Human processing is separate. A changed AI conclusion invalidates reuse.
    return digest({key: row.get(key) for key in ('product', 'subject', 'analysis_type', 'summary', 'detail',
        'verdict', 'confidence', 'evidence', 'sections', 'draft_tcs', 'action', 'related_ids')})


def save(storage, fingerprint, finding_ids, model):
    try:
        rows = [storage.daily_qa_get_finding(item) for item in finding_ids]
        storage.cache_set(fingerprint, {'fingerprint': fingerprint, 'finding_ids': finding_ids, 'model': model,
                                       'results': [_finding_digest(row) for row in rows]})
        return True
    except (ValueError, TypeError, AttributeError, OSError, sqlite3.Error):
        return False


def load(storage, fingerprint, product, kind, subject):
    try:
        value = storage.cache_get(fingerprint)
        if not isinstance(value, dict) or value.get('fingerprint') != fingerprint or not value.get('finding_ids'):
            return None
        findings = [storage.daily_qa_get_finding(item) for item in value['finding_ids']]
        if any(not row or (row['product'], row['analysis_type'], row['subject']) != (product, kind, subject) for row in findings):
            return None
        if value.get('results') != [_finding_digest(row) for row in findings]:
            return None
        return {**value, 'analyzed_at': findings[0]['created_at'], 'run_id': findings[0]['run_id']}
    except (ValueError, TypeError, KeyError, sqlite3.Error):
        return None
