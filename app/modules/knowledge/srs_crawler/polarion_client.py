"""기존 QA Agent의 읽기 전용 REST 전송을 재사용하는 SRS 수집 클라이언트."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote, urlsplit
import time

import httpx

from app.modules.daily_qa.polarion import PolarionError, ReadOnlyPolarionClient
from app.modules.daily_qa.settings import PolarionSettings

PolarionApiError = PolarionError


class PolarionClient:
    def __init__(self, host: str, token: str, verify_ssl=True, timeout_seconds=90,
                 request_interval_seconds=.15, page_size=100):
        self.host, self.token = host, token
        self.verify_ssl, self.timeout_seconds = verify_ssl, timeout_seconds
        self.request_interval_seconds, self.page_size = request_interval_seconds, page_size
        self.clients = {}
        self.session = httpx.Client(headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json'},
                                    verify=verify_ssl, timeout=timeout_seconds, follow_redirects=False)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.session.close()

    def _http_get(self, url, params):
        last = ''
        for attempt in range(1, 4):
            try:
                response = self.session.get(url, params=params)
                if 400 <= response.status_code < 500:
                    raise PolarionApiError(f'Polarion 요청 오류 HTTP {response.status_code}')
                response.raise_for_status()
                return response.json()
            except PolarionApiError:
                raise
            except (httpx.HTTPError, ValueError) as exc:
                last = type(exc).__name__
                if attempt < 3:
                    time.sleep(min(2 ** attempt, 8))
            finally:
                time.sleep(self.request_interval_seconds)
        raise PolarionApiError(f'Polarion 요청이 3회 실패했습니다 ({last}).')

    def _client(self, project_id):
        if project_id not in self.clients:
            self.clients[project_id] = ReadOnlyPolarionClient(PolarionSettings(
                host=self.host, token=self.token, project_id=project_id,
                srs_query='type:srs', issue_query='type:issue', verify_ssl=self.verify_ssl,
                timeout_seconds=self.timeout_seconds, request_interval_seconds=self.request_interval_seconds,
                page_size=self.page_size), transport=self._http_get)
        return self.clients[project_id]

    def ping(self, project_id):
        self._client(project_id)._get(f'/projects/{project_id}')
        return True

    def get_total_count(self, project_id, query):
        payload = self._client(project_id)._get(f'/projects/{project_id}/workitems',
                                             {'query': query, 'page[size]': 1, 'page[number]': 1})
        count = payload.get('meta', {}).get('totalCount')
        if count is None:
            raise PolarionApiError('Polarion 응답에 예상 건수가 없습니다.')
        return int(count)

    def iter_workitems(self, project_id, query, fields='@all'):
        return self._client(project_id).iter_workitems(query, fields=fields, sort='oldId')

    def document_scope(self, project_id, space, name):
        client = self._client(project_id)
        document_id = f'{project_id}/{space}/{name}'
        path = f'/projects/{project_id}/spaces/{quote(space, safe="")}/documents/{quote(name, safe="")}'
        before = client._get(path, {'fields[documents]': '@all'}).get('data') or {}
        if before.get('id') != document_id:
            raise PolarionApiError('수집할 LiveDoc을 확인할 수 없습니다.')
        parts = {}
        page = 1
        while True:
            response = client._get(path + '/parts', {
                'page[size]': self.page_size, 'page[number]': page, 'fields[document_parts]': '@all',
            })
            batch = response.get('data')
            if not isinstance(batch, list) or not batch or response.get('errors') or page > 1000:
                raise PolarionApiError('LiveDoc 부분 목록을 끝까지 읽지 못했습니다.')
            # Polarion may repeat the whole set on each advertised page.
            for part in batch:
                previous = parts.setdefault(part['id'], part)
                if previous != part:
                    raise PolarionApiError('조회 중 LiveDoc의 부분 목록이 변경됐습니다.')
            if not (response.get('links') or {}).get('next'):
                break
            page += 1
        ids = set()
        for part in parts.values():
            relations = part.get('relationships') or {}
            for relation in ('previousPart', 'nextPart'):
                neighbor = (relations.get(relation) or {}).get('data')
                if neighbor and neighbor['id'] not in parts:
                    raise PolarionApiError('LiveDoc 부분 목록에 연결된 항목이 빠졌습니다.')
            if part.get('attributes', {}).get('type') == 'workitem':
                item = (relations.get('workItem') or {}).get('data') or {}
                item_id = item.get('id', '')
                if not item_id.startswith(project_id + '/') or item_id in ids:
                    raise PolarionApiError('LiveDoc 작업 항목 ID가 중복됐거나 다른 프로젝트에 속합니다.')
                ids.add(item_id)
        after = client._get(path, {'fields[documents]': '@all'}).get('data') or {}
        if not ids or before != after:
            raise PolarionApiError('LiveDoc이 비어 있거나 수집 중 변경됐습니다.')
        attrs = after['attributes']
        return ids, {'id': document_id, 'revision': attrs.get('version'), 'status': attrs.get('status'),
                     'updated': attrs.get('updated'), 'url': f'{self.host.rstrip("/")}/#/project/{project_id}/wiki/{quote(space, safe="")}/{quote(name, safe="")}'}

    def get_comments(self, project_id, wi_id):
        return self._client(project_id).get_comments(wi_id, strict=True)

    def get_attachments(self, project_id, wi_id):
        client = self._client(project_id)
        path = f'/projects/{project_id}/workitems/{wi_id}/attachments'
        items, page = [], 1
        while True:
            payload = client._get(path, {'page[size]': self.page_size, 'page[number]': page})
            batch = payload.get('data') or []
            items.extend(batch)
            if not batch or not payload.get('links', {}).get('next'):
                return items
            page += 1

    def download_attachment(self, content_url, dest_path: Path):
        # 원본 서버 밖으로 인증 헤더를 보내지 않는다.
        if urlsplit(content_url)[:2] != urlsplit(self.host)[:2]:
            raise PolarionApiError('첨부파일 주소가 Polarion 서버 밖을 가리킵니다.')
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = dest_path.with_suffix(dest_path.suffix + '.part')
        for attempt in range(3):
            try:
                with self.session.stream('GET', content_url, headers={'Accept': '*/*'}) as response:
                    if response.status_code < 500 and response.status_code != 200:
                        raise PolarionApiError(f'첨부파일 읽기 실패 HTTP {response.status_code}')
                    response.raise_for_status()
                    with temporary.open('wb') as handle:
                        for chunk in response.iter_bytes():
                            handle.write(chunk)
                temporary.replace(dest_path)
                return
            except httpx.HTTPError as exc:
                if attempt == 2:
                    raise PolarionApiError(f'첨부파일 읽기 3회 실패 ({type(exc).__name__})') from exc
                time.sleep(2 ** attempt)
            finally:
                temporary.unlink(missing_ok=True)
                time.sleep(self.request_interval_seconds)
