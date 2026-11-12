import json
import hashlib

import httpx
import pytest

from app.modules.vxvue_chatbot import redmine_draft
from app.modules.vxvue_chatbot.redmine_draft import validate, FieldMapLookup, DRAFT_TOOLS

PATH = '\\\\10.1.1.100\\전사 공유폴더\\VXvue\\2.Test_Case\\V1.1.0\\Defect\\#'
BODY = f"""[Issue Title]
(DK INNOGEN) [에뮬레이터연동] 현상

[Precondition]
- 에뮬레이터 연동

[Step]
1. 동작한다.

[Actual Result]
1. 변경됨

[Expected Result]
1. 유지되어야 할 것 같음. 사양 확인 필요

[Log, Data]
{PATH}"""


def ok(**kw):
    args = dict(subject='(DK INNOGEN) [에뮬레이터연동] 현상', description=BODY, project_id=61, tracker_id=7, priority_id=2,
                custom_fields=[{'id': 54, 'value': '2767'}, {'id': 84, 'value': 'VSS.Vieworks(DK)'}])
    args.update(kw)
    return validate(**args)


def test_valid_draft_passes_and_hash_is_stable():
    a, b = ok(), ok()
    assert a['ok'] and not a['errors'], a['errors']
    assert a['draft_sha256'] == b['draft_sha256']
    assert a['payload']['issue']['custom_fields'][0]['id'] == 54
    assert ok(subject='(DK INNOGEN) [에뮬레이터연동] 다른 현상')['draft_sha256'] != a['draft_sha256']


def test_expected_before_actual_is_error():
    body = BODY.replace('[Actual Result]', '[TMP]').replace('[Expected Result]', '[Actual Result]').replace('[TMP]', '[Expected Result]')
    assert not ok(description=body)['ok']


def test_missing_section_and_ids_are_errors():
    r = ok(description=BODY.replace('[Precondition]', ''), tracker_id=None)
    assert not r['ok']
    assert any('Precondition' in e for e in r['errors']) and any('tracker_id' in e for e in r['errors'])


def test_single_backslash_share_path_is_error():
    r = ok(description=BODY.replace(PATH, PATH[1:]))
    assert not r['ok'] and any('역슬래시' in e for e in r['errors'])


def test_issue_number_required_after_registration():
    assert not ok(issue_number_known=True)['ok']
    assert ok(description=BODY.replace(PATH, PATH + '25572'), issue_number_known=True)['ok']


@pytest.mark.parametrize('secret', ['X-Redmine-API-Key: abcdefabcdef1234', 'a' * 0 + '0123456789abcdef0123456789abcdef01234567', 'someone@vieworks.com'])
def test_secrets_are_rejected(secret):
    assert not ok(description=BODY + '\n' + secret)['ok']


def test_polite_ending_is_warning_but_pilyo_is_not():
    assert not any('어미' in w for w in ok()['warnings'])  # "사양 확인 필요" must not trigger
    assert any('어미' in w for w in ok(description=BODY.replace('1. 변경됨', '1. 변경돼요'))['warnings'])


def test_disallowed_project_is_error():
    assert not ok(project_id=999)['ok']


class Settings:
    class secrets:
        redmine_host = 'http://redmine.example.test/redmine'
        redmine_api_key = 'k' * 40
    root = None


def test_field_map_is_get_only_and_scoped(tmp_path):
    seen = []

    def handler(request):
        seen.append(request.method)
        path = request.url.path
        if path.endswith('/projects/61.json'):
            return httpx.Response(200, json={'project': {'id': 61, 'name': 'Integration Defect', 'trackers': [{'id': 7, 'name': '결함(QA)'}], 'issue_categories': [{'id': 26, 'name': 'V.S.S.'}]}})
        if path.endswith('/issue_priorities.json'):
            return httpx.Response(200, json={'issue_priorities': [{'id': 2, 'name': '보통', 'is_default': True}]})
        if path.endswith('/projects/61/versions.json'):
            return httpx.Response(200, json={'versions': [{'id': 2767, 'name': '1.1.0.004T1', 'status': 'open'}]})
        if path.endswith('/issues.json'):
            return httpx.Response(200, json={'issues': [
                {'id': 1, 'custom_fields': [{'id': 84, 'name': 'Integration Model', 'value': 'VSS.Vieworks(DK)'}]},
                {'id': 2, 'is_private': True, 'custom_fields': [{'id': 84, 'name': 'Integration Model', 'value': 'SECRET'}]}]})
        return httpx.Response(404)

    s = Settings(); s.root = tmp_path
    result = FieldMapLookup(s, transport=httpx.MockTransport(handler)).field_map(61)
    assert set(seen) == {'GET'}
    assert result['trackers'] == [{'id': 7, 'name': '결함(QA)'}]
    assert result['versions'][0]['id'] == 2767
    assert result['custom_fields'] == [{'id': 84, 'name': 'Integration Model', 'observed_values': ['VSS.Vieworks(DK)']}]
    with pytest.raises(ValueError):
        FieldMapLookup(s, transport=httpx.MockTransport(handler)).field_map(999)


