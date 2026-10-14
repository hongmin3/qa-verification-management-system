/* Prepared targets are server-owned. Display and selection never invoke AI. */
window.QASelection = function (progress) {
  const root = document.getElementById('qa-selection');
  if (!root) return;
  const status = document.getElementById('qa-selection-status');
  const list = document.getElementById('qa-selection-targets');
  const run = document.getElementById('qa-selected-run');
  const review = document.getElementById('qa-selected-review');
  let plan = null, running = false, lastRun = null, loading = false, launching = false;
  const selected = () => [...list.querySelectorAll('input:checked')].map(input => input.value);
  function enable() {
    run.disabled = launching || running || !plan || !selected().length;
    review.disabled = launching || running || !plan || selected().length !== 1;
  }
  async function refresh() {
    if (loading) return;
    loading = true;
    try {
      const response = await fetch('/qa-agent/plans/latest?product=' + encodeURIComponent(root.dataset.product), {cache: 'no-store'});
      if (!response.ok) throw new Error('준비된 대상을 읽지 못했습니다.');
      const data = await response.json(); plan = data.plan;
      list.replaceChildren();
      const budget = data.budget;
      status.textContent = `오늘 새 호출 ${budget.used}/${budget.per_day}회 · 실행당 최대 ${budget.per_run}회. `;
      if (!plan) { status.textContent += '위 버튼으로 대상을 먼저 준비하세요.'; return; }
      status.textContent += `대상 ${plan.targets.length}건 · 실제 비교 기준: 사양 ${plan.basis.srs || '없음'}, 이슈 ${plan.basis.issues || '없음'}. 준비 후 30분 안에 선택하세요.`;
      for (const [kind, coverage] of Object.entries(plan.basis.coverage || {})) {
        status.textContent += ` ${kind === 'srs' ? '사양' : '이슈'} 자료: ${coverage.start || '기준 없음'} ~ ${coverage.end || '없음'}. ${(coverage.warnings || []).join(' ')}`;
      }
      for (const item of plan.targets) {
        const row = document.createElement('div'); row.className = 'qa-selection-row';
        const label = document.createElement('label'), input = document.createElement('input');
        input.type = 'checkbox'; input.value = item.key; input.addEventListener('change', enable);
        label.append(input, document.createTextNode(` ${item.subject} · ${item.title} · ${item.state === 'reused' ? '기존 결과 사용' : '새 분석 후보'}`));
        row.append(label);
        if (item.source) {
          const link = document.createElement('a');
          link.href = '/qa-agent/findings/' + encodeURIComponent(item.source.finding_ids[0]);
          link.textContent = ' 기존 결과 보기 (' + item.source.analyzed_at + ')'; row.append(link);
        }
        list.append(row);
      }
    } catch (error) { status.textContent = error.message; plan = null; }
    finally { loading = false; enable(); }
  }
  async function launch(isReview) {
    if (!plan || running || launching) return;
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
    const id = event.detail.last_run && event.detail.last_run.id;
    if (!running && id && id !== lastRun) { lastRun = id; refresh(); }
    enable();
  });
  refresh();
};
