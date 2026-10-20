"""MCP surface for immutable review sources and derived report files."""
from .shared_review import ReviewStore, build_report, read_review
from .report_explanations import explanation_schema


def definitions():
    strings = {key: {'type': 'string'} for key in ('id', 'title', 'decision', 'priority', 'proposed', 'basis', 'coverage', 'reason', 'location', 'note')}
    strings['decision']['enum'] = ['수정 필수', '추가 필요', '보강 권장', '사양 확인 필요', '유지', '타 문서 위임 적절']
    strings['priority']['enum'] = ['높음', '보통', '낮음']
    numbers = {'type': 'array', 'maxItems': 40, 'items': {'type': 'integer', 'minimum': 1}}
    item = {'type': 'object', 'properties': {**strings, **explanation_schema(), 'paragraphs': numbers, 'quote_paragraphs': numbers,
            'kind': {'type': 'string', 'enum': ['manual', 'proposal', 'source']}},
            'required': ['id', 'title', 'decision', 'priority', 'paragraphs', 'proposed', 'basis', 'coverage', 'reason'], 'additionalProperties': False}
    return [
        {'name': 'get_manual_review', 'description': '공통 매뉴얼 검토: 등록 문서(document_id+snapshot_id) 또는 업로드 자료(source_id)의 전체 본문·변경추적·취소선·메모·원문 이미지 조회. summary에서 source_id를 받고 paragraphs/comments를 next_offset 끝까지 읽기. 현재 text는 원문 그대로이며 before/deleted와 혼합하지 않기.',
         'inputSchema': {'type': 'object', 'properties': {'source_id': {'type': 'string'}, 'document_id': {'type': 'string'}, 'snapshot_id': {'type': 'string'},
             'view': {'type': 'string', 'enum': ['summary', 'paragraphs', 'comments', 'page', 'images']}, 'offset': {'type': 'integer', 'minimum': 0},
             'limit': {'type': 'integer', 'minimum': 1, 'maximum': 40}, 'paragraphs': numbers,
             'page': {'type': 'integer', 'minimum': 1}, 'basis': {'type': 'string', 'enum': ['source', 'saved', 'final']}}, 'additionalProperties': False}},
        {'name': 'make_manual_review_report', 'description': '요청한 검토보고서 HTML·Excel 생성. 현재 문구는 quote_paragraphs(생략 시 paragraphs) 원문에서 서버가 채우며 상단에 펼쳐 표시. current_meaning에 한국어 뜻, evidence에 확인한 공식 근거의 위치·원문·한국어 해설, reason에 AI 대조 판단, proposed에 구체적 한국어 수정 요청을 작성. short_comment는 위치에 붙일 짧은 한국어 메모이며 이 값만 복사. 한국어 초안과 뜬금없는 한국어 조각을 language_context로 구분하되 기능 오류 검토는 유지. 문체·판정은 최신 통합 QA MD 35절 적용. 생성 문장의 띄어쓰기 오류와 한글에 붙은 숫자(최대200개)는 교정 후 재생성. 근거 문서 자체 오류(kind=source)는 paragraphs·quote_paragraphs를 비우고 location에 그 문서의 이름과 위치를 씀. 원본 수정 없음. 원문 순서·기존 ID·실제 쪽수와 문단 구분 유지. 선택 필드 없는 기존 호출도 지원.',
         'inputSchema': {'type': 'object', 'properties': {'source_id': {'type': 'string'}, 'title': {'type': 'string'},
             'memo_review': {'type': 'array', 'maxItems': 1000, 'items': {'type': 'object'}},
             'release_review': {'type': 'array', 'maxItems': 1000, 'items': {'type': 'object'}},
             'findings': {'type': 'array', 'minItems': 1, 'maxItems': 200, 'items': item}}, 'required': ['source_id', 'findings'], 'additionalProperties': False}},
    ]


def call(name, arguments, root, snapshot_provider):
    store, args = ReviewStore(root), dict(arguments)
    if name == 'make_manual_review_report':
        result = build_report(store, **args)
        result.pop('directory', None)
        result['files'] = [{'name': filename, 'url': f'/vxvue-desktop/manual-review/files/{result["report_id"]}/{filename}'} for filename in result['files']]
        return result
    source_id, document_id = args.pop('source_id', None), args.pop('document_id', None)
    snapshot_id = args.pop('snapshot_id', None)
    if bool(source_id) == bool(document_id):
        raise ValueError('업로드 자료 ID 또는 등록 문서 ID 중 하나를 지정하세요.')
    if document_id:
        if not isinstance(snapshot_id, str):
            raise ValueError('등록 문서에는 get_status가 반환한 snapshot_id가 필요합니다.')
        from app.modules.vxvue_chatbot.mcp_server import KnowledgeTools
        current = snapshot_provider(snapshot_id)
        session = KnowledgeTools(current, [], store.base / 'unused')
        path = session._source(document_id)
        source_id = store.add(path.read_bytes(), document_id)['source_id']
    view = args.get('view', 'summary')
    if view == 'page':
        from app.modules.vxvue_chatbot.mcp_server import KnowledgeTools
        path = store.pdf_path(source_id, args.get('basis', 'source'))
        return KnowledgeTools._image(None, path, args.get('page', 1))
    if view == 'images':
        return _images(store, source_id, args.get('paragraphs'))
    args.pop('page', None)
    args.pop('basis', None)
    return read_review(store, source_id, **args)


def _images(store, source_id, paragraphs):
    import base64
    import json
    from xml.etree import ElementTree as ET
    from zipfile import ZipFile
    data = store.inventory(source_id)
    if not isinstance(paragraphs, list) or not 1 <= len(paragraphs) <= 40 or any(type(n) is not int or not 1 <= n <= len(data['paragraphs']) for n in paragraphs):
        raise ValueError('그림의 원본 문단 번호를 지정하세요.')
    if data['source_kind'] != 'docx':
        raise ValueError('PDF는 page 조회로 확인하세요.')
    blocks, count, omitted = [], 0, 0
    with ZipFile(store.source_path(source_id)) as archive:
        if 'word/_rels/document.xml.rels' not in archive.namelist():
            raise ValueError('DOCX 그림 참조가 없습니다.')
        relations = {x.get('Id'): x.get('Target', '') for x in ET.fromstring(archive.read('word/_rels/document.xml.rels'))}
        for number in paragraphs:
            for image in data['paragraphs'][number - 1]['images']:
                target = relations.get(image['relationship_id'], '')
                suffix = target.rsplit('.', 1)[-1].lower()
                if not target.startswith('media/') or '..' in target or suffix not in ('png', 'jpg', 'jpeg', 'webp') or count >= 6:
                    omitted += 1
                    continue
                content = archive.read('word/' + target)
                if len(content) > 8 * 1024 * 1024:
                    omitted += 1
                    continue
                blocks.extend([{'type': 'text', 'text': json.dumps({'paragraph': number, 'deleted': image['deleted'], 'visual_status': 'rendered-not-reviewed'})},
                               {'type': 'image', 'mimeType': 'image/' + ('jpeg' if suffix == 'jpg' else suffix), 'data': base64.b64encode(content).decode()}])
                count += 1
    blocks.insert(0, {'type': 'text', 'text': json.dumps({'images': count, 'omitted': omitted, 'instruction': '그림 제공은 내용 검증 완료가 아닙니다. 생략한 그림은 원본 또는 대응 PDF로 확인하세요.'}, ensure_ascii=False)})
    return blocks
