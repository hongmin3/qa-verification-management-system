"""Read-only corpus adapter. The SQLite cache is derived data outside the source root.

Reuses knowledge.py's XLSX reader; does not infer support or semantic coverage.
No network calls, model API, source edits, OCR, or product execution occur here.
"""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
from contextlib import closing
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from . import knowledge as k

SCHEMA = 'vxvue-corpus-3'
FORMATS = {'.pdf', '.docx', '.xlsx', '.md', '.txt', '.json', '.csv', '.tsv'}
KINDS = {'specification', 'manual', 'testcase', 'issue', 'rules', 'other'}
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
LIMITATIONS = [
    'Search evidence is not a product support, Pass/Fail, or semantic coverage verdict.',
    'PDF text does not establish strike/deletion, visual table alignment, or image content; inspect the original.',
    'Word revisions/strike are retained as review evidence; acceptance and visual layout require the original.',
    'XLSX formulas are cached source values, not recalculated; historical/model columns require applicability review.',
    'Filename revision/date and version mentions are metadata candidates, not confirmed product applicability.',
    'Only local files are searched; live Redmine is a separate explicit read-only workflow.',
]


def normalize(text):
    return unicodedata.normalize('NFKC', text).casefold()


def kind(path):
    name = normalize(path.name)
    if '지침' in name or 'qa 작성 규칙' in name:
        return 'rules'
    if '(tc)' in name or 'testcase' in name or 'test_case' in name or 'checklist' in name:
        return 'testcase'
    if '(사양서)' in name or 'spec' in name or 'srs' in name:
        return 'specification'
    if '(매뉴얼)' in name or 'manual' in name or 'integration' in name or 'protocol' in name:
        return 'manual'
    if 'issue' in name or '이슈' in name or 'polarion' in name or 'redmine' in name:
        return 'issue'
    return 'other'


def identifiers(text):
    bare = re.findall(r'(?<![\w-])\d{2}-\d{2}-\d{2}(?![\w-])', text)
    named = re.findall(r'\b(?:SRS|VP)[-_ ]\d+(?:[-_]\d+)*\b', text, re.I)
    requirements = sorted(set(bare + [x for x in named if not re.fullmatch(r'SRS[-_ ]\d{2}-\d{2}-\d{2}', x, re.I)]))
    tcs = sorted(set(re.findall(r'\bTC[-_]?\d+(?:[-_]\d+)*\b', text, re.I)))
    issues = sorted(set(re.findall(r'(?:#|/issues/)(\d{4,8})(?!\d)', text)))
    return {'requirement_ids': requirements, 'tc_ids': tcs, 'issue_ids': issues}


def query_terms(query):
    if not isinstance(query, str) or not query.strip() or len(query) > 1000:
        raise ValueError('query must be nonempty and at most 1000 characters')
    words = re.findall(r'[a-z0-9]+(?:[-_.][a-z0-9]+)*|[가-힣]+', normalize(query))
    noise = {'관련', '사양', '사양서', '매뉴얼', '문서', '기능', '검색', '조회', '비교', '조건',
             '차이', '찾아줘', '알려줘', '보여줘', '확인해줘', '해줘', '무엇', '어떤',
             'the', 'for', 'and', 'or', 'find', 'show', 'please', 'spec', 'tc'}
    result = []
    for word in words:
        if re.fullmatch(r'[가-힣]+', word):
            word = re.sub(r'(?:에서는|에서|으로|과|와|을|를|은|는|이|가)$', '', word)
        elif word in {'tcs', 'testcases', 'documents'}:
            continue
        if word and word not in noise and word not in result:
            result.append(word)
    if not result:
        result = [normalize(query.strip())]
    return result[:20]


def chunk(unit, text, location, details=None, **metadata):
    return {'unit': unit, 'text': text, 'location': location, 'details': details or {}, **metadata}


def text_chunks(path):
    text = path.read_text(encoding='utf-8-sig')
    lines = text.splitlines(keepends=True)
    result, heading = [], None
    for start in range(0, len(lines), 80):
        batch = lines[start:start + 80]
        headings = [x.strip() for x in batch if re.match(r'^#{1,6}\s', x)]
        if headings:
            heading = headings[0]
        result.append(chunk(f'lines:{start + 1}-{start + len(batch)}', ''.join(batch),
                            {'line_start': start + 1, 'line_end': start + len(batch), 'section': heading}))
    return result, []


