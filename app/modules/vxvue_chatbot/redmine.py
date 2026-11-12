"""Bounded Redmine GETs; source URLs and credentials never come from tool input."""
from datetime import datetime, timezone
import json
import re
import shlex
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.core.security_filter import mask_payload
from .desktop_polarion import PolarionLookup, integer

DEFAULT_PROJECTS = (61, 29, 107)
# XIPL(33)·QXLink(QA Defect)(41): VXvue 검증이 참조하는 연동 모듈. 명시 지정·번호 조회만 허용한다.
ALLOWED_PROJECTS = DEFAULT_PROJECTS + (33, 41)
MAX_BYTES = 10 * 1024 * 1024
# Redmine search.json event types for issues: 'issue' (open) and 'issue-closed'.
ISSUE_RESULT_TYPES = ('issue', 'issue-closed', 'issue closed')


class RedmineLookup(PolarionLookup):
    DEFAULT = DEFAULT_PROJECTS
    ALLOWED = ALLOWED_PROJECTS
    SUBDIR = 'data/vxvue_desktop/redmine'
    LABEL = 'VXvue'

    def __init__(self, settings, root=None, transport=None):
        self.root = Path(root or settings.root) / self.SUBDIR if root or hasattr(settings, 'root') else Path(self.SUBDIR)
        self.host = str(settings.secrets.redmine_host).rstrip('/')
        self.key = settings.secrets.redmine_api_key
        parts = urlsplit(self.host)
        if not self.host or not self.key:
            raise ValueError('Redmine 설정이 없습니다. 서버 REDMINE_HOST / REDMINE_API_KEY를 확인하세요.')
        if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment or parts.path not in ('', '/', '/redmine'):
            raise ValueError('Redmine 서버 설정 형식을 확인하세요. 호스트와 선택적 /redmine 경로만 허용합니다.')
        try:
            parts.port
        except ValueError:
            raise ValueError("Redmine 서버 포트 설정을 확인하세요.") from None
        self.transport = transport
        self.deadline = None

    def _get(self, path, params=None):
        remaining = self.deadline - time.monotonic() if self.deadline else 20
        if remaining <= 0:
            raise ValueError('Redmine 전체 조회 시간 55초를 넘었습니다. 페이지 크기를 줄여 다시 조회하세요.')
        try:
            with httpx.Client(transport=self.transport, timeout=min(20, remaining), follow_redirects=False, trust_env=False) as client:
                with client.stream('GET', self.host + path, params=params or {}, headers={'X-Redmine-API-Key': self.key, 'Accept': 'application/json'}) as response:
                    if response.status_code != 200:
                        raise ValueError(f'Redmine 조회 실패 HTTP {response.status_code}')
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        if self.deadline and time.monotonic() > self.deadline:
                            raise ValueError('Redmine 전체 조회 시간 55초를 넘었습니다.')
                        body.extend(chunk)
                        if len(body) > MAX_BYTES:
                            raise ValueError('Redmine 응답이 10 MiB 상한을 넘었습니다.')
            value = json.loads(body)
        except (httpx.HTTPError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f'Redmine 조회 실패 ({type(exc).__name__}). 연결과 서버 응답을 확인하세요.') from None
        if not isinstance(value, dict) or value.get('errors'):
            raise ValueError('Redmine 응답 형식이 맞지 않습니다.')
        return value

    def _authorize_issue(self, issue):
        if not isinstance(issue, dict) or issue.get('is_private') is True or (issue.get('project') or {}).get('id') not in self.ALLOWED:
            raise ValueError(f'이 이슈는 공용 {self.LABEL} 조회 범위에 없습니다.')

    @staticmethod
    def _public_issue(issue):
        value = dict(issue)
        journals = value.get('journals', [])
        if not isinstance(journals, list) or any(not isinstance(j, dict) or not isinstance(j.get('private_notes', False), bool) for j in journals):
            raise ValueError('Redmine 댓글 공개 범위를 확인할 수 없습니다.')
        visible = [j for j in journals if not j.get('private_notes', False)]
        if 'journals' in value:
            value['journals'] = visible
        return value

    @staticmethod
    def _public_fields(issue):
        """Readable text per field label: subject, description, public journals, custom fields."""
        from app.parsers.polarion_issue import html_to_text

        def norm(text):
            return ' '.join(html_to_text(str(text or ''))[0].casefold().split())
        fields = [('제목', norm(issue.get('subject'))), ('상세 설명', norm(issue.get('description')))]
        fields.append(('댓글', ' '.join(norm(j.get('notes')) for j in issue.get('journals', []))))
        for field in issue.get('custom_fields') or []:
            if isinstance(field, dict) and isinstance(field.get('name'), str):
                value = field.get('value')
                value = ' '.join(map(str, value)) if isinstance(value, list) else value
                if value not in (None, ''):
                    fields.append((field['name'], norm(value)))
        return fields

    @classmethod
    def _public_match(cls, issue, terms):
        """Labels of public fields containing a term, or None when not every term is found."""
        fields = cls._public_fields(issue)
        public = ' '.join(text for _, text in fields)
        wanted = [' '.join(term.casefold().split()) for term in terms]
        if not all(term in public for term in wanted):
            return None
        return [label for label, text in fields if any(term in text for term in wanted)]

    @staticmethod
    def _metadata(issue):
        if not isinstance(issue, dict) or not isinstance(issue.get('subject'), str):
            raise ValueError('Redmine 이슈 응답 형식이 맞지 않습니다.')
        integer(issue.get('id'), 1, 2_147_483_647)
        for key in ('project', 'status'):
            if not isinstance(issue.get(key), dict) or not isinstance(issue[key].get('name'), str):
                raise ValueError('Redmine 이슈 메타데이터가 없습니다.')
            integer(issue[key].get('id'), 1, 2_147_483_647)
        return {'id':issue['id'], 'subject':issue['subject'], 'project':{k:issue['project'][k] for k in ('id','name')}, 'status':{k:issue['status'][k] for k in ('id','name')}}

    def issue(self, issue_id):
        integer(issue_id, 1, 2_147_483_647)
        self.deadline = time.monotonic() + 55
        value = self._get(f'/issues/{issue_id}.json', {'include': 'journals,attachments,relations'})
        if not isinstance(value.get('issue'), dict) or value['issue'].get('id') != issue_id:
            raise ValueError('Redmine 상세 응답 형식이 맞지 않습니다.')
        self._authorize_issue(value['issue'])
        value['issue'] = self._public_issue(value['issue'])
        value.update(collected_at=datetime.now(timezone.utc).isoformat(), scope='본문·공개 journals·첨부 목록·relations 조회. 비공개 댓글은 저장·반환에서 제외. 첨부 본문 미수집. 수집은 분석 완료가 아님.')
        value, report = mask_payload(value)
        value['protection'] = report.as_dict()
        self.root.mkdir(parents=True, exist_ok=True)
        # Reuse the bounded, expiring result reader without sharing the Polarion directory.
        for path in self.root.glob('*.json'):
            if re.fullmatch(r'[0-9a-f]{32}\.json', path.name) and path.stat().st_mtime < time.time()-86400:
                path.unlink(missing_ok=True)
        result_id = uuid.uuid4().hex
        (self.root / f'{result_id}.json').write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        return self.read(result_id)

    def read(self, result_id, offset=0, limit=12000):
        try:
            return super().read(result_id, offset=offset, limit=limit)
        except ValueError as exc:
            raise ValueError(str(exc).replace('Polarion', 'Redmine')) from None

    def search(self, keywords, project_ids=None, status='*', page=1, page_size=50, full_text=False, expand_synonyms=True):
        if not isinstance(expand_synonyms, bool): raise ValueError('expand_synonyms는 참 또는 거짓입니다.')
        if not isinstance(keywords, str) or not keywords.strip() or len(keywords) > 500:
            raise ValueError('검색어는 1~500자로 입력하세요.')
        project_ids = list(self.DEFAULT) if project_ids is None else project_ids
        if not isinstance(project_ids, list) or not 1 <= len(project_ids) <= 10:
            raise ValueError('프로젝트 번호는 1~10개입니다.')
        for project in project_ids:
            integer(project, 1, 2_147_483_647)
            if project not in self.ALLOWED: raise ValueError('허용 프로젝트는 ' + ', '.join(map(str, self.ALLOWED)) + '입니다.')
        if len(set(project_ids)) != len(project_ids): raise ValueError('프로젝트 번호가 중복됐습니다.')
        if not isinstance(status, str) or status not in ('*', 'open', 'closed') and not re.fullmatch(r'[1-9][0-9]*', status):
            raise ValueError('상태는 *, open, closed 또는 상태 번호입니다.')
        if full_text and status in ('open', 'closed'): raise ValueError('전문 검색의 상태 제한은 상태 번호를 사용하세요.')
        if not isinstance(full_text, bool): raise ValueError('full_text는 참 또는 거짓입니다.')
        integer(page, 1, 100000); integer(page_size, 1, 100)
        terms = shlex.split(keywords) if full_text else []
        if full_text and (not terms or any(not any(c.isalnum() for c in term) for term in terms)):
            raise ValueError('전문 검색어에는 단어나 따옴표로 묶은 구문을 입력하세요.')
        self.deadline = time.monotonic() + 55
        items, errors, totals, next_pages = [], [], {}, {}
        seen = set()
        for project in project_ids:
            try:
                params = {'offset': (page-1)*page_size, 'limit': page_size}
                if full_text:
                    # Redmine treats any present open_issues/titles_only value as on, so '0' would
                    # restrict the search to open issues and titles. Omit both to search everything.
                    params.update(q=keywords, issues=1, all_words=1)
                    data = self._get(f'/projects/{project}/search.json', params)
                    rows = data.get('results')
                else:
                    params.update(project_id=project, subproject_id='!*', status_id=status, subject='~'+keywords, sort='id:asc')
                    data = self._get('/issues.json', params)
                    rows = data.get('issues')
                count, offset, limit = data.get('total_count'), data.get('offset'), data.get('limit')
                integer(count, 0, 2_147_483_647); integer(offset, 0, 10_000_000); integer(limit, 1, 100)
                if not isinstance(rows, list) or len(rows)>page_size or offset != params['offset'] or limit != page_size or not rows and offset < count:
                    raise ValueError('Redmine 페이지 응답이 맞지 않습니다. 빈 검색으로 처리하지 않았습니다.')
                if rows and (offset+len(rows)>count or offset+len(rows)<count and len(rows)!=limit):
                    raise ValueError('Redmine 페이지 건수가 전체 수와 맞지 않습니다.')
                totals[str(project)] = None
                if offset+len(rows)<count: next_pages[str(project)] = page+1
                page_seen = set()
                for row in rows:
                    if full_text:
                        if not isinstance(row, dict) or row.get('type') not in ISSUE_RESULT_TYPES: raise ValueError('전문 검색에 이슈 외 결과가 있습니다.')
                        integer(row.get('id'), 1, 2_147_483_647)
                        requested_id = row['id']
                        row = self._get(f"/issues/{requested_id}.json", {'include':'journals'}).get('issue')
                        if not isinstance(row, dict) or row.get('id') != requested_id:
                            raise ValueError('검색 후보와 상세 이슈 번호가 다릅니다.')
                    try:
                        self._authorize_issue(row)
                    except ValueError:
                        continue
                    if row['project']['id'] != project:
                        continue
                    meta = self._metadata(row)
                    if meta['id'] in page_seen:
                        raise ValueError('Redmine 페이지에 중복 이슈 번호가 있습니다. 전체 수집 완료로 처리하지 않았습니다.')
                    page_seen.add(meta['id'])
                    if full_text:
                        row = self._public_issue(row)
                        matched_in = self._public_match(row, terms)
                        if matched_in is None:
                            continue
                        meta['matched_in'] = matched_in
                    if meta['id'] in seen:
                        continue
                    if full_text and status != '*':
                        if status in ('open','closed'):
                            raise ValueError('전문 검색의 상태 제한은 상태 번호를 사용하세요.')
                        if str(meta['status']['id']) != status: continue
                    seen.add(meta['id'])
                    items.append(meta)
            except ValueError as exc:
                errors.append({'project_id': project, 'error': str(exc)})
        expansion = None
        if expand_synonyms and not full_text and page == 1:
            # 한영 동의어(REQ-VXVUECHATBOT-021): 단어 하나를 바꾼 변형 검색어로 제목을 더 찾아 합친다.
            from app.core.qa_synonyms import variants
            expansion = {'queries': [], 'added': 0, 'next_pages': {}, 'errors': []}
            for variant in variants(keywords, limit=3):
                sub = self.search(variant, project_ids, status, 1, page_size, False, expand_synonyms=False)
                expansion['queries'].append(variant)
                for meta in sub['items']:
                    if meta['id'] not in seen:
                        seen.add(meta['id'])
                        items.append({**meta, 'matched_query': variant})
                        expansion['added'] += 1
                if sub['next_pages']:
                    expansion['next_pages'][variant] = sub['next_pages']
                expansion['errors'] += [{**e, 'query': variant} for e in sub['errors']]
            if not expansion['queries']:
                expansion = None
        result = {'items': items, 'project_ids': project_ids, 'status': status, 'search_mode': 'full_text' if full_text else 'subject',
                'page': page, 'page_size': page_size, 'total_count_by_project': totals, 'next_pages': next_pages,
                'returned': len(items), 'complete': False, 'errors': errors,
                'limitations': ['공개 이슈 후보만 제공하며 비공개·확인 불가 후보와 전체 건수는 집계하지 않습니다. 전문 검색은 종료 이슈를 포함하고 공개 제목·본문·댓글·사용자 정의 항목에서 일치를 다시 확인해 matched_in에 항목 이름을 남깁니다. 제목·전문 조회 모두 전체 수집 완료를 뜻하지 않으며 다음 페이지는 원본 후보 페이지입니다.'],
                'collected_at': datetime.now(timezone.utc).isoformat(), 'scope': '목록 메타데이터만 조회. 전체 수집과 상세 분석 완료는 별개.'}
        if expansion:
            result['synonym_expansion'] = expansion
            result['returned'] = len(items)
        return result


