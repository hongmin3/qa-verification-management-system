async (page) => {
  // Mocked API + real Vite application. No production API calls or browser launch.
  const assert = (value, message) => { if (!value) throw new Error(message); };
  // The browser tool's isolated VM does not expose Node's URL globals.
  const parseURL = href => {
    const source = String(href);
    const withoutOrigin = source.replace(/^[a-z][a-z0-9+.-]*:\/\/[^/]+/i, '');
    const pathname = withoutOrigin.split(/[?#]/, 1)[0] || '/';
    const query = source.includes('?') ? source.split('?')[1].split('#')[0] : '';
    const searchParams = new Map();
    const decode = value => decodeURIComponent(value.replace(/\+/g, ' '));
    for (const pair of query.split('&')) {
      if (!pair) continue;
      const split = pair.indexOf('=');
      const key = decode(split < 0 ? pair : pair.slice(0, split));
      if (!searchParams.has(key)) searchParams.set(key, decode(split < 0 ? '' : pair.slice(split + 1)));
    }
    return { pathname, searchParams };
  };
  assert(parseURL('http://fixture/manual-hub/api/search?q=%ED%95%9C%EA%B8%80+text&sort=document').searchParams.get('q') === '한글 text', 'Fixture URL parser decoding failed');
  let admin = true;
  let firstStarted;
  let releaseFirst;
  const firstSeen = new Promise(resolve => { firstStarted = resolve; });
  const firstGate = new Promise(resolve => { releaseFirst = resolve; });
  const searches = [];
  const indexing = [];
  const now = '2026-11-06T00:00:00Z';
  const user = () => ({ id: 'smoke-admin', login_id: 'browser-fixture', display_name: '브라우저 검증', role: admin ? 'admin' : 'user', is_active: true, must_change_password: false, last_login_at: now, created_at: now, updated_at: now, is_admin: admin });
  const hit = (id, name, status, snippet) => ({ document_id: id, document_name: name, product_id: 'product-1', product_name: 'Fixture Product', category_name: 'Manual', document_status: 'active', version_id: 'version-' + id, revision: 'R3', version: 'V3', document_number: 'DOC-' + id, language: 'EN', revision_date: '2026-11-06', upload_date: now, uploaded_by_display_name: 'Fixture uploader', original_file_name: name + '.pdf', version_status: 'active', is_current: true, body_status: status, body_detail: status === 'no_text' ? '이미지 PDF에 추출 가능한 텍스트가 없습니다.' : null, snippet });
  await page.route('**/api/**', async route => {
    const request = route.request();
    const url = parseURL(request.url());
    const path = url.pathname.replace(/^.*\/api/, '');
    const reply = data => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(data) });
    if (path === '/auth/me') return reply(user());
    if (path === '/products') return reply([{ id: 'product-1', name: 'Fixture Product', code: 'FIX', description: null, is_active: true, sort_order: 0, created_at: now, updated_at: now, document_count: 2, version_count: 2, created_by_display_name: 'Fixture', last_upload_at: now }]);
    if (path === '/categories') return reply([{ id: 'category-1', name: 'Manual', description: null, is_active: true, sort_order: 0, created_at: now, updated_at: now, document_count: 2 }]);
    if (path === '/search/reindex') {
      const options = request.postDataJSON();
      indexing.push(options);
      if (indexing.length === 1) {
        firstStarted();
        await firstGate;
        return reply({ processed: 2, results: [{ id: 'version-1', status: 'ready', detail: null }, { id: 'version-2', status: 'failed', detail: 'fixture parser failure' }], next_cursor: 'resume-cursor', done: false, current_only: true, remaining: 2 });
      }
      return reply({ processed: 2, results: [{ id: 'version-3', status: 'no_text', detail: 'OCR 미제공' }, { id: 'version-4', status: 'truncated', detail: '본문 일부만 색인했습니다.' }], next_cursor: null, done: true, current_only: true, remaining: 0 });
    }
    if (path === '/search') {
      searches.push(Object.fromEntries(url.searchParams));
      return reply([hit('1', 'Alpha Manual', 'ready', 'needle <img src=x onerror="window.__bodyXss=1"> literal'), hit('2', 'Zeta Manual', 'no_text', null)]);
    }
    return route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: 'Unmocked fixture API: ' + path }) });
  });
  await page.goto('http://127.0.0.1:24682/search?q=seed&sort=document&dir=desc&current_only=true&view=advanced');
  await page.getByRole('link', { name: 'Zeta Manual', exact: true }).waitFor();
  assert(await page.getByRole('searchbox').inputValue() === 'seed', 'Shared query was not restored');
  assert(await page.getByLabel('Current 버전만 검색', { exact: true }).isChecked(), 'Current filter was not restored');
  assert(indexing.length === 0, 'Indexing must never run on screen load');
  assert(searches[0].limit === '200', 'Existing bounded array search contract changed');
  assert(await page.locator('tbody tr').first().innerText().then(text => text.includes('Zeta Manual')), 'Descending URL sort was not restored');
  assert(await page.getByText('needle <img src=x onerror="window.__bodyXss=1"> literal', { exact: true }).count() === 1, 'Snippet text is not visible');
  assert(await page.locator('tbody img').count() === 0, 'Snippet HTML was executed/rendered');
  assert(await page.getByText('본문 검색 가능 · 원문 판 V3', { exact: true }).count() === 1, 'Body status/source version missing');
  assert(await page.getByText('추출 가능한 텍스트 없음 · OCR 미제공 · 원문 판 V3', { exact: true }).count() === 1, 'OCR limitation missing');

  await page.getByText('관리자 · 본문 색인 갱신', { exact: true }).click();
  assert(await page.getByLabel('Current 버전만 (해제하면 과거 판 포함)', { exact: true }).isChecked(), 'Indexing must default to Current');
  await page.getByRole('button', { name: '본문 색인 갱신', exact: true }).click();
  await firstSeen;
  await page.getByRole('button', { name: '중지', exact: true }).click();
  releaseFirst();
  await page.getByRole('button', { name: '본문 색인 재개', exact: true }).waitFor();
  assert(indexing.length === 1, 'Stop must prevent the next batch');
  assert(indexing[0].current_only === true && indexing[0].limit === 2 && indexing[0].force === false, 'Safe default batch parameters changed');
  await page.getByRole('button', { name: '본문 색인 재개', exact: true }).click();
  await page.getByText(/색인 갱신을 마쳤습니다/).waitFor();
  assert(indexing.length === 2 && indexing[1].cursor === 'resume-cursor', 'Resume did not preserve the last accepted cursor');
  assert(await page.getByText(/처리 4개/).count() > 0, 'Cumulative processing progress missing');
  assert(await page.getByText('본문 추출 실패: 1개', { exact: true }).count() === 1, 'Earlier failure was hidden by later batches');

  await page.getByRole('searchbox').fill('changed');
  await page.getByRole('button', { name: '검색', exact: true }).click();
  await page.waitForURL(url => url.searchParams.get('q') === 'changed');
  assert(parseURL(page.url()).searchParams.get('sort') === 'document', 'Submitting a query lost the sort');
  await page.goBack();
  await page.waitForURL(url => url.searchParams.get('q') === 'seed');
  await page.getByRole('searchbox').waitFor();
  await page.waitForFunction(() => document.querySelector('input[type="search"]')?.value === 'seed');
  await page.reload();
  await page.getByRole('link', { name: 'Zeta Manual', exact: true }).waitFor();
  assert(await page.getByRole('searchbox').inputValue() === 'seed', 'Refresh did not restore query');
  assert(indexing.length === 2, 'Refresh triggered unauthorized automatic indexing');
  admin = false;
  await page.reload();
  await page.getByRole('link', { name: 'Zeta Manual', exact: true }).waitFor();
  assert(await page.getByText('관리자 · 본문 색인 갱신', { exact: true }).count() === 0, 'Non-admin sees admin indexing control');
  await page.unroute('**/api/**');
  return { mocked_api: true, actual_ui: true, shared_query_sort: true, snippet_escaped: true, original_version_status: true, explicit_admin_indexing: true, default_current: true, stop_resume_cursor: true, cumulative_failure_status: true, history_refresh: true, non_admin_hidden: true, search_requests: searches.length, indexing_requests: indexing.length };
}
