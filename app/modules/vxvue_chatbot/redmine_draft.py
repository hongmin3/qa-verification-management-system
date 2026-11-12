"""Read-only helpers for preparing a Redmine issue draft on the user's PC.

The server never writes to Redmine. These tools only read field metadata (GET) or validate
a draft locally, so the PC can register an issue with the user's own API key after approval.
"""
from datetime import datetime, timezone

from .redmine import RedmineLookup
from .redmine_draft_rules import SECRET_PATTERNS, SECTIONS, SHARE_PREFIX, integer  # noqa: F401  (재노출)
from .redmine_draft_rules import validate as _validate_rules

DRAFT_TOOLS = ('get_redmine_field_map', 'validate_redmine_draft')
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


def validate(subject, description, project_id=61, tracker_id=None, priority_id=None, category_id=None,
             custom_fields=None, issue_number_known=False):
    """Pure validation; no network. 규칙 원본은 redmine_draft_rules.py (PC 등록 스크립트와 공유)."""
    result = _validate_rules(subject, description, project_id, tracker_id, priority_id, category_id,
                             custom_fields, issue_number_known, allowed_projects=RedmineLookup.ALLOWED)
    result.update(note='서버는 검증만 하며 Redmine에 쓰지 않습니다. 등록은 사용자 승인 후 PC에서 사용자 키로 수행합니다.',
                  rules=WRITE_RULES)
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
                # 규칙 검사는 통과했지만 공용 연결의 보호 처리가 전송 내용(공유 경로 등)을 바꾼다.
                # 검증 결과(checks_passed)와 전송용 payload를 분리한다: payload·해시는 돌려주지 않고,
                # PC 등록 스크립트가 같은 규칙 원본(redmine_draft_rules.py)으로 원문 payload를 만든다.
                result.update(ok=False, checks_passed=True, payload_source='pc', errors=[
                    '규칙 검사는 통과했습니다. 공용 연결의 보호 처리가 공유 경로 등 전송 내용을 바꾸므로 payload와 해시는 반환하지 않습니다. '
                    '전송용 payload는 PC에서 scripts/vxvue_issue_tools/register_redmine_issue.py 로 같은 규칙으로 만듭니다.'])
                result.pop('payload')
                result.pop('draft_sha256')
            else:
                result['checks_passed'] = True
                result['payload_source'] = 'server'
        else:
            result['checks_passed'] = False
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
