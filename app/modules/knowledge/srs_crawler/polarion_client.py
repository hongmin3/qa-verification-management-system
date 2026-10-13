"""기존 QA Agent의 읽기 전용 REST 전송을 재사용하는 SRS 수집 클라이언트."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit
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

    def iter_workitems(self, project_id, query):
        return self._client(project_id).iter_workitems(query, sort='oldId')

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
        try:
            with self.session.stream('GET', content_url, headers={'Accept': '*/*'}) as response:
                if response.status_code != 200:
                    raise PolarionApiError(f'첨부파일 읽기 실패 HTTP {response.status_code}')
                with temporary.open('wb') as handle:
                    for chunk in response.iter_bytes():
                        handle.write(chunk)
            temporary.replace(dest_path)
        finally:
            temporary.unlink(missing_ok=True)
        time.sleep(self.request_interval_seconds)