def pdf_chunks(path):
    import pymupdf as fitz
    result, warnings = [], []
    with fitz.open(path) as doc:
        if doc.needs_pass:
            raise ValueError('Encrypted PDF requires access outside this adapter')
        for number, page in enumerate(doc, 1):
            text = page.get_text(sort=True)
            if not text.strip():
                warnings.append({'page': number, 'status': 'no-extractable-text', 'requires': 'original-render-or-OCR'})
            result.append(chunk(f'page:{number}', text, {'page': number},
                                evidence_status='text-extracted-visual-review-required'))
    return result, warnings


def word_properties(prop):
    result = {}
    if prop is not None:
        for tag in ('strike', 'dstrike'):
            value = prop.find(W + tag)
            if value is not None:
                result[tag] = value.get(W + 'val', '1') not in ('0', 'false', 'off')
    return result


def word_styles(archive):
    if 'word/styles.xml' not in archive.namelist():
        return {}, {}, None
    xml = ET.fromstring(archive.read('word/styles.xml'))
    defaults = word_properties(xml.find(W + 'docDefaults/' + W + 'rPrDefault/' + W + 'rPr'))
    nodes = {n.get(W + 'styleId'): n for n in xml.findall(W + 'style')}
    cache = {}
    def resolve(key, seen=()):
        if key in cache:
            return cache[key]
        if key not in nodes or key in seen:
            return {}
        node = nodes[key]
        base = node.find(W + 'basedOn')
        result = dict(resolve(base.get(W + 'val'), (*seen, key))) if base is not None else {}
        result.update(word_properties(node.find(W + 'rPr')))
        cache[key] = result
        return result
    default_style = next((key for key, n in nodes.items() if n.get(W + 'type') == 'paragraph' and
                          n.get(W + 'default') in ('1', 'true', 'on')), None)
    return {key: resolve(key) for key in nodes}, defaults, default_style


def word_paragraph(node, styles=None, defaults=None, default_style=None, revision_flags=()):
    parts, runs = [], []
    styles = styles or {}
    p_style = node.find(W + 'pPr/' + W + 'pStyle')
    paragraph = dict(defaults or {})
    paragraph.update(styles.get(p_style.get(W + 'val') if p_style is not None else default_style, {}))
    def visit(n, inherited=revision_flags):
        flags = list(inherited)
        if n.tag in (W + 'del', W + 'moveFrom'):
            flags.append('deleted')
        if n.tag in (W + 'ins', W + 'moveTo'):
            flags.append('inserted')
        if n.tag == W + 'r':
            prop = n.find(W + 'rPr')
            effective = dict(paragraph)
            r_style = prop.find(W + 'rStyle') if prop is not None else None
            if r_style is not None:
                effective.update(styles.get(r_style.get(W + 'val'), {}))
            effective.update(word_properties(prop))
            if any(effective.values()):
                flags.append('strike')
        if n.tag in (W + 't', W + 'delText'):
            value = n.text or ''
            parts.append(value)
            runs.append({'text': value, 'flags': flags})
        elif n.tag in (W + 'tab', W + 'br', W + 'cr'):
            parts.append('\t' if n.tag == W + 'tab' else '\n')
        for child in n:
            visit(child, tuple(flags))
    visit(node)
    return ''.join(parts), runs


def docx_chunks(path):
    result = []
    with ZipFile(path) as archive:
        styles, defaults, default_style = word_styles(archive)
        names = [n for n in archive.namelist() if re.fullmatch(r'word/(document|header\d*|footer\d*|footnotes|endnotes|comments)\.xml', n)]
        for name in sorted(names):
            xml = ET.fromstring(archive.read(name))
            parents = {child: parent for parent in xml.iter() for child in parent}
            heading = None
            for i, p in enumerate(xml.iter(W + 'p'), 1):
                ancestor = parents.get(p)
                revisions = []
                while ancestor is not None:
                    if ancestor.tag in (W + 'del', W + 'moveFrom'):
                        revisions.append('deleted')
                    elif ancestor.tag in (W + 'ins', W + 'moveTo'):
                        revisions.append('inserted')
                    ancestor = parents.get(ancestor)
                text, runs = word_paragraph(p, styles, defaults, default_style, tuple(revisions))
                if not text.strip():
                    continue
                style = p.find(W + 'pPr/' + W + 'pStyle')
                style_name = style.get(W + 'val', '') if style is not None else ''
                if 'heading' in style_name.casefold():
                    heading = text
                flags = {f for run in runs for f in run['flags']}
                result.append(chunk(f'{name}:paragraph:{i}', text, {'part': name, 'paragraph': i, 'section': heading},
                                    {'runs': runs, 'revision_flags': sorted(flags)},
                                    evidence_status='revision-review-required' if flags else 'text-extracted-review-required'))
    return result, []


