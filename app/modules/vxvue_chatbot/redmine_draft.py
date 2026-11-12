"""Read-only helpers for preparing a Redmine issue draft on the user's PC.

The server never writes to Redmine. These tools only read field metadata (GET) or validate
a draft locally, so the PC can register an issue with the user's own API key after approval.
"""
from datetime import datetime, timezone
import hashlib
import json
import re

from .desktop_polarion import integer
from .redmine import RedmineLookup

DRAFT_TOOLS = ('get_redmine_field_map', 'validate_redmine_draft')
SECTIONS = ('[Issue Title]', '[Precondition]', '[Step]', '[Actual Result]', '[Expected Result]', '[Log, Data]')
SHARE_PREFIX = '\\\\10.1.1.100\\'
SECRET_PATTERNS = (
    (re.compile(r'(?i)(api[_-]?key|x-redmine-api-key|bearer|password|passwd|token)\s*[:=]\s*\S{8,}'), '인증 정보로 보이는 값'),
    (re.compile(r'\b[0-9a-f]{40}\b'), 'API 키 형식(40자리 16진수)'),
    (re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+'), '이메일 주소'),
)
WRITE_RULES = """[Redmine 등록(쓰기) 규칙]
1. 이 서버 연결은 Redmine에 쓰지 않는다. 등록·수정은 사용자 PC에서 사용자 본인 API 키(사용자 환경변수 REDMINE_API_KEY)로만 한다. 키가 없으면 등록 기능을 안내만 한다.
2. 등록·수정·삭제는 사용자가 "해당 초안"을 명시적으로 승인한 뒤에만 한다. 분석 요청, 초안 요청, "이슈인지 확인해줘", 질문형 문장, 이전 초안 승인은 승인이 아니다. 승인은 초안 1건에만 유효하고 초안이 바뀌면 다시 승인받는다.
3. 승인 전에는 초안과 Redmine 입력 항목표만 제시한다. 등록 전 get_redmine_field_map으로 필드 ID·버전 ID를 확인하고 validate_redmine_draft로 검증하며 search_redmine_issues로 중복을 확인한다.
4. 등록 후: 이슈 번호 확인 → 공용서버 Defect\\#번호 폴더 생성·파일 수/용량 일치 검증 → 본문 [Log, Data] 경로 수정 → Redmine 재조회로 저장값 확인 → 결과 보고.
5. API 키·토큰은 화면·대화·로그·파일에 출력하거나 저장하지 않는다. 기존 이슈 상태 변경·댓글·다른 사람 이슈 수정은 별도 승인 없이는 하지 않는다."""


class FieldMapLookup(RedmineLookup):
    """GET-only field metadata for one allowed project."""

    def field_map(self, project_id=61, sample=25):
        integer(project_id, 1, 2_147_483_647)
        integer(sample, 1, 100)
        if project_id not in self.ALLOWED:
            raise ValueError('허용 프로젝트는 ' + ', '.join(map(str, self.ALLOWED)) + '입니다.')
        import time
        self.deadline = time.monotonic() + 55
        project = self._get(f'/projects/{project_id}.json', {'include': 'trackers,issue_categories'}).get('project') or {}
        result = {
            'project': {'id': project.get('id'), 'name': project.get('name')},
            'trackers': [{'id': t.get('id'), 'name': t.get('name')} for t in project.get('trackers') or []],
            'categories': [{'id': c.get('id'), 'name': c.get('name')} for c in project.get('issue_categories') or []],
            'priorities': [], 'versions': [], 'custom_fields': [], 'errors': [],
        }
        try:
            rows = self._get('/enumerations/issue_priorities.json').get('issue_priorities') or []
            result['priorities'] = [{'id': p.get('id'), 'name': p.get('name'), 'is_default': p.get('is_default', False)} for p in rows]
        except ValueError as exc:
            result['errors'].append({'part': 'priorities', 'error': str(exc)})
        try:
            rows = self._get(f'/projects/{project_id}/versions.json').get('versions') or []
            result['versions'] = [{'id': v.get('id'), 'name': v.get('name'), 'status': v.get('status')} for v in rows]
        except ValueError as exc:
            result['errors'].append({'part': 'versions', 'error': str(exc)})
        # Custom field definitions need admin rights; observed values from recent public issues are used instead.
        try:
            rows = self._get('/issues.json', {'project_id': project_id, 'subproject_id': '!*', 'status_id': '*',
                                              'sort': 'id:desc', 'limit': sample}).get('issues') or []
            seen = {}
            for issue in rows:
                if not isinstance(issue, dict) or issue.get('is_private') is True:
                    continue
                for field in issue.get('custom_fields') or []:
                    if not isinstance(field, dict) or 'id' not in field:
                        continue
                    entry = seen.setdefault(field['id'], {'id': field['id'], 'name': field.get('name'), 'observed_values': []})
                    value = field.get('value')
                    values = value if isinstance(value, list) else [value]
                    for item in values:
                        text = str(item).strip() if item is not None else ''
                        if text and len(text) <= 80 and '\n' not in text and text not in entry['observed_values'] and len(entry['observed_values']) < 15:
                            entry['observed_values'].append(text)
            result['custom_fields'] = sorted(seen.values(), key=lambda f: f['id'])
        except ValueError as exc:
            result['errors'].append({'part': 'custom_fields', 'error': str(exc)})
        result.update(collected_at=datetime.now(timezone.utc).isoformat(),
                      scope=f'GET 전용. 사용자 정의 항목은 최근 공개 이슈 {sample}건에서 관찰한 값이며 전체 허용값 목록이 아님.',
                      rules=WRITE_RULES)
        return result


def _check_body(description, issue_number_known):
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


def validate(subject, description, project_id=61, tracker_id=None, priority_id=None, category_id=None,
             custom_fields=None, issue_number_known=False):
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
    if project_id not in RedmineLookup.ALLOWED:
        errors.append('허용 프로젝트는 ' + ', '.join(map(str, RedmineLookup.ALLOWED)) + '입니다.')
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
    body_errors, body_warnings = _check_body(description, bool(issue_number_known))
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
    canonical = json.dumps({'issue': issue}, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    result = {'ok': not errors, 'errors': errors, 'warnings': warnings,
              'note': '서버는 검증만 하며 Redmine에 쓰지 않습니다. 등록은 사용자 승인 후 PC에서 사용자 키로 수행합니다.',
              'rules': WRITE_RULES}
    if not errors:
        result.update(payload={'issue': issue},
                      draft_sha256=hashlib.sha256(canonical.encode('utf-8')).hexdigest())
    return result


def call(name, arguments):
    if name == 'get_redmine_field_map':
        from app.core.config import get_settings
        return FieldMapLookup(get_settings()).field_map(**arguments)
    if name == 'validate_redmine_draft':
        from app.core.security_filter import mask_payload
        result = validate(**arguments)
        if result['ok']:
            protected, _ = mask_payload(result['payload'])
            if protected != result['payload']:
                result.update(ok=False, errors=[
                    '공용 연결의 민감정보 보호 처리가 전송 내용을 바꾸므로 payload와 해시를 반환하지 않습니다. 원문은 사용자 PC에서 관리하세요.'])
                result.pop('payload')
                result.pop('draft_sha256')
        return result
    raise ValueError('허용되지 않은 도구입니다.')


def definitions():
    positive = {'type': 'integer', 'minimum': 1}
    specs = [
        ('get_redmine_field_map',
         'VXvue Redmine 등록 준비용 읽기 도구. 프로젝트의 트래커·범주·우선순위·버전 ID와 최근 공개 이슈에서 관찰한 사용자 정의 항목 ID·값을 GET으로만 조회한다. Redmine 등록 규칙 포함. 서버는 Redmine에 쓰지 않는다.',
         {'project_id': {'type': 'integer', 'enum': list(RedmineLookup.ALLOWED)}, 'sample': {'type': 'integer', 'minimum': 1, 'maximum': 100}}, []),
        ('validate_redmine_draft',
         'VXvue Redmine 이슈 초안 검증(네트워크 없음). 제목 형식, 본문 섹션 순서(Actual이 Expected 앞), 공용서버 경로(\\\\10.1.1.100\\...\\Defect\\#번호), 필수 ID, 민감정보를 확인하고 정규화한 전송 JSON과 draft_sha256을 돌려준다. 등록은 사용자 승인 후 PC에서 사용자 키로만 한다.',
         {'subject': {'type': 'string'}, 'description': {'type': 'string'}, 'project_id': positive, 'tracker_id': positive,
          'priority_id': positive, 'category_id': positive,
          'custom_fields': {'type': 'array', 'items': {'type': 'object', 'properties': {'id': positive, 'value': {}}, 'required': ['id', 'value']}},
          'issue_number_known': {'type': 'boolean'}},
         ['subject', 'description']),
    ]
    return [{'name': n, 'description': d,
             'inputSchema': {'type': 'object', 'properties': p, 'required': r, 'additionalProperties': False},
             'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'openWorldHint': False}}
            for n, d, p, r in specs]