def test_registered_on_vxvue_desktop_only_and_read_only():
    from app.modules.vxvue_chatbot import desktop
    from app.modules.vxvue_chatbot.redmine import bellalun_definitions
    tools = {t['name']: t for t in desktop.definitions()}
    for name in DRAFT_TOOLS:
        assert name in tools
        assert tools[name]['annotations']['readOnlyHint'] is True
    assert not {('bellalun_' + n) for n in DRAFT_TOOLS} & {t['name'] for t in bellalun_definitions()}
    # existing Redmine read tools are untouched
    assert {'get_redmine_issue', 'search_redmine_issues', 'read_redmine_result'} <= set(tools)


def test_write_rules_text_keeps_approval_rule():
    assert '사용자가 "해당 초안"을 명시적으로 승인한 뒤에만' in redmine_draft.WRITE_RULES


# Validates: REQ-VXVUECHATBOT-020
def test_workflow_does_not_append_write_rules_twice():
    """쓰기 규칙은 통합 QA MD 29.10(issue 섹션)에서 배급한다. 응답 끝에 다시 붙이면 같은 규칙이 두 번 실린다."""
    from app.modules.vxvue_chatbot.desktop import workflow
    rules = ('# QA\n<!-- qa-section: id=core; title="공통" -->\nCORE_KEEP\n<!-- /qa-section: id=core -->\n'
             '<!-- qa-section: id=issue; title="이슈"; requires=core -->\nISSUE_KEEP\n<!-- /qa-section: id=issue -->\n')
    current = {'id': 's', 'skill_text': 'ADAPTER', 'rules': rules, 'skill_metadata': None}
    text = workflow(current, {'section': 'issue'})['text']
    assert 'ISSUE_KEEP' in text
    assert '[Redmine 등록(쓰기) 규칙]' not in text


def draft_response(**changes):
    from app.modules.vxvue_chatbot import desktop
    args = dict(subject='(SYNTHETIC) [에뮬레이터연동] 현상', description=BODY,
                project_id=61, tracker_id=7, priority_id=2)
    args.update(changes)
    response = desktop.dispatch({'method': 'tools/call', 'params': {
        'name': 'validate_redmine_draft', 'arguments': args}})
    assert not response['isError'], response
    return json.loads(response['content'][0]['text'])


# Validates: REQ-VXVUECHATBOT-020
def test_mcp_draft_hash_matches_the_returned_non_sensitive_payload():
    result = draft_response(description=BODY.replace(PATH, '원본 경로는 PC에서 관리'))
    assert result['ok'], result
    encoded = json.dumps(result['payload'], ensure_ascii=False, sort_keys=True,
                         separators=(',', ':')).encode('utf-8')
    assert hashlib.sha256(encoded).hexdigest() == result['draft_sha256']


@pytest.mark.parametrize('value', [PATH, r'\\customer.example.test\logs\#12345',
                                 '10.9.8.7', 'someone@example.test'])
def test_mcp_protected_draft_is_not_returned_as_a_registration_payload(value):
    result = draft_response(description=BODY.replace(PATH, value))
    assert not result['ok'], result
    assert result['errors']
    assert 'payload' not in result
    assert 'draft_sha256' not in result
    assert value not in json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize('field', ['subject', 'description', 'custom_fields'])
def test_mcp_rejected_secret_is_not_echoed_in_any_response_field(field):
    secret = '0123456789abcdef' * 2 + '01234567'
    changes = {'description': BODY.replace(PATH, '원본 경로는 PC에서 관리')}
    if field == 'subject':
        changes[field] = '(SYNTHETIC) [에뮬레이터연동] ' + secret
    elif field == 'description':
        changes[field] += '\n' + secret
    else:
        changes[field] = [{'id': 84, 'value': secret}]
    result = draft_response(**changes)
    assert not result['ok']
    assert 'payload' not in result
    assert 'draft_sha256' not in result
    assert secret not in json.dumps(result, ensure_ascii=False)
