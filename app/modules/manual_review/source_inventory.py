"""Read-only manual inventory. Quote text is never stripped or normalized."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
W14 = '{http://schemas.microsoft.com/office/word/2010/wordml}'
A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_XML_BYTES = 64 * 1024 * 1024
MAX_PARAGRAPHS = 100000


def _on(node):
    return node is not None and node.get(W + 'val', 'true').lower() not in ('false', '0', 'off')


def _styles(archive):
    result = {}
    if 'word/styles.xml' not in archive.namelist():
        return result
    for node in ET.fromstring(archive.read('word/styles.xml')).iter(W + 'style'):
        name, base = node.find(W + 'name'), node.find(W + 'basedOn')
        result[node.get(W + 'styleId', '')] = {
            'name': name.get(W + 'val', '') if name is not None else '',
            'base': base.get(W + 'val', '') if base is not None else '',
            'strike': node.find(W + 'rPr/' + W + 'strike'),
            'double': node.find(W + 'rPr/' + W + 'dstrike'),
        }
    return result


def _style_flag(styles, name, flag, seen=None):
    seen = set() if seen is None else seen
    if name in seen or name not in styles:
        return False
    seen.add(name)
    item = styles[name]
    inherited = _style_flag(styles, item['base'], flag, seen)
    # Toggle properties in a style turn inherited emphasis on/off. Direct rPr overrides below.
    return not inherited if _on(item[flag]) else inherited


def _heading_level(styles, style):
    seen = set()
    while style and style in styles and style not in seen:
        seen.add(style)
        name = styles[style]['name'] or style
        match = re.fullmatch(r'(?:Heading\s*|제목\s*|VTitle\.)([1-6])', name, re.I)
        if match:
            return int(match[1])
        style = styles[style]['base']
    return 0


def _paragraph(para, styles):
    pstyle = para.find(W + 'pPr/' + W + 'pStyle')
    style = pstyle.get(W + 'val', '') if pstyle is not None else ''
    current, before, raw, inserted, deleted = [], [], [], [], []
    images, events, strike_runs, unsupported = [], [], [], []

    def walk(node, gone=False, added=False, strike=False):
        if node is not para and node.tag == W + 'p':
            return  # Text boxes have their own paragraph; never join two independent paragraphs.
        gone = gone or node.tag in (W + 'del', W + 'moveFrom')
        added = added or node.tag in (W + 'ins', W + 'moveTo')
        if node.tag == W + 'r':
            prop = node.find(W + 'rPr')
            run_style = prop.find(W + 'rStyle') if prop is not None else None
            run_name = run_style.get(W + 'val', '') if run_style is not None else ''
            strike = any(_style_flag(styles, item, flag) for item in (style, run_name) for flag in ('strike', 'double'))
            if prop is not None:
                direct = [prop.find(W + 'strike'), prop.find(W + 'dstrike')]
                present = [n for n in direct if n is not None]
                if present:
                    strike = any(_on(n) for n in present)
        if node.tag in (W + 'commentRangeStart', W + 'commentRangeEnd', W + 'commentReference'):
            events.append((node.tag.split('}')[1], node.get(W + 'id', '')))
        if node.tag == A + 'blip' and node.get(R + 'embed'):
            images.append({'relationship_id': node.get(R + 'embed'), 'deleted': gone, 'inserted': added})
        text = None
        if node.tag in (W + 't', W + 'delText'):
            text = node.text or ''
        elif node.tag == W + 'noBreakHyphen':
            text = '\u2011'
        elif node.tag == W + 'softHyphen':
            text = '\u00ad'
        elif node.tag == W + 'sym' and not gone:
            unsupported.append({'font': node.get(W + 'font', ''), 'char': node.get(W + 'char', '')})
        elif node.tag in (W + 'tab', W + 'br', W + 'cr'):
            # An explicit page break is layout, not a typed newline to search for.
            if node.get(W + 'type') != 'page':
                text = '\t' if node.tag == W + 'tab' else '\n'
        if text is not None:
            raw.append(text)
            if not gone:
                current.append(text)
                if strike and text:
                    strike_runs.append(text)
            if not added:
                before.append(text)
            if gone:
                deleted.append(text)
            if added:
                inserted.append(text)
        for child in node:
            # Historical style/property snapshots are not visible document text.
            if not child.tag.endswith('PrChange'):
                walk(child, gone, added, strike)

    walk(para)
    return {'text': ''.join(current), 'before': ''.join(before), 'raw': ''.join(raw),
            'inserted': ''.join(inserted), 'deleted': ''.join(deleted), 'style': style,
            'has_strike': bool(strike_runs), 'strike_text': strike_runs, 'images': images,
            'para_id': para.get(W14 + 'paraId'), 'unsupported_symbols': unsupported, '_events': events}


def extract_inventory(path: Path) -> dict:
    path = Path(path)
    if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError('원본 파일과 크기(최대 128MB)를 확인하세요.')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if path.suffix.lower() == '.pdf':
        import fitz
        paragraphs = []
        try:
            with fitz.open(path) as pdf:
                if pdf.needs_pass or len(pdf) > 2000:
                    raise ValueError('암호 또는 PDF 페이지 수를 확인하세요.')
                for page_number, page in enumerate(pdf, 1):
                    for block in page.get_text('blocks'):
                        if len(block) > 6 and block[6] != 0:
                            continue
                        text = block[4]
                        if text:
                            paragraphs.append({'number': len(paragraphs) + 1, 'paragraph_index': len(paragraphs),
                                               'text': text, 'before': text, 'raw': text, 'inserted': '', 'deleted': '',
                                               'section': f'PDF {page_number}쪽', 'physical_page': page_number,
                                               'has_strike': False, 'strike_text': [], 'images': [], 'comment_ids': []})
                page_count = len(pdf)
        except Exception as exc:
            raise ValueError('PDF를 읽을 수 없습니다. 원본과 암호 설정을 확인하세요.') from exc
        return {'format_version': 1, 'sha256': digest, 'source_kind': 'pdf', 'paragraphs': paragraphs,
                'comments': [], 'physical_pages': page_count, 'revision_validity': 'visual_review_required',
                'warnings': ['PDF 추출 텍스트는 취소선·변경추적의 현재 유효성을 확정하지 않습니다. 원본 페이지를 확인하세요.']}
    if path.suffix.lower() != '.docx':
        raise ValueError('DOCX 또는 PDF 파일이 필요합니다.')
    try:
        with ZipFile(path) as archive:
            if sum(x.file_size for x in archive.infolist()) > 256 * 1024 * 1024:
                raise ValueError('DOCX 압축 해제 크기가 너무 큽니다.')
            for entry in archive.infolist():
                if entry.filename.endswith('.xml') and entry.file_size > MAX_XML_BYTES:
                    raise ValueError('DOCX XML이 너무 큽니다.')
            root = ET.fromstring(archive.read('word/document.xml'))
            body = root.find(W + 'body')
            if body is None:
                raise ValueError('DOCX 본문이 없습니다.')
            styles = _styles(archive)
            paragraphs, headings, active = [], [], set()
            for number, para in enumerate(body.iter(W + 'p'), 1):
                if number > MAX_PARAGRAPHS:
                    raise ValueError('문단 수가 너무 많습니다.')
                row = _paragraph(para, styles)
                level = _heading_level(styles, row['style'])
                if level and row['text'].strip():
                    headings = [h for h in headings if h[0] < level] + [(level, row['text'])]
                ids = set(active)
                for kind, identifier in row.pop('_events'):
                    ids.add(identifier)
                    if kind == 'commentRangeStart':
                        active.add(identifier)
                    elif kind == 'commentRangeEnd':
                        active.discard(identifier)
                row.update(number=number, paragraph_index=number - 1, section=' / '.join(x[1] for x in headings),
                           comment_ids=sorted(ids), heading_level=level)
                paragraphs.append(row)
            comments = []
            if 'word/comments.xml' in archive.namelist():
                for node in ET.fromstring(archive.read('word/comments.xml')).iter(W + 'comment'):
                    identifier = node.get(W + 'id', '')
                    comments.append({'id': identifier, 'author': node.get(W + 'author', ''),
                                     'text': '\n'.join(_paragraph(p, styles)['text'] for p in node.findall(W + 'p')),
                                     'paragraphs': [p['number'] for p in paragraphs if identifier in p['comment_ids']]})
            extended = []
            if 'word/commentsExtended.xml' in archive.namelist():
                for node in ET.fromstring(archive.read('word/commentsExtended.xml')):
                    extended.append({key.split('}')[-1]: value for key, value in node.attrib.items()})
    except (BadZipFile, KeyError, ET.ParseError) as exc:
        raise ValueError('DOCX 구조를 읽을 수 없습니다.') from exc
    return {'format_version': 1, 'sha256': digest, 'source_kind': 'docx', 'paragraphs': paragraphs,
            'comments': comments, 'comment_threads': extended, 'revision_validity': 'tracked_changes_separated',
            'warnings': ['문단 번호는 실제 쪽수가 아닙니다. DOCX 쪽수는 대응 PDF가 있어야 확인할 수 있습니다.']}
