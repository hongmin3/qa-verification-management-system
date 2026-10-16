/* REQ-QAINTEL-021·036: 제품별 진행 기록과 중단 요청을 모든 요청 화면에서 공유한다. */
window.QARunProgress = function (product) {
  const panel = document.getElementById('qa-progress');
  const spinner = document.getElementById('qa-spinner');
  const step = document.getElementById('qa-progress-step');
  const time = document.getElementById('qa-progress-time');
  const link = document.getElementById('qa-progress-link');
  const cancelButton = document.getElementById('qa-cancel');
  const cancelMessage = document.getElementById('qa-cancel-message');
  const buttons = [...document.querySelectorAll('#run-now,#audit-now,#deferred-open,#deferred-start,button.on-demand,#manual-check')];
  let latest, pending = false, priorId, activeId, seen = false, pendingIdle = 0, elapsed = 0, fetched = Date.now(), timer, tick;
  let currentRunId = null, cancellingId = null, cancelPending = false;
  const started = () => { panel.hidden = false; spinner.hidden = false; step.textContent = '준비'; link.hidden = true; };
  const lock = value => buttons.forEach(button => {
    button.disabled = value;
    if (button.id === 'run-now') button.textContent = value ? '실행 중…' : button.dataset.idleLabel || '변경사항 점검하기';
  });
  function render(data) {
    document.dispatchEvent(new CustomEvent('qa:status', {detail: data}));
    latest = data;
    const last = data.last_run, progress = data.progress || {};
    currentRunId = data.running ? data.current_run_id || (last && last.status === 'RUNNING' ? last.id : null) : null;
    if (cancellingId && currentRunId !== cancellingId) cancellingId = null;
    const cancelling = !!(currentRunId && (cancellingId === currentRunId || data.cancel_requested || progress.cancel_requested));
    if (cancelling) cancellingId = currentRunId;
    cancelButton.hidden = !currentRunId;
    cancelButton.disabled = cancelPending || cancelling || progress.phase === 'email';
    cancelButton.textContent = cancelling ? '중단 중…' : '이번 점검 중단';
    cancelButton.title = progress.phase === 'email' ? '메일 발송을 이미 시작해 분석을 중단할 수 없습니다.' : '';
    if (last) {
      const startedAt = document.getElementById('last-run'), badge = document.getElementById('last-status');
      if (startedAt) startedAt.textContent = last.started;
      if (badge) { badge.textContent = last.status_label; badge.className = 'pill ' + last.status; }
    }
    if (data.running) { seen = true; activeId = last && last.status === 'RUNNING' ? last.id : activeId; }
    const abandoned = !data.running && last && last.status === 'RUNNING';
    if (data.running || (last && last.id !== priorId && !abandoned)) pendingIdle = 0;
    else if ((pending || seen || abandoned) && ++pendingIdle >= 10) {
      pending = false; lock(false); spinner.hidden = true; panel.hidden = false;
      seen = false; activeId = null;
      step.textContent = abandoned ? '점검이 계속 실행되는지 확인하지 못했습니다. 점검 기록 보기를 확인해 주세요.' : '실행 시작 기록을 확인하지 못했습니다. 상태를 확인한 뒤 다시 눌러 주세요.';
      time.textContent = ''; link.hidden = !last;
      link.textContent = '점검 기록 보기';
      if (last) link.href = '/qa-agent/runs/' + encodeURIComponent(last.id);
      return;
    }
    const newFinished = pending && last && last.id !== priorId && last.status !== 'RUNNING';
    const finished = !data.running && last && last.status !== 'RUNNING' && (newFinished || (seen && (!activeId || last.id === activeId)));
    if (finished) { pending = false; seen = false; activeId = null; }
    const running = !!data.running || pending;
    lock(running);
    if (running) {
      started();
      const current = data.running && last && last.status === 'RUNNING';
      step.textContent = cancelling ? '중단 요청 확인 중 · 저장한 결과를 정리합니다' : current ? (progress.label || '준비') + (progress.phase === 'ai' ? ` ${progress.done || 0}/${progress.total || 0}` : '') : '준비';
      elapsed = current ? progress.elapsed_seconds || 0 : 0; fetched = Date.now();
    } else if (finished) {
      panel.hidden = false; spinner.hidden = true;
      cancellingId = null; cancelMessage.hidden = true;
      step.textContent = last.result || last.status_label;
      elapsed = progress.elapsed_seconds || 0; fetched = Date.now();
      // 대상 준비를 마친 실행은 다음 할 일인 대상 선택 화면으로 안내한다 (REQ-QAINTEL-042).
      link.textContent = last.prepared_plan ? '상세 분석 열기' : '점검 기록 보기';
      link.href = last.prepared_plan ? '/qa-agent/select?product=' + encodeURIComponent(product) : '/qa-agent/runs/' + encodeURIComponent(last.id);
      link.hidden = false;
    }
    time.textContent = panel.hidden ? '' : `지난 시간 ${Math.floor(elapsed / 60)}분 ${elapsed % 60}초`;
  }
  async function poll() {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch('/qa-agent/status?product=' + encodeURIComponent(product), {signal: controller.signal, cache: 'no-store'});
      if (!response.ok) throw new Error('status');
      render(await response.json());
    } catch (_) {
      if (pending || seen) { panel.hidden = false; step.textContent = cancellingId ? '중단 상태 확인 중 · 다시 연결합니다' : '상태 연결 확인 중 · 다시 연결합니다'; }
    } finally { clearTimeout(timeout); clearTimeout(timer); timer = setTimeout(poll, 3000); }
  }
  async function cancel() {
    if (!currentRunId || cancelPending || cancelButton.disabled) return;
    const target = currentRunId;
    cancelPending = true; cancellingId = target;
    cancelButton.disabled = true; cancelButton.textContent = '중단 중…'; cancelMessage.hidden = true;
    step.textContent = '중단 요청 확인 중 · 저장한 결과를 정리합니다';
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch('/qa-agent/runs/' + encodeURIComponent(target) + '/cancel?product=' + encodeURIComponent(product),
        {method: 'POST', signal: controller.signal});
      const data = await response.json().catch(() => ({}));
      if (!((response.status === 202 && data.status === 'cancelling') || (response.status === 200 && data.status === 'CANCELLED'))) {
        throw new Error(data.detail || '이번 점검 중단 요청을 처리하지 못했습니다. 상태를 확인한 뒤 다시 눌러 주세요.');
      }
    } catch (error) {
      cancellingId = null;
      cancelMessage.textContent = error.name === 'AbortError' ? '중단 요청 응답을 확인하지 못했습니다. 서버 상태를 다시 읽습니다.' : error.message;
      cancelMessage.hidden = false;
    } finally {
      clearTimeout(timeout); cancelPending = false;
      await poll();
    }
  }
  cancelButton.addEventListener('click', cancel);
  const ready = poll();
  tick = setInterval(() => {
    if (!spinner.hidden && !panel.hidden) {
      const seconds = elapsed + Math.floor((Date.now() - fetched) / 1000);
      time.textContent = `지난 시간 ${Math.floor(seconds / 60)}분 ${seconds % 60}초`;
    }
  }, 1000);
  window.addEventListener('pagehide', () => { clearInterval(tick); clearTimeout(timer); });
  return {
    async launch(url, body, message) {
      lock(true); started();
      cancelMessage.hidden = true;
      try {
        await ready;
        if (!latest) throw new Error('상태 서버에 연결하지 못했습니다. 잠시 뒤 다시 눌러 주세요.');
        priorId = latest.last_run && latest.last_run.id; pending = true; pendingIdle = 0; seen = false; activeId = null;
        const response = await fetch(url, {method: 'POST', ...(body ? {body} : {})});
        const data = await response.json().catch(() => ({}));
        if (response.status === 202 || (response.status === 409 && data.status === 'running')) {
          if (message) message.textContent = data.warning || '실행을 시작했습니다.';
          await poll();
        } else {
          pending = false; spinner.hidden = true; lock(false);
          step.textContent = data.detail || '시작하지 못했습니다.';
        }
      } catch (error) {
        // 요청의 응답이 유실돼도 서버는 실행할 수 있다. 상태 조회로 끝까지 확인한다.
        if (pending) { step.textContent = '요청 응답 확인 중 · 서버 상태를 다시 읽습니다'; await poll(); }
        else { spinner.hidden = true; lock(false); step.textContent = error.message; }
      }
    }
  };
};