def lookup(cls=None):
    from app.core.config import get_settings
    return (cls or RedmineLookup)(get_settings())


def call(name, arguments, cls=None, prefix=''):
    """Run a Redmine read tool; `prefix` strips a product prefix such as 'bellalun_'."""
    cls = cls or RedmineLookup
    name = name.removeprefix(prefix)
    if name == 'read_redmine_result':
        from app.core.config import get_settings
        reader = object.__new__(cls)
        reader.root = get_settings().root / cls.SUBDIR
        return reader.read(**arguments)
    reader = lookup() if cls is RedmineLookup else lookup(cls)
    return getattr(reader, {'get_redmine_issue': 'issue', 'search_redmine_issues': 'search'}[name])(**arguments)


SEARCH_SCOPE = 'XIPL(33)·QXLink QA Defect(41)는 project_ids로 지정.'


def definitions(cls=None, prefix='', scope_note=None):
    cls = cls or RedmineLookup
    scope_note = SEARCH_SCOPE if scope_note is None else scope_note
    default = '·'.join(map(str, cls.DEFAULT))
    string={'type':'string'}; positive={'type':'integer','minimum':1}
    specs=[
        ('get_redmine_issue',f'{cls.LABEL} Redmine 이슈 상세·journals·첨부 목록·relations·사용자 정의 항목 읽기. 첨부 본문은 미수집. 잘리면 {prefix}read_redmine_result로 이어 읽기.',{'issue_id':positive},['issue_id']),
        ('search_redmine_issues',f'{cls.LABEL} Redmine 여러 프로젝트 제목 검색. 기본 {default} 및 모든 상태. {scope_note} full_text=true는 종료 이슈를 포함한 전문 검색이며 공개 제목·본문·댓글·사용자 정의 항목 일치를 확인해 matched_in에 남긴다. 프로젝트별 next_pages와 errors 확인. 목록은 번호·프로젝트·상태·제목(전문 검색은 matched_in 포함)만.',{'keywords':string,'project_ids':{'type':'array','minItems':1,'maxItems':10,'items':{'type':'integer','enum':list(cls.ALLOWED)}},'status':string,'page':positive,'page_size':{'type':'integer','minimum':1,'maximum':100},'full_text':{'type':'boolean'},'expand_synonyms':{'type':'boolean','description':'제목 검색 첫 페이지에서 한영 동의어 변형(버키/Bucky/Receptor, 재실행/재기동/restart, 유지/remember)도 검색해 합친다. 기본 true. 추가 결과는 matched_query 표시.'}},['keywords']),
        ('read_redmine_result',f'수집·보호한 {cls.LABEL} Redmine 상세를 문자 위치로 이어 읽기. 24시간 보관.',{'result_id':string,'offset':{'type':'integer','minimum':0},'limit':{'type':'integer','minimum':1,'maximum':12000}},['result_id'])]
    return [{'name':prefix+n,'description':d,'inputSchema':{'type':'object','properties':p,'required':r,'additionalProperties':False},'annotations':{'readOnlyHint':True,'destructiveHint':False,'openWorldHint':False}} for n,d,p,r in specs]


class BellalunRedmineLookup(RedmineLookup):
    """Bellalun Viewer issues: Mammo System(VDMS Series) and the previous VDMS 1000B generation."""
    # 75 = VDMS Series > QA Defect (기본), 86 = VDMS Series > Software Dev,
    # 35 = old VDMS 1000B > QA Defect, 37 = old VDMS 1000B > Dev Defect (참고 이력, 명시 지정만).
    DEFAULT = (75,)
    ALLOWED = (75, 86, 35, 37)
    SUBDIR = 'data/bellalun_shared/redmine'
    LABEL = 'Bellalun'


BELLALUN_PREFIX = 'bellalun_'
BELLALUN_SCOPE = 'VDMS Series Software Dev(86)와 이전 세대 VDMS 1000B QA Defect(35)·Dev Defect(37)는 project_ids로 지정.'


def bellalun_definitions():
    return definitions(BellalunRedmineLookup, BELLALUN_PREFIX, BELLALUN_SCOPE)


def bellalun_call(name, arguments):
    return call(name, arguments, BellalunRedmineLookup, BELLALUN_PREFIX)
