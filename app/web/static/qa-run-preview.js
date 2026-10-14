/* REQ-QAINTEL-041: Read-only saved-work preview. Never start an analysis. */
(function () {
  const panel = document.getElementById('qa-run-preview');
  if (!panel) return;
  const since = document.getElementById('run-since'), until = document.getElementById('run-until');
  let sequence = 0, controller;
  function row(title, data, labels) {
    const text = document.createElement('p');
    const kinds = Object.entries(data.targets).map(([kind, count]) => `${labels[kind] || kind} ${count}건`).join(', ');
    text.textContent = `${title}: 저장된 대기 ${data.waiting}건 중 ${data.selected}건을 이어받습니다. 이번에 제외 ${data.deferred}건.` +
      (kinds ? ` 대상: ${kinds}. 건수 기준 예상 ${data.batches_by_count}묶음이며 자료 크기에 따라 더 나뉩니다.` : '');
    return text;
  }
  async function refresh() {
    const request = ++sequence;
    if (controller) controller.abort();
    controller = new AbortController();
    const active = controller;
    const timeout = setTimeout(() => active.abort(), 10000);
    panel.textContent = '저장된 대기 분석을 확인하는 중입니다.';
    try {
      const params = new URLSearchParams({product: panel.dataset.product, since: since.value, until: until.value});
      const response = await fetch('/qa-agent/run-preview?' + params, {cache: 'no-store', signal: active.signal});
      const data = await response.json();
      if (request !== sequence) return;
      if (!response.ok) throw new Error(data.detail || '대기 분석을 확인하지 못했습니다.');
      const note = document.createElement('p');
      note.textContent = (data.historical ? '과거 종료일의 변경 점검은 현재 대기분을 이어받지 않습니다. ' : '') +
        '새 변경·전체 점검의 새 대상은 실행 후 확정됩니다. ' +
        `한 번에 최대 ${data.max_tasks}묶음을 처리하며, 실제 작업량과 사용량은 수집 결과·자료 크기·결과 재사용 여부에 따라 달라집니다.`;
      panel.replaceChildren(row('변경사항 점검', data.changes, data.labels), row('현재 이슈 전체 점검', data.audit, data.labels), note);
    } catch (error) {
      if (request === sequence) panel.textContent = error.name === 'AbortError' ? '대기 분석 조회가 지연됩니다. 날짜를 다시 선택해 확인해 주세요.' : error.message;
    } finally { clearTimeout(timeout); }
  }
  since.addEventListener('change', refresh);
  until.addEventListener('change', refresh);
  window.addEventListener('pagehide', () => { ++sequence; if (controller) controller.abort(); });
  refresh();
})();
