"""Detect compact Korean prose without rewriting quotations or identifiers."""
import re
import unicodedata

MIN_KOREAN_RUN = 12
GENERATED_FIELDS = ('title', 'proposed', 'basis', 'coverage', 'reason', 'note')
_RUN = re.compile(r'[가-힣]{' + str(MIN_KOREAN_RUN) + r',}')
_CODE = re.compile(r'(`+).*?\1', re.S)
_URL = re.compile(r'https?://[^\s<>]+', re.I)
_FILE_PATH = re.compile(
    r'''(?<!\w)(?:[A-Za-z]:[\\/]|\\\\|\.{1,2}[\\/]|/|[\w.-]+[\\/])'''
    r'''(?:[^\\/\r\n<>:"|?*]+?[\\/])*[^\\/\r\n<>:"|?*]+?\.[A-Za-z0-9]{1,10}'''
    r'''(?=$|[\s),;\]}>"'])''')
_PATH = re.compile(r'''(?<!\w)(?:[A-Za-z]:[\\/]|\\\\|\.{1,2}[\\/]|/)[^\s<>"']+''')


def spacing_candidates(text):
    """Heuristic candidates only; never return edited text or infer word breaks."""
    if not isinstance(text, str):
        return []
    inspected = unicodedata.normalize('NFC', text)
    for pattern in (_CODE, _URL, _FILE_PATH, _PATH):
        inspected = pattern.sub(' ', inspected)
    return [match.group() for match in _RUN.finditer(inspected)]


def validate_report_prose(title, findings, memo_review, release_review, inventory):
    source_values = {row['text'] for row in inventory['paragraphs']}
    source_values.update(row['text'] for row in inventory['comments'])
    issues = []

    def check(label, value):
        # Quoted source values are exempt only when actually present in this source.
        if isinstance(value, str) and value not in source_values and spacing_candidates(value):
            issues.append(label)

    check('report.title', title)
    for finding in findings:
        for field in GENERATED_FIELDS:
            check(f'{finding["id"]}.{field}', finding.get(field))
    for name, records in (('memo_review', memo_review), ('release_review', release_review)):
        for number, record in enumerate(records or [], 1):
            for field, value in record.items():
                check(f'{name}[{number}].{field}', value)
    if issues:
        labels = ', '.join(issues[:20]) + (' 등' if len(issues) > 20 else '')
        raise ValueError(f'분석 문장 띄어쓰기 확인 필요: {labels}. '
                         f'한글 {MIN_KOREAN_RUN}자 이상이 공백 없이 이어지는 표현을 검토하고 다시 생성하세요. '
                         '현재 문구는 원문 그대로 유지하며 제품명·UI·코드·경로를 임의로 교정하지 마세요. '
                         '이 검사는 일반 맞춤법 전체를 판정하지 않습니다.')
