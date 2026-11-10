"""Shared QA section selection. Source rules remain complete and product scoped."""
from collections import Counter
import re

SECTION_IDS = ('core', 'spec', 'tc', 'issue', 'doc', 'verify', 'release', 'mail',
               'integ', 'auto', 'usage', 'meta', 'revlog')
SECTION_SCHEMA = {'type': 'string', 'enum': ['full', *SECTION_IDS]}
_OPEN = re.compile(r'^<!--\s*qa-section:\s*id=([a-z]+)(.*?)-->\s*$')
_CLOSE = re.compile(r'^<!--\s*/qa-section:\s*id=([a-z]+)\s*-->\s*$')


def parse_sections(text):
    """Allow repeated disjoint ranges of an ID (operations and detail rules)."""
    chunks, metadata, outside = [], {}, []
    active, lines, fence = None, [], None
    for line in text.splitlines(keepends=True):
        mark = re.match(r'^\s*(`{3,}|~{3,})', line)
        if mark:
            token = mark[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
        opening = _OPEN.match(line.strip()) if fence is None else None
        closing = _CLOSE.match(line.strip()) if fence is None else None
        if opening:
            sid, attrs = opening.groups()
            if active or sid not in SECTION_IDS:
                raise ValueError('QA 섹션 시작·ID를 확인하세요.')
            remainder = re.sub(r';\s*title="[^"]*"', '', attrs)
            remainder = re.sub(r';\s*requires=[a-z]+(?:,[a-z]+)*\s*(?=;|$)', '', remainder)
            if remainder.strip() or attrs.count('title=') > 1 or attrs.count('requires=') > 1:
                raise ValueError('QA 섹션 title·requires 형식을 확인하세요.')
            title = re.search(r'title="([^"]*)"', attrs)
            needs = re.search(r'requires=([a-z,]+)', attrs)
            meta = {'id': sid, 'title': title[1] if title else sid,
                    'requires': needs[1].split(',') if needs else []}
            if sid in metadata and metadata[sid] != meta:
                raise ValueError('같은 QA 섹션의 제목·선행 규칙이 다릅니다.')
            metadata[sid] = meta
            active, lines = sid, [line]
        elif closing:
            if active != closing[1]:
                raise ValueError('QA 섹션 표지 짝이 맞지 않습니다.')
            chunks.append((active, ''.join([*lines, line])))
            active, lines = None, []
        elif active:
            lines.append(line)
        else:
            outside.append(line)
    if active:
        raise ValueError('닫히지 않은 QA 섹션이 있습니다.')
    if not chunks and 'qa-section:' in text:
        raise ValueError('QA 섹션 표지 형식을 확인하세요.')
    visiting, done = set(), set()

    def visit(sid):
        if sid not in metadata or sid in visiting:
            raise ValueError('QA 선행 섹션 누락 또는 순환 참조입니다.')
        if sid in done:
            return
        visiting.add(sid)
        for dependency in metadata[sid]['requires']:
            visit(dependency)
        visiting.remove(sid)
        done.add(sid)
    for sid in metadata:
        visit(sid)
    return chunks, metadata, ''.join(outside)


def select_sections(text, section=None):
    if section is not None and (not isinstance(section, str) or section not in ('full', *SECTION_IDS)):
        raise ValueError('지원되는 QA section을 지정하세요.')
    chunks, metadata, outside = parse_sections(text)
    catalog = [{**m, 'chars': sum(len(body) for sid, body in chunks if sid == m['id'])}
               for m in metadata.values()]
    meta = {'section': section or 'full', 'sections': catalog, 'legacy_fallback': not bool(chunks)}
    if section in (None, 'full') or not chunks:
        return text, {**meta, 'selected_sections': list(metadata)}
    if 'core' not in metadata or section not in metadata:
        raise ValueError('요청한 QA 섹션이 없습니다. 목차를 확인하세요.')
    selected = {'core', section}

    def include(sid):
        for dependency in metadata[sid]['requires']:
            if dependency not in selected:
                selected.add(dependency)
                include(dependency)
    for sid in list(selected):
        include(sid)
    ids = [sid for sid in metadata if sid in selected]
    index = '\n## 작업별 QA 섹션 목차\n' + '\n'.join(
        f'- {m["id"]}: {m["title"]} ({m["chars"]}자; 선행: {",".join(m["requires"]) or "없음"})'
        for m in catalog)
    # Unmarked header and cross-section precedence rules are always included.
    return outside + index + '\n\n' + ''.join(body for sid, body in chunks if sid in selected), {
        **meta, 'selected_sections': ids}


def preservation(before, after):
    """Count exact nonblank source lines; moving lines is allowed, deletion is not."""
    def content(text):
        return Counter(line for line in text.replace('\r\r\n', '\n').replace('\r\n', '\n').split('\n')
                       if line.strip() and not _OPEN.match(line.strip()) and not _CLOSE.match(line.strip()))
    a, b = content(before), content(after)
    return {'missing': list((a - b).elements()), 'added': list((b - a).elements())}


def validate_guide(text, product, baseline=None):
    errors, warnings = [], []
    try:
        _, meta = select_sections(text, 'core')
    except ValueError as exc:
        return {'errors': [str(exc)], 'warnings': [], 'sections': []}
    for section in meta['sections']:
        cap = (13000 if 'bellalun' in product.casefold() else 25000) if section['id'] == 'core' else 20000
        if section['chars'] > cap:
            warnings.append(f"{section['id']}: {section['chars']}자 > 권고 {cap}자. 내용 보존 후 분할 검토.")
    if baseline is not None:
        missing = preservation(baseline, text)['missing']
        if missing:
            errors.append(f'직전 판의 기존 규칙 {len(missing)}행이 누락되었습니다. 의도한 변경은 원문 근거와 함께 별도 검토하세요.')
        headings = lambda value: {title.rstrip('\r') for title in re.findall(r'^#{1,6}\s+(.+)$', value, re.M)}
        for title in headings(text) - headings(baseline):
            if re.search(r'Rev\.\d|강화 사항|추가 항목', title):
                errors.append('새 개정별 덧붙임 제목: ' + title)
    return {'errors': errors, 'warnings': warnings, 'sections': meta['sections']}


def compact_evidence(value):
    """Hoist only document-level metadata. Never change source text or cell detail."""
    if not isinstance(value, dict) or not isinstance(value.get('results'), list):
        return value
    keys = {'version_mentions', 'chunk_count', 'document_revision', 'document_snapshot',
            'product_version', 'document_kind', 'warnings', 'status'}
    documents, results = {}, []
    for row in value['results']:
        if not isinstance(row, dict) or 'document_id' not in row:
            results.append(row)
            continue
        doc_id = row['document_id']
        fields = {k: v for k, v in row.items() if k in keys}
        if doc_id in documents and documents[doc_id] != {'document_id': doc_id, **fields}:
            # Different chunk metadata must never be merged as if it were document metadata.
            return value
        documents[doc_id] = {'document_id': doc_id, **fields}
        results.append({k: v for k, v in row.items() if k not in fields})
    return {**value, 'results': results, 'documents': list(documents.values()), 'compact': True}


def compact_inventory(value):
    if not isinstance(value, dict) or not isinstance(value.get('scope', {}).get('documents'), list):
        return value
    documents = []
    for row in value['scope']['documents']:
        if isinstance(row.get('version_mentions'), list) and row['version_mentions']:
            row = {**row, 'version_mentions_count': len(row['version_mentions'])}
            row.pop('version_mentions')
        documents.append(row)
    return {**value, 'scope': {**value['scope'], 'documents': documents},
            'metadata_view': 'get_status(compact=false)'}
