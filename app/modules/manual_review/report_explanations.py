"""Optional reviewer-authored explanations; never translate or infer defects."""
LANGUAGE_CONTEXTS = {
    'intentional_korean_draft': '번역 전 한국어 초안 (언어만으로 수정 대상 집계하지 않음)',
    'stray_korean_candidate': '영문 사이 한국어 조각 검토 후보 (문맥 확인 필요)',
    'intentional_literal': 'UI·고유명·인용의 의도된 표기',
    'uncertain': '언어 문맥 확인 필요',
    'not_applicable': '언어 검토 해당 없음',
}
EVIDENCE_FIELDS = ('source', 'location', 'quote', 'meaning', 'relation')
EXPLANATION_FIELDS = {'current_meaning', 'short_comment', 'evidence', 'language_context'}


def evidence_text(records):
    return '\n\n'.join(f'{r["source"]} · {r["location"]}\n원문: {r["quote"]}\n한국어 뜻: {r["meaning"]}\n대조 관계: {r["relation"]}' for r in records)


def validate_explanations(finding):
    for field, limit in (('current_meaning', 12000), ('short_comment', 600)):
        if field in finding and (not isinstance(finding[field], str) or not finding[field].strip() or len(finding[field]) > limit):
            raise ValueError(f'{finding["id"]}.{field}: 비어 있지 않은 {limit}자 이하 문장이 필요합니다.')
    context = finding.get('language_context', 'not_applicable')
    if not isinstance(context, str) or context not in LANGUAGE_CONTEXTS:
        raise ValueError(f'{finding["id"]}.language_context: 언어 문맥을 확인하세요.')
    evidence = finding.get('evidence', [])
    if not isinstance(evidence, list) or len(evidence) > 20:
        raise ValueError(f'{finding["id"]}.evidence: 근거는 20건 이하의 목록으로 전달하세요.')
    for item in evidence:
        if not isinstance(item, dict) or set(item) != set(EVIDENCE_FIELDS) or any(
            not isinstance(item[k], str) or not item[k].strip() or len(item[k]) > 12000 for k in EVIDENCE_FIELDS
        ):
            raise ValueError(f'{finding["id"]}.evidence: 출처·위치·원문·한국어 뜻·대조 관계가 필요합니다.')
    if len(evidence_text(evidence)) > 30000:
        raise ValueError(f'{finding["id"]}.evidence: 근거가 너무 깁니다. 항목을 나누세요.')


def explanation_schema():
    return {
        'current_meaning': {'type': 'string', 'minLength': 1, 'maxLength': 12000, 'description': '현재 원문의 한국어 뜻. 원문 인용과 분리.'},
        'short_comment': {'type': 'string', 'minLength': 1, 'maxLength': 600, 'description': '위치에 붙일 한국어 메모 1~3문장. 위치·배경 반복 없이 수정 요청 드립니다 등의 댓글 문체.'},
        'language_context': {'type': 'string', 'enum': list(LANGUAGE_CONTEXTS), 'description': '검토자가 확인한 문맥. 한국어 초안이어도 기능 오류 검토는 유지.'},
        'evidence': {'type': 'array', 'maxItems': 20, 'items': {'type': 'object',
            'properties': {k: {'type': 'string', 'minLength': 1, 'maxLength': 12000} for k in EVIDENCE_FIELDS},
            'required': list(EVIDENCE_FIELDS), 'additionalProperties': False},
            'description': '확인한 공식 근거. source=문서명·VP, location=절·쪽수, quote=원문 그대로, meaning=한국어 뜻, relation=수정 대상과 대조 관계.'},
    }