def xlsx_chunks(path):
    result = []
    for sheet in k.read_workbook(path):
        hr = k.tc_header_row(sheet)
        header_rows = list(range(1, hr + 1))
        if 'VSS Specification' in path.name:
            header_rows = [4, 5, 8, 9, 10]
        header_cache = {k.column_name(c): [k.cell(sheet, k.column_name(c) + str(r)) for r in header_rows]
                        for c in range(1, sheet['max_column'] + 1)}
        rows = {}
        for ref in sheet['cells']:
            _, row = k.coordinates(ref)
            rows.setdefault(row, []).append(ref)
        id_column = next((k.column_name(c) for c in range(1, sheet['max_column'] + 1)
                          if hr and k.cell(sheet, k.column_name(c) + str(hr))['text'].strip().casefold() == 'tc_id'), None)
        for row, refs in sorted(rows.items()):
            cells = {ref: k.cell(sheet, ref) for ref in sorted(refs, key=lambda x: k.coordinates(x)[0])}
            # Include only merged context whose anchor lies on a different source row.
            for merged in sheet['merges']:
                start, end = merged.split(':') if ':' in merged else (merged, merged)
                x1, y1 = k.coordinates(start); x2, y2 = k.coordinates(end)
                if y1 < row <= y2:
                    for c in range(x1, x2 + 1):
                        ref = k.column_name(c) + str(row)
                        cells[ref] = k.cell(sheet, ref)
            if not any(v['text'] or v['comment'] or v['formula'] for v in cells.values()):
                continue
            headers = {}
            for ref in cells:
                col = k.column_name(k.coordinates(ref)[0])
                if header_rows and row > max(header_rows):
                    headers[col] = header_cache[col]
            text = '\n'.join(f"{ref}: {v['text']}" + (f"\nComment: {v['comment']}" if v['comment'] else '')
                             for ref, v in cells.items())
            header_text = '\n'.join(f"{col} header: " + ' | '.join(x['text'] for x in values if x['text'])
                                    for col, values in headers.items())
            roles = {}
            for ref in cells:
                col = k.column_name(k.coordinates(ref)[0])
                values = headers.get(col, [])
                field = values[-1]['text'].strip().casefold() if values else ''
                if 'expected' in field or 'procedure' in field or 'step' in field:
                    role = 'test-definition'
                elif 'comment' in field or '코멘트' in field or '비고' in field:
                    role = 'comment'
                elif 'spec' in field or '사양' in field:
                    role = 'model-spec'
                elif re.match(r'^v?\d+\.\d+\.', field) or 'result' in field or '결과' in field or re.fullmatch(r'\d+차', field):
                    role = 'execution-history'
                else:
                    role = 'unclassified-source'
                roles[ref] = role
            tc_id = k.cell(sheet, id_column + str(row))['text'] if id_column and row > hr else ''
            result.append(chunk(f"sheet:{sheet['name']}:row:{row}", text,
                                {'sheet': sheet['name'], 'row': row, 'cells': list(cells), 'sheet_state': sheet['state']},
                                {'cells': cells, 'headers': headers, 'header_row': hr},
                                search_context=header_text, tc_id=tc_id,
                                cell_roles=roles,
                                noncurrent_cells={ref: v['validity'] for ref,v in cells.items() if v['validity'] not in ('current','missing')},
                                sheet_review_required=bool(re.search(r'폐기|삭제|backup|\(구\)|legacy', sheet['name'], re.I)),
                                evidence_status='source-cells-review-required'))
    return result, []


def parse(path):
    if path.suffix.casefold() == '.xlsx':
        return xlsx_chunks(path)
    if path.suffix.casefold() == '.pdf':
        return pdf_chunks(path)
    if path.suffix.casefold() == '.docx':
        return docx_chunks(path)
    return text_chunks(path)


