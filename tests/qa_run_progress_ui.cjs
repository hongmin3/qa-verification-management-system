/* Validates: REQ-QAINTEL-021, REQ-QAINTEL-036, REQ-QAINTEL-042. 실제 공용 JS를 DOM·HTTP 경계에서 실행한다. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const scenario = process.argv[2];
const elements = Object.fromEntries(['qa-progress', 'qa-spinner', 'qa-progress-step', 'qa-progress-time',
  'qa-progress-link', 'qa-cancel', 'qa-cancel-message', 'run-now', 'audit-now', 'last-run', 'last-status'].map(id => [id, {
    id, hidden: id.startsWith('qa-'), _disabled: false, textContent: '', href: '', listeners: {}, dataset: {},
    get disabled() { return this._disabled; }, set disabled(value) { this._disabled = !!value; },
    addEventListener(name, callback) { this.listeners[name] = callback; }
  }]));
const startButtons = [elements['run-now'], elements['audit-now']];
elements['run-now'].textContent = '실행 중…';
let state = {running: true, current_run_id: 'fixture-run', cancel_requested: false,
  last_run: {id: 'fixture-run', status: 'RUNNING', status_label: '실행 중', started: '시작'},
  progress: {phase: 'ai', label: 'AI 분석', done: 4, total: 18, elapsed_seconds: 61}};
if (scenario === 'shared') state.cancel_requested = true;
if (scenario === 'email') state.progress = {...state.progress, phase: 'email', label: '메일'};
let requested = [], resolveCancel, timerId = 0;
const timers = new Map();
const statusEvents = [];
const context = {window: {addEventListener() {}}, document: {
  dispatchEvent(event) { statusEvents.push(event); },
  getElementById: id => elements[id], querySelectorAll: () => startButtons
}, CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } }, AbortController, Date, Promise, encodeURIComponent,
  setTimeout(callback, delay) { const id = ++timerId; timers.set(id, {callback, delay}); return id; },
  clearTimeout(id) { timers.delete(id); }, setInterval() { return 1; }, clearInterval() {},
  fetch: async (url, options = {}) => {
    if (url.startsWith('/qa-agent/status?')) return {ok: true, json: async () => JSON.parse(JSON.stringify(state))};
    requested.push({url, options});
    if (scenario === 'rejected') return {ok: false, status: 409, json: async () => ({detail: '실행 상태가 바뀌었습니다.'})};
    if (scenario === 'lost') throw new Error('연결 유실');
    return new Promise(resolve => { resolveCancel = resolve; });
  }};
vm.runInNewContext(fs.readFileSync('app/web/static/qa-run-progress.js', 'utf8'), context);
context.window.QARunProgress('VXvue');
const settle = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
async function poll() {
  const found = [...timers].find(([, value]) => value.delay === 3000);
  assert.ok(found, '상태를 계속 읽는 타이머가 있어야 한다');
  timers.delete(found[0]); await found[1].callback(); await settle();
}
(async () => {
  await settle();
  assert.equal(statusEvents[0].type, 'qa:status');
  assert.equal(statusEvents[0].detail.running, true);
  const cancel = elements['qa-cancel'];
  assert.equal(cancel.hidden, false, '진행 중 실행의 중단 버튼이 보여야 한다');
  assert.ok(startButtons.every(button => button.disabled));
  assert.equal(elements['qa-spinner'].hidden, false);
  if (scenario === 'email') {
    assert.equal(cancel.disabled, true, '이미 시작한 메일 발송은 중단 버튼으로 취소하지 않는다');
    assert.match(cancel.title, /메일/);
    cancel.listeners.click(); await settle();
    assert.equal(requested.length, 0);
    console.log('메일 단계 중단 비활성화 통과');
    return;
  }
  if (scenario === 'prepared') {
    // 대상 준비를 마친 실행은 기록이 아니라 대상 선택 화면(`상세 분석`)으로 안내한다.
    state = {...state, running: false, current_run_id: null,
      last_run: {...state.last_run, status: 'SUCCESS', status_label: '완료', result: '완료 · 대상 14건 준비', prepared_plan: true},
      progress: {...state.progress, phase: 'done', label: '완료', elapsed_seconds: 70}};
    await poll();
    assert.equal(elements['qa-spinner'].hidden, true);
    assert.equal(elements['qa-progress-link'].hidden, false);
    assert.equal(elements['qa-progress-link'].textContent, '상세 분석 열기');
    assert.equal(elements['qa-progress-link'].href, '/qa-agent/select?product=VXvue');
    console.log('준비 완료 뒤 상세 분석 열기 링크 통과');
    return;
  }
  if (scenario === 'shared') {
    assert.equal(cancel.disabled, true, '다른 사용자의 중단 요청도 표시해야 한다');
    assert.match(elements['qa-progress-step'].textContent, /중단/);
  } else {
    assert.equal(cancel.disabled, false);
    assert.equal(typeof cancel.listeners.click, 'function', '공통 중단 버튼에 호출이 연결되어야 한다');
    const operation = cancel.listeners.click();
    await settle();
    if (scenario === 'rejected' || scenario === 'lost') {
      await operation;
      assert.equal(cancel.disabled, false, '실패한 중단 요청은 다시 할 수 있어야 한다');
      assert.equal(elements['qa-cancel-message'].hidden, false);
      assert.ok(startButtons.every(button => button.disabled), '중단 요청 오류가 실행 완료를 뜻하지 않는다');
      assert.equal(elements['qa-spinner'].hidden, false);
      await poll();
    } else {
      assert.equal(cancel.disabled, true);
      cancel.listeners.click(); await settle();
      assert.equal(requested.length, 1, '연속 클릭으로 중단을 두 번 요청하지 않는다');
      assert.equal(requested[0].url, '/qa-agent/runs/fixture-run/cancel?product=VXvue');
      assert.equal(requested[0].options.method, 'POST');
      state.cancel_requested = true;
      resolveCancel({ok: true, status: 202, json: async () => ({status: 'cancelling', run_id: 'fixture-run'})});
      await operation;
    }
  }
  state = {...state, running: false, current_run_id: null, cancel_requested: false,
    last_run: {...state.last_run, status: 'CANCELLED', status_label: '사용자 중단', result: '사용자 중단 · 저장한 결과 1건'},
    progress: {...state.progress, phase: 'cancelled', label: '사용자 중단', elapsed_seconds: 70}};
  await poll();
  assert.equal(elements['qa-spinner'].hidden, true);
  assert.equal(cancel.hidden, true);
  assert.ok(startButtons.every(button => !button.disabled));
  assert.equal(elements['run-now'].textContent, '변경사항 점검하기', '다른 사람이 연 화면도 종료 후 시작 버튼 이름을 복구한다');
  assert.match(elements['qa-progress-step'].textContent, /사용자 중단/);
  assert.equal(elements['qa-progress-link'].hidden, false);
  assert.equal(elements['qa-progress-link'].href, '/qa-agent/runs/fixture-run');
  assert.equal(elements['qa-progress-link'].textContent, '점검 기록 보기', '준비 실행이 아니면 점검 기록으로 안내한다');
  console.log('진행 화면 중단 시나리오 통과: ' + scenario);
})().catch(error => { console.error(error); process.exitCode = 1; });
