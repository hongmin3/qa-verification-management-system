/* Prepared targets are server-owned. Display and selection never invoke AI.
   `상세 분석` 화면(#qa-selection)은 목록과 선택을, 점검 화면(#qa-prepared-notice)은 한 줄 안내 갱신만 맡는다. */
window.QASelection = function (progress) {
  const root = document.getElementById('qa-selection');
  const notice = document.getElementById('qa-prepared-notice');
  // 새 실행이 끝났을 때만 true다. 화면을 연 시점의 실행 중·완료 기록은 이미 화면에 반영돼 있다.
  const finished = (detail, state) => {
    const id = detail.last_run && detail.last_run.id;
    if (state.id === undefined) { state.id = detail.running ? null : id || null; return false; }
    if (!detail.running && id && id !== state.id) { state.id = id; return true; }
    return false;
  };
  async function readPlan(product) {
    const response = await fetch('/qa-agent/plans/latest?product=' + encodeURIComponent(product), {cache: 'no-store'});
    if (!response.ok) throw new Error('준비된 대상을 읽지 못했습니다.');
    return response.json();
  }
  if (!root) {
    if (!notice) return;
    const seen = {};
    document.addEventListener('qa:status', async event => {
      if (!finished(event.detail, seen)) return;
      try {
        const plan = (await readPlan(notice.dataset.product)).plan;
        notice.replaceChildren();
        if (!plan) { notice.textContent = '아직 준비한 대상이 없습니다. 위 버튼으로 먼저 준비하세요.'; return; }
        if (plan.expired) { notice.textContent = '준비 목록 만료 · 다시 준비하세요'; return; }
        const link = document.createElement('a');
        link.href = '/qa-agent/select?product=' + encodeURIComponent(notice.dataset.product);
        link.textContent = '상세 분석 열기';
        notice.append(`준비된 대상 ${plan.targets.length}건 · `, link);
      } catch (error) { /* 읽지 못하면 서버가 그린 안내를 그대로 둔다. */ }
    });
    return;
  }
  const status = document.getElementById('qa-selection-status');
  const list = document.getElementById('qa-selection-targets');
  const run = document.getElementById('qa-selected-run');
  const review = document.getElementById('qa-selected-review');
  let plan = null, running = false, loading = false, launching = false;
  const seen = {};
  const selected = () => [...list.querySelectorAll('input:checked')].map(input => input.value);
  function enable() {
    run.disabled = launching || running || !plan || plan.expired || !selected().length;
    review.disabled = launching || running || !plan || plan.expired || selected().length !== 1;
  }
  function meta(parts) {
    const line = document.createElement('div'); line.className = 'qa-selection-meta';
    for (const text of parts.filter(Boolean)) { const span = document.createElement('span'); span.textContent = text; line.append(span); }
    return line;
  }
  function renderRow(item) {
    const row = document.createElement('label'), input = document.createElement('input'), body = document.createElement('div');
    row.className = 'qa-selection-row'; body.className = 'qa-selection-body';
    input.type = 'checkbox'; input.value = item.key; input.addEventListener('change', enable);
    const head = document.createElement('div'), number = document.createElement('b');
    number.textContent = item.subject; head.append(number, document.createTextNode(item.title ? ' · ' + item.title : ''));
    body.append(head, meta([
      item.kind_label,
      item.reporter_name && '작성자 ' + item.reporter_name,
      item.kind !== 'SPEC_COVERAGE' && '이슈 업데이트 ' + (item.issue_updated_kst || '미확인'),
      item.reason && '분석 이유 ' + item.reason,
      item.state === 'reused' ? '기존 결과 사용' : '새 분석 후보']));
    if (item.source) {
      const link = document.createElement('a');
      link.href = '/qa-agent/findings/' + encodeURIComponent(item.source.finding_ids[0]);
      link.textContent = '기존 결과 보기 (' + item.source.analyzed_at + ')'; body.append(link);
    }
    row.append(input, body);
    return row;
  }
  async function refresh() {
    if (loading) return;
    loading = true;
    try {
      const data = await readPlan(root.dataset.product); plan = data.plan;
      list.replaceChildren();
      const budget = data.budget;
      status.textContent = `오늘 새 호출 ${budget.used}/${budget.per_day}회 · 실행당 최대 ${budget.per_run}회. `;
      if (!plan) { status.textContent += '점검 화면에서 대상을 먼저 준비하세요.'; return; }
      if (plan.expired) {
        // 만료된 목록은 고를 수 없다. 서버도 30분이 지난 선택을 거절한다.
        status.textContent = `준비 목록 만료 (준비 ${plan.created_kst} 한국 시간) · 다시 준비하세요`;
        return;
      }
      status.textContent += `대상 ${plan.targets.length}건 (준비 ${plan.created_kst} 한국 시간) · 실제 비교 기준: 사양 ${plan.basis.srs || '없음'}, 이슈 ${plan.basis.issues || '없음'}. 준비 후 30분 안에 선택하세요.`;
      for (const [kind, coverage] of Object.entries(plan.basis.coverage || {})) {
        status.textContent += ` ${kind === 'srs' ? '사양' : '이슈'} 자료: ${coverage.start || '기준 없음'} ~ ${coverage.end || '없음'}. ${(coverage.warnings || []).join(' ')}`;
      }
      for (const item of plan.targets) list.append(renderRow(item));
    } catch (error) { status.textContent = error.message; plan = null; }
    finally { loading = false; enable(); }
  }
  async function launch(isReview) {
    if (!plan || plan.expired || running || launching) return;
    const body = new FormData(); body.append('product', root.dataset.product);
    body.append('targets', JSON.stringify(selected())); body.append('review', String(isReview));
    launching = true; enable();
    try { await progress.launch('/qa-agent/plans/' + encodeURIComponent(plan.id) + '/execute', body, status); }
    finally { launching = false; enable(); }
  }
  run.addEventListener('click', () => launch(false));
  review.addEventListener('click', () => launch(true));
  document.addEventListener('qa:status', event => {
    running = !!event.detail.running;
    if (finished(event.detail, seen)) refresh();
    enable();
  });
  refresh();
};