class Corpus:
    def __init__(self, root=None, index_path=None):
        self.root = Path(root or os.environ.get('VXVUE_KNOWLEDGE_ROOT') or k.DEFAULT_ROOT).resolve()
        root_key = hashlib.sha256(str(self.root).encode('utf-8')).hexdigest()[:16]
        self.index_path = Path(index_path or os.environ.get('VXVUE_INDEX_PATH') or
                               Path.home() / f'.codex/vxvue-knowledge/{root_key}/index.sqlite3').resolve()
        if self.index_path.is_relative_to(self.root):
            raise ValueError('Cache must be outside the read-only knowledge root')

    def connect(self):
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.index_path, timeout=120)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript('''
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, sha TEXT, status TEXT, metadata TEXT);
            CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY, document_id TEXT, unit TEXT,
                text TEXT, search_text TEXT, metadata TEXT, UNIQUE(document_id, unit));
            CREATE INDEX IF NOT EXISTS chunks_document ON chunks(document_id);
            CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(search_text, tokenize='trigram');
        ''')
        return db

    def source_paths(self):
        if not self.root.is_dir():
            raise FileNotFoundError(f'Knowledge folder unavailable: {self.root}')
        # A complete listing precedes cache reads. Failure never masquerades as an empty corpus.
        paths = []
        def listing_error(error):
            raise error
        for folder, dirs, files in os.walk(self.root, onerror=listing_error, followlinks=False):
            # Directory links cannot silently extend or shrink the declared source scope.
            for name in dirs:
                p = Path(folder) / name
                if p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction()):
                    raise ValueError(f'Linked knowledge directory requires an explicit root: {p}')
            paths.extend(Path(folder) / name for name in files if not name.startswith('~$'))
        paths.sort()
        for path in paths:
            if not path.resolve().is_relative_to(self.root):
                raise ValueError(f'Source resolves outside the knowledge root: {path}')
        return paths

    def verify_snapshot(self, db, paths=None):
        current_paths = self.source_paths()
        if paths is not None and current_paths != paths:
            raise ValueError('Knowledge inventory changed during reading; retry')
        current_ids = {p.relative_to(self.root).as_posix() for p in current_paths}
        stored_ids = {r['id'] for r in db.execute('SELECT id FROM documents')}
        if current_ids != stored_ids:
            raise ValueError('Knowledge inventory changed during reading; retry')
        for doc in db.execute("SELECT id,sha FROM documents WHERE status='indexed'"):
            try:
                current = k.fingerprint(self.root / doc['id'])
            except OSError as exc:
                raise ValueError('Source became unreadable during reading; retry: ' + doc['id']) from exc
            if current != doc['sha']:
                raise ValueError('Source changed during reading; retry: ' + doc['id'])

    def refresh(self, db):
        paths = self.source_paths()
        db.execute('BEGIN IMMEDIATE')
        try:
            stored = dict(db.execute('SELECT key,value FROM meta').fetchall())
            if stored.get('schema') != SCHEMA or stored.get('root') != str(self.root):
                # A full parser revision rebuild need not delete every FTS posting individually.
                db.execute('DROP TABLE search_index')
                db.execute("CREATE VIRTUAL TABLE search_index USING fts5(search_text, tokenize='trigram')")
                db.execute('DELETE FROM chunks'); db.execute('DELETE FROM documents')
                db.executemany('INSERT OR REPLACE INTO meta VALUES (?,?)', [('schema', SCHEMA), ('root', str(self.root))])
            seen = set()
            for path in paths:
                doc_id = path.relative_to(self.root).as_posix()
                seen.add(doc_id)
                sha = None
                revision = re.search(r'Rev\.?([\d.]+)', path.name, re.I)
                date = re.search(r'\((\d{6}|\d{8})\)', path.name)
                metadata = {'document_id': doc_id, 'document_name': path.name, 'file_path': str(path),
                            'document_kind': kind(path), 'document_revision': revision[1].rstrip('.') if revision else None,
                            'document_snapshot': date[1] if date else None, 'product_version': None,
                            'version_source': 'filename-candidate', 'warnings': []}
                try:
                    if not path.resolve().is_relative_to(self.root):
                        raise ValueError('Source resolves outside the knowledge root')
                    if path.suffix.casefold() not in FORMATS:
                        raise ValueError('Unsupported document format')
                    sha = k.fingerprint(path)
                    old = db.execute('SELECT sha,status FROM documents WHERE id=?', (doc_id,)).fetchone()
                    if old and old['sha'] == sha and old['status'] == 'indexed':
                        continue
                    chunks, warnings = parse(path)
                    if sha != k.fingerprint(path):
                        raise ValueError('Source changed during parsing; retry')
                    metadata['warnings'] = warnings
                    metadata['chunk_count'] = len(chunks)
                    metadata['version_mentions'] = sorted(set(re.findall(r'\b[Vv]?\d+\.\d+\.\d+(?:\.[\dA-Za-z]+)?(?:W\d+)?\b',
                                                              '\n'.join(c['text'] for c in chunks))))[:50]
                    status = 'indexed'
                except Exception as exc:
                    # Parsing failures are per-source evidence, never stale successful chunks.
                    metadata['error'] = {'type': type(exc).__name__, 'message': str(exc)}
                    chunks = []; status = 'source-read-failed'
                self.remove_chunks(db, doc_id)
                db.execute('INSERT OR REPLACE INTO documents VALUES (?,?,?,?)',
                           (doc_id, sha, status, json.dumps(metadata, ensure_ascii=False)))
                for c in chunks:
                    details = c.pop('details')
                    text = c.pop('text')
                    unit = c.pop('unit')
                    context = c.pop('search_context', '')
                    ids = identifiers(text)
                    if c.get('tc_id'):
                        ids['tc_ids'] = sorted(set(ids['tc_ids'] + [c['tc_id']]))
                    c.update(ids, details=details, support_status='unknown',
                             evidence_status=c.get('evidence_status', 'text-extracted-review-required'))
                    search_text = normalize(path.name + '\n' + c['location'].get('sheet', '') + '\n' + text + '\n' + context)
                    cursor = db.execute('INSERT INTO chunks(document_id,unit,text,search_text,metadata) VALUES (?,?,?,?,?)',
                                        (doc_id, unit, text, search_text, json.dumps(c, ensure_ascii=False)))
                    db.execute('INSERT INTO search_index(rowid,search_text) VALUES (?,?)', (cursor.lastrowid, search_text))
            for old in db.execute('SELECT id FROM documents').fetchall():
                if old['id'] not in seen:
                    self.remove_chunks(db, old['id'])
                    db.execute('DELETE FROM documents WHERE id=?', (old['id'],))
            self.verify_snapshot(db, paths)
            db.commit()
        except BaseException:
            db.rollback()
            raise

    @staticmethod
    def remove_chunks(db, doc_id):
        db.execute('DELETE FROM search_index WHERE rowid IN (SELECT id FROM chunks WHERE document_id=?)', (doc_id,))
        db.execute('DELETE FROM chunks WHERE document_id=?', (doc_id,))

    def scope(self, db):
        docs = [dict(json.loads(r['metadata']), sha256=r['sha'], status=r['status'])
                for r in db.execute('SELECT * FROM documents ORDER BY id')]
        errors = [{'document_id': d['document_id'], **d.get('error', {})} for d in docs if d['status'] != 'indexed']
        return {'root': str(self.root), 'index_path': str(self.index_path), 'files': len(docs),
                'indexed_files': sum(d['status'] == 'indexed' for d in docs), 'failed_files': len(errors),
                'errors': errors, 'warnings': [{'document_id': d['document_id'], 'warnings': d['warnings']}
                                             for d in docs if d['warnings']],
                'checked_utc': datetime.now(timezone.utc).isoformat(), 'documents': docs}

    def record(self, row, full=False, terms=()):
        doc = json.loads(row['doc_metadata'])
        meta = json.loads(row['metadata'])
        text = row['text']
        evidence = text
        clipped = False
        if not full and len(text) > 1800:
            pos = min((normalize(text).find(t) for t in terms if t in normalize(text)), default=0)
            start = max(0, pos - 250)
            evidence = text[start:start + 1800]
            clipped = True
        result = {**doc, **meta, 'sha256': row['sha'], 'chunk_id': doc['document_id'] + '::' + row['unit'],
                  'evidence': evidence, 'evidence_truncated': clipped, 'related_documents': [],
                  'package_and_conditions': 'Inspect the quoted source and cell headers; not inferred.',
                  'human_review_required': True}
        if not full:
            result.pop('details', None)
        return result

    @staticmethod
    def validate_pagination(limit, offset=0):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError('limit must be an integer from 1 to 100')
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError('offset must be a nonnegative integer')

    def search_documents(self, query, limit=20, document_kind=None, model=None, package=None):
        self.validate_pagination(limit)
        terms = query_terms(query)
        if document_kind and document_kind not in KINDS:
            raise ValueError('Unknown document kind')
        with closing(self.connect()) as db:
            self.refresh(db)
            long_terms = [t for t in terms if len(t) >= 3]
            candidates = set()
            if long_terms:
                expression = ' OR '.join('"' + t.replace('"', '""') + '"' for t in long_terms)
                candidates.update(r[0] for r in db.execute('SELECT rowid FROM search_index WHERE search_index MATCH ?', (expression,)))
            for t in terms:
                if len(t) < 3:
                    candidates.update(r[0] for r in db.execute('SELECT id FROM chunks WHERE instr(search_text,?)>0', (t,)))
            scored = []
            kinds = {r['id']: json.loads(r['metadata'])['document_kind'] for r in db.execute('SELECT id,metadata FROM documents')}
            ids = sorted(candidates)
            for start in range(0, len(ids), 500):
                batch = ids[start:start + 500]
                rows = db.execute('SELECT id,document_id,text,search_text FROM chunks WHERE id IN (' + ','.join('?' for _ in batch) + ')', batch)
                for row in rows:
                    if document_kind and kinds[row['document_id']] != document_kind:
                        continue
                    filters = [normalize(v) for v in (model, package) if v]
                    if any(value not in row['search_text'] for value in filters):
                        continue
                    hits = [t for t in terms if t in row['search_text']]
                    score = len(hits) * 100 + sum(t in normalize(row['text']) for t in hits) * 10
                    scored.append((score, row))
            scored.sort(key=lambda x: (-x[0], x[1]['document_id'], x[1]['id']))
            # Keep the integrated top-N useful even when historical TC rows vastly outnumber specifications.
            chosen = []
            if document_kind is None and limit > 1:
                for category in ('specification', 'manual', 'testcase', 'issue'):
                    best = next((x for x in scored if kinds[x[1]['document_id']] == category), None)
                    if best is not None and len(chosen) < limit:
                        chosen.append(best)
            chosen_ids = {x[1]['id'] for x in chosen}
            chosen.extend(x for x in scored if x[1]['id'] not in chosen_ids)
            chosen = chosen[:limit]
            scope = self.scope(db)
            status = 'partial-evidence' if scope['errors'] or scope['warnings'] else ('evidence-found' if scored else 'not-found-in-readable-sources')
            results = []
            for score, candidate in chosen:
                row = db.execute('SELECT c.*,d.metadata AS doc_metadata,d.sha FROM chunks c JOIN documents d ON c.document_id=d.id WHERE c.id=?', (candidate['id'],)).fetchone()
                results.append(dict(self.record(row, terms=terms), relevance_score=score))
            self.verify_snapshot(db)
            return {'query': query, 'query_terms': terms, 'status': status, 'support_status': 'unknown',
                    'matched': len(scored), 'returned': min(limit, len(scored)), 'truncated': len(scored) > limit,
                    'results': results,
                    'scope': scope, 'limitations': LIMITATIONS}

    def get_document(self, document_id, section=None, offset=0, limit=10):
        self.validate_pagination(limit, offset)
        if not isinstance(document_id, str):
            raise ValueError('document_id must be a relative inventory ID')
        candidate = (self.root / document_id).resolve()
        if Path(document_id).is_absolute() or not candidate.is_relative_to(self.root):
            raise ValueError('document_id must remain inside the knowledge root')
        doc_id = candidate.relative_to(self.root).as_posix()
        with closing(self.connect()) as db:
            self.refresh(db)
            doc = db.execute('SELECT * FROM documents WHERE id=?', (doc_id,)).fetchone()
            if not doc:
                raise FileNotFoundError('Document not present in current inventory')
            scope = self.scope(db)
            if doc['status'] != 'indexed':
                return {'status': 'source-read-failed', 'results': [], 'scope': scope, 'limitations': LIMITATIONS}
            condition = 'c.document_id=?'
            params = [doc_id]
            if section:
                condition += " AND (c.unit=? OR c.unit=? OR instr(lower(COALESCE(json_extract(c.metadata,'$.location.section'),'')),?)>0)"
                params += [section, section.removeprefix(doc_id + '::'), normalize(section)]
            total = db.execute('SELECT count(*) FROM chunks c WHERE ' + condition, params).fetchone()[0]
            selected = db.execute('SELECT c.*,d.metadata AS doc_metadata,d.sha FROM chunks c JOIN documents d ON c.document_id=d.id WHERE ' + condition + ' ORDER BY c.id LIMIT ? OFFSET ?', [*params,limit,offset]).fetchall()
            records = [self.record(r, full=True) for r in selected]
            self.verify_snapshot(db)
            return {'status': 'evidence-found' if records else 'section-not-found', 'results': records,
                    'matched': total, 'returned': len(records), 'truncated': offset + len(records) < total,
                    'next_offset': offset + len(records), 'scope': scope, 'limitations': LIMITATIONS}

    def find_testcases(self, query, model=None, limit=20):
        return self.search_documents(query, limit=limit, document_kind='testcase', model=model)

    def compare_packages(self, query, packages, limit=10):
        if not isinstance(packages, list) or not 2 <= len(packages) <= 10 or any(not isinstance(p, str) or not p.strip() for p in packages):
            raise ValueError('Provide 2 to 10 nonempty package names')
        evidence = {p: self.search_documents(query, limit=limit, document_kind='specification', package=p) for p in packages}
        for group in evidence.values():
            for r in group['results']:
                if k.fingerprint(self.root / r['document_id']) != r['sha256']:
                    raise ValueError('Source changed during package comparison; retry')
        return {'query': query, 'assessment': 'human-review-required', 'packages': evidence,
            'limitations': LIMITATIONS + ['Package-name co-occurrence is a comparison candidate; inspect headers, applicability and UI conditions.']}

    def trace_requirements(self, query, limit=20):
        spec = self.search_documents(query, limit=limit, document_kind='specification')
        tc = self.find_testcases(query, limit=limit)
        ids = sorted({i for s in spec['results'] for i in s['requirement_ids']})
        references = self.find_testcases(' '.join(ids), limit=limit) if ids else None
        if references:
            merged = {r['chunk_id']: r for r in tc['results']}
            merged.update({r['chunk_id']: r for r in references['results']})
            tc = {**tc, 'results': list(merged.values()), 'returned': len(merged),
                  'reference_search': references, 'truncated': tc['truncated'] or references['truncated']}
        links = []
        for s in spec['results']:
            for t in tc['results']:
                shared = sorted(set(normalize(x) for x in s['requirement_ids']) & set(normalize(x) for x in t['requirement_ids']))
                links.append({'requirement_chunk': s['chunk_id'], 'testcase_chunk': t['chunk_id'],
                              'requirement_ids': shared, 'tc_ids': t['tc_ids'],
                              'basis': 'explicit-reference' if shared else 'keyword-candidate',
                              'coverage': 'not-assessed'})
        # Detect a mutation between separate spec/TC searches before returning linked evidence.
        for r in spec['results'] + tc['results']:
            if k.fingerprint(self.root / r['document_id']) != r['sha256']:
                raise ValueError('Source changed during trace analysis; retry')
        return {'query': query, 'specifications': spec, 'testcases': tc, 'links': links,
                'coverage_status': 'semantic-review-required', 'human_review_required': True,
                'limitations': LIMITATIONS + ['An ID reference alone does not prove Trigger/Step/Expected coverage.']}

    def get_status(self):
        with closing(self.connect()) as db:
            self.refresh(db)
            return {'schema': SCHEMA, 'scope': self.scope(db), 'limitations': LIMITATIONS,
                    'latest_rules': k.inventory(self.root)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['status', 'search', 'get', 'tc', 'trace', 'compare'])
    parser.add_argument('--root', type=Path)
    parser.add_argument('--index', type=Path)
    parser.add_argument('--query', default='')
    parser.add_argument('--document-id')
    parser.add_argument('--section')
    parser.add_argument('--model')
    parser.add_argument('--packages', nargs='+')
    parser.add_argument('--limit', type=int, default=20)
    parser.add_argument('--offset', type=int, default=0)
    args = parser.parse_args()
    corpus = Corpus(args.root, args.index)
    actions = {'status': lambda: corpus.get_status(), 'search': lambda: corpus.search_documents(args.query, args.limit),
               'get': lambda: corpus.get_document(args.document_id, args.section, args.offset, args.limit),
               'tc': lambda: corpus.find_testcases(args.query, args.model, args.limit),
               'trace': lambda: corpus.trace_requirements(args.query, args.limit),
               'compare': lambda: corpus.compare_packages(args.query, args.packages, args.limit)}
    try:
        print(json.dumps(actions[args.mode](), ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, sqlite3.Error) as exc:
        import sys
        print(json.dumps({'status': 'adapter-error', 'type': type(exc).__name__, 'message': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
