"""Redmine 이슈 초안 검증 규칙 — 서버 도구와 PC 등록 스크립트가 함께 쓰는 단일 원본.

외부 패키지·패키지 상대 import를 쓰지 않는다. PC 스크립트(`scripts/vxvue_issue_tools/register_redmine_issue.py`)가
이 파일을 경로로 직접 읽어 서버 `validate_redmine_draft`와 같은 규칙·같은 정규화로 전송 payload와 해시를 만든다.
"""
import hashlib
import json
import re

ALLOWED_PROJECTS = (61, 29, 107, 33, 41)
SECTIONS = ('[Issue Title]', '[Precondition]', '[Step]', '[Actual Result]', '[Expected Result]', '[Log, Data]')
SHARE_PREFIX = '\\\\10.1.1.100\\'
SECRET_PATTERNS = (
    (re.compile(r'(?i)(api[_-]?key|x-redmine-api-key|bearer|password|passwd|token)\s*[:=]\s*\S{8,}'), '인증 정보로 보이는 값'),
    (re.compile(r'\b[0-9a-f]{40}\b'), 'API 키 형식(40자리 16진수)'),
    (re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+'), '이메일 주소'),
)


def integer(value, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"정수 범위 {minimum}~{maximum}를 확인하세요.")
    return value


def check_body(description, issue_number_known):
    errors, warnings = [], []
    positions = [description.find(s) for s in SECTIONS]
    missing = [s for s, p in zip(SECTIONS, positions) if p < 0]
    if missing:
        errors.append('본문 섹션 누락: ' + ', '.join(missing))
    else:
        if positions != sorted(positions):
            errors.append('본문 섹션 순서가 맞지 않습니다: ' + ' → '.join(SECTIONS))
    actual = description.find('[Actual Result]')
    expected = description.find('[Expected Result]')
    if actual >= 0 and expected >= 0 and actual > expected:
        errors.append('[Actual Result]가 [Expected Result]보다 앞에 있어야 합니다.')
    # "필요/중요"는 허용하고 "-어요/-해요/-세요" 같은 해요체 어미만 잡는다.
    if re.search(r'(어요|아요|해요|세요|네요|되요|돼요|에요|예요|나요|군요|죠|지요)[.!]?\s*$', description, re.M):
        warnings.append('"-요" 어미가 있습니다. 결과 서술은 "-됨/-함" 형태로 씁니다.')
    if expected >= 0:
        exp_text = description[expected:description.find('[Log, Data]') if '[Log, Data]' in description else None]
        if '할 것 같음' in exp_text and '사양 확인 필요' not in exp_text:
            warnings.append('근거가 불명확한 Expected에는 "사양 확인 필요"를 함께 적습니다.')
    log_at = description.find('[Log, Data]')
    if log_at >= 0:
        log_lines = [l.strip() for l in description[log_at + len('[Log, Data]'):].splitlines() if l.strip()]
        paths = [l for l in log_lines if '10.1.1.100' in l]
        if not paths:
            warnings.append('[Log, Data]에 공용서버 경로가 없습니다.')
        for line in paths:
            if not line.startswith(SHARE_PREFIX):
                errors.append('공용서버 경로는 \\\\10.1.1.100\\ 로 시작해야 합니다(역슬래시 2개).')
            if issue_number_known and not re.search(r'\\#\d{4,6}\s*$', line):
                errors.append('[Log, Data] 경로 끝에 Defect\\#이슈번호가 필요합니다.')
            elif not issue_number_known and not re.search(r'\\#(\d{4,6})?\s*$', line):
                warnings.append('[Log, Data] 경로 끝은 Defect\\# (등록 후 번호 기입) 형식이어야 합니다.')
        if len(log_lines) > len(paths):
            warnings.append('[Log, Data]에는 경로만 적습니다.')
    return errors, warnings


def canonical_json(issue):
    """draft_sha256 정규화: UTF-8, 키 정렬, 공백 없는 구분자, 원래 유니코드 문자."""
    return json.dumps({'issue': issue}, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def draft_sha256(issue):
    return hashlib.sha256(canonical_json(issue).encode('utf-8')).hexdigest()


def validate(subject, description, project_id=61, tracker_id=None, priority_id=None, category_id=None,
             custom_fields=None, issue_number_known=False, allowed_projects=ALLOWED_PROJECTS):
    """Pure validation; no network. Returns a normalized payload and its SHA-256 for approval binding."""
    errors, warnings = [], []
    if not isinstance(subject, str) or not subject.strip():
        errors.append('제목이 비어 있습니다.')
        subject = ''
    if not isinstance(description, str) or not description.strip():
        errors.append('본문이 비어 있습니다.')
        description = ''
    subject = subject.strip()
    if len(subject) > 255:
        errors.append('제목은 255자 이하입니다.')
    if subject and not re.match(r'^\([^)]+\)\s*\[[^\]]+\]\s*\S', subject):
        warnings.append('제목 형식 권장: "(모델) [연동형태] 현상"')
    integer(project_id, 1, 2_147_483_647)
    if project_id not in allowed_projects:
        errors.append('허용 프로젝트는 ' + ', '.join(map(str, allowed_projects)) + '입니다.')
    for label, value in (('tracker_id', tracker_id), ('priority_id', priority_id)):
        if value is None:
            errors.append(f'{label}가 필요합니다(get_redmine_field_map 참고).')
        else:
            integer(value, 1, 2_147_483_647)
    if category_id is not None:
        integer(category_id, 1, 2_147_483_647)
    fields = []
    for field in custom_fields or []:
        if not isinstance(field, dict) or not isinstance(field.get('id'), int) or 'value' not in field:
            errors.append('custom_fields 항목은 {"id": 정수, "value": 값} 형식입니다.')
            continue
        value = field['value']
        if not isinstance(value, (str, int, float, list)):
            errors.append(f'custom_fields {field["id"]} 값 형식이 맞지 않습니다.')
            continue
        fields.append({'id': field['id'], 'value': value})
    if fields and len({f['id'] for f in fields}) != len(fields):
        errors.append('custom_fields에 같은 id가 중복됩니다.')
    if not any(f['id'] == 54 for f in fields):
        warnings.append('발생 버전(cf 54) 버전 ID가 없습니다.')
    body_errors, body_warnings = check_body(description, bool(issue_number_known))
    errors += body_errors
    warnings += body_warnings
    blob = subject + '\n' + description + '\n' + json.dumps(fields, ensure_ascii=False)
    for pattern, label in SECRET_PATTERNS:
        if pattern.search(blob):
            errors.append(f'민감정보로 보이는 값이 있습니다: {label}. 제거 후 다시 검증하세요.')
    issue = {'project_id': project_id, 'tracker_id': tracker_id, 'priority_id': priority_id,
             'subject': subject, 'description': description}
    if category_id is not None:
        issue['category_id'] = category_id
    if fields:
        issue['custom_fields'] = sorted(fields, key=lambda f: f['id'])
    result = {'ok': not errors, 'errors': errors, 'warnings': warnings}
    if not errors:
        result.update(payload={'issue': issue}, draft_sha256=draft_sha256(issue))
    return result
