"""Bounded, offline, read-only extraction run in a disposable child process.

This module intentionally has no application imports: the worker never opens a
database, starts the web application, or invokes a document's macros/links.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_XML_BYTES = 8 * 1024 * 1024
MAX_ZIP_BYTES = 32 * 1024 * 1024
MAX_TEXT_CHARS = 1_000_000
MAX_PDF_PAGES = 500
TIMEOUT_SECONDS = 8
INDEX_VERSION = 2


def result(status, text='', detail=None):
    return {'status': status, 'text': text, 'detail': detail}


def snippet(text, query):
    if not text or not query:
        return None
    position = text.casefold().find(query.strip().casefold())
    if position < 0:
        return None
    start = max(0, position - 90)
    end = min(len(text), position + len(query) + 150)
    return ('…' if start else '') + text[start:end] + ('…' if end < len(text) else '')


def _extract(path, extension):
    """Child-only parser; all input and output sizes are bounded."""
    parts = []
    size = 0
    truncated = False

    def add(text):
        nonlocal size, truncated
        text = text.replace('\x00', '').strip()
        if not text:
            return True
        remaining = MAX_TEXT_CHARS - size
        if remaining <= 0:
            truncated = True
            return False
        chunk = text[:remaining]
        parts.append(chunk)
        size += len(chunk) + 1
        if len(text) > remaining:
            truncated = True
            return False
        return True

    if extension in ('.txt', '.md'):
        raw = path.read_bytes()
        # BOMless CP949 with an even byte count is also syntactically valid
        # UTF-16. Only a BOM authorizes UTF-16; do not guess from byte length.
        encodings = ('utf-16',) if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else ('utf-8-sig', 'cp949')
        if b'\x00' in raw and encodings != ('utf-16',):
            return result('failed', detail='UTF-16 텍스트에는 BOM이 필요합니다.')
        for encoding in encodings:
            try:
                text = raw.decode(encoding)
                break
            except UnicodeError:
                continue
        else:
            return result('failed', detail='텍스트 문자 인코딩을 읽을 수 없습니다.')
        add(text)
    elif extension == '.pdf':
        import fitz
        with fitz.open(path) as document:
            if document.needs_pass:
                return result('encrypted', detail='암호가 필요한 PDF입니다.')
            for index in range(min(len(document), MAX_PDF_PAGES)):
                if not add(document[index].get_text('text')):
                    break
            if len(document) > MAX_PDF_PAGES:
                truncated = True
    elif extension in ('.docx', '.xlsx'):
        with path.open('rb') as handle:
            if handle.read(8) == bytes.fromhex('d0cf11e0a1b11ae1'):
                return result('encrypted', detail='암호화된 Office 파일 또는 지원하지 않는 구형 Office 형식입니다.')
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > 512 or sum(i.file_size for i in infos) > MAX_ZIP_BYTES:
                return result('too_large', detail='Office 압축 해제 제한(32MiB·512항목)을 넘었습니다.')
            parsed = {}
            for info in infos:
                if info.flag_bits & 1:
                    return result('encrypted', detail='암호화된 Office 압축 항목입니다.')
                name = info.filename
                wanted = (extension == '.docx' and name.startswith('word/') and name.endswith('.xml') and any(x in name for x in ('document.xml', 'header', 'footer', 'footnotes', 'endnotes')))
                wanted |= extension == '.xlsx' and (name == 'xl/sharedStrings.xml' or (name.startswith('xl/worksheets/') and name.endswith('.xml')))
                if not wanted:
                    continue
                if info.file_size > MAX_XML_BYTES:
                    return result('too_large', detail='Office XML 항목 제한(8MiB)을 넘었습니다.')
                with archive.open(info) as source:
                    raw = source.read(MAX_XML_BYTES + 1)
                if len(raw) > MAX_XML_BYTES:
                    return result('too_large', detail='Office XML 항목 제한을 넘었습니다.')
                declarations = raw.replace(b'\x00', b'').upper()
                if b'<!DOCTYPE' in declarations or b'<!ENTITY' in declarations:
                    return result('failed', detail='외부 선언 또는 사용자 엔터티가 있는 XML은 읽지 않습니다.')
                parsed[name] = ET.fromstring(raw)

            def local(element):
                return element.tag.rsplit('}', 1)[-1]

            def rich_text(element):
                return ''.join(e.text or '' for e in element.iter() if local(e) == 't')

            if extension == '.docx':
                for root in parsed.values():
                    for paragraph in root.iter():
                        if local(paragraph) != 'p':
                            continue
                        # Formatting splits words/phrases into runs. Preserve
                        # run text and xml:space, then separate paragraphs only.
                        text = ''.join((e.text or '') if local(e) == 't' else '\t' if local(e) == 'tab' else '\n' if local(e) in ('br','cr') else '' for e in paragraph.iter())
                        if not add(text):
                            break
                    if truncated:
                        break
            else:
                shared_root = parsed.get('xl/sharedStrings.xml')
                shared = [rich_text(e) for e in shared_root if local(e) == 'si'] if shared_root is not None else []
                for name, root in parsed.items():
                    if not name.startswith('xl/worksheets/'):
                        continue
                    for row in root.iter():
                        if local(row) != 'row':
                            continue
                        cells = []
                        for cell in row:
                            if local(cell) != 'c':
                                continue
                            kind = cell.get('t', '')
                            value = next((e.text or '' for e in cell if local(e) == 'v'), '')
                            if kind == 's':
                                # Ordinals and unreferenced shared strings are
                                # implementation details, not displayed content.
                                index = int(value)
                                if not 0 <= index < len(shared):
                                    raise ValueError('invalid shared string index')
                                value = shared[index]
                            elif kind == 'inlineStr':
                                value = rich_text(cell)
                            elif kind == 'b':
                                value = 'TRUE' if value == '1' else 'FALSE' if value == '0' else value
                            if value:
                                cells.append(value)
                        if not add('\t'.join(cells)):
                            break
                    if truncated:
                        break
    text = '\n'.join(parts)[:MAX_TEXT_CHARS]
    if not text.strip():
        detail = '추출 가능한 본문이 없습니다. 이미지 PDF에는 OCR을 수행하지 않습니다.'
        if truncated:
            detail = '검사한 처음 500쪽에 추출 가능한 본문이 없습니다. 이후 쪽은 검사하지 않았으며 OCR도 수행하지 않습니다.'
        return result('no_text', detail=detail)
    return result('truncated' if truncated else 'ready', text,
                  '본문 100만 자 또는 PDF 500쪽까지만 색인했습니다.' if truncated else None)


def extract_file(path, extension, expected_hash=None):
    """Validate immutable file identity, then isolate parser with wall-time cap."""
    path = Path(path)
    extension = '.' + extension.lower().lstrip('.')
    if extension not in ('.pdf', '.docx', '.xlsx', '.txt', '.md'):
        return result('unsupported', detail='PDF·DOCX·XLSX·TXT·MD만 본문을 추출합니다.')
    if not path.is_file():
        return result('missing', detail='저장된 원본 파일을 찾을 수 없습니다.')
    if path.stat().st_size > MAX_FILE_BYTES:
        return result('too_large', detail='본문 추출 파일 제한(50MiB)을 넘었습니다.')
    if expected_hash:
        digest = hashlib.sha256()
        with path.open('rb') as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(chunk)
        if digest.hexdigest() != expected_hash:
            return result('hash_mismatch', detail='저장된 해시와 원본 파일 내용이 다릅니다.')
    try:
        process = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--worker', str(path.resolve()), extension],
                                 capture_output=True, timeout=TIMEOUT_SECONDS,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if process.returncode or len(process.stdout) > MAX_TEXT_CHARS * 6 + 4096:
            return result('failed', detail='분리된 본문 추출 프로세스가 실패했습니다.')
        value = json.loads(process.stdout.decode('utf-8'))
        return value
    except subprocess.TimeoutExpired:
        return result('timeout', detail='본문 추출 시간 제한(8초)을 넘었습니다.')
    except (OSError, ValueError, UnicodeError):
        return result('failed', detail='본문 추출 결과를 읽을 수 없습니다.')


if __name__ == '__main__':
    # Linux deployment additionally caps address space and CPU. On Windows the
    # disposable worker still has the same byte/page/text and wall-time caps.
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (6, 6))
    except (ImportError, ValueError, OSError):
        pass
    try:
        value = _extract(Path(sys.argv[2]), sys.argv[3])
    except Exception:
        value = result('failed', detail='파일 형식 또는 본문 파싱 오류입니다.')
    sys.stdout.buffer.write(json.dumps(value, ensure_ascii=False).encode('utf-8'))
