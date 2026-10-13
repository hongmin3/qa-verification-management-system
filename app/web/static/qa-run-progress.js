/* REQ-QAINTEL-021: 제품별 서버 진행 기록을 모든 요청 화면에서 읽는다. */
window.QARunProgress = function (product) {
  const panel = document.getElementById('qa-progress');
  const spinner = document.getElementById('qa-spinner');
  const step = document.getElementById('qa-progress-step');
  const time = document.getElementById('qa-progress-time');
  const link = document.getElementById('qa-progress-link');
  const buttons = [...document.querySelectorAll('#run-now,#audit-now,button.on-demand,#manual-check')];
  let latest, pending = false, priorId, activeId, seen = false, pendingIdle = 0, elapsed = 0, fetched = Date.now(), timer, tick;
  const started = () => { panel.hidden = false; spinner.hidden = false; step.textContent = '준비'; link.hidden = true; };
  const lock = value => buttons.forEach(button => { button.disabled = value; });
  function render(data) {
    latest = data;
    const last = data.last_run, progress = data.progress || {};
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
      step.textContent = abandoned ? '점검이 계속 실행되는지 확인하지 못했습니다. 실행 상세를 확인해 주세요.' : '실행 시작 기록을 확인하지 못했습니다. 상태를 확인한 뒤 다시 눌러 주세요.';
      time.textContent = ''; link.hidden = !last;
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
      step.textContent = current ? (progress.label || '준비') + (progress.phase === 'ai' ? ` ${progress.done || 0}/${progress.total || 0}` : '') : '준비';
      elapsed = current ? progress.elapsed_seconds || 0 : 0; fetched = Date.now();
    } else if (finished) {
      panel.hidden = false; spinner.hidden = true;
      step.textContent = last.result || last.status_label;
      elapsed = progress.elapsed_seconds || 0; fetched = Date.now();
      link.href = '/qa-agent/runs/' + encodeURIComponent(last.id); link.hidden = false;
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
      if (pending || seen) { panel.hidden = false; step.textContent = '상태 연결 확인 중 · 다시 연결합니다'; }
    } finally { clearTimeout(timeout); clearTimeout(timer); timer = setTimeout(poll, 3000); }
  }
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
