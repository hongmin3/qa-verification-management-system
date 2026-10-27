'use strict';
(async () => {
  const status = document.querySelector('#usage-status');
  const app = document.querySelector('#usage-app');
  let csrf = '', selected = '', offset = 0, lastPreview = '', stats = null;
  const labels = {documents:'문서 조회',specification:'사양·패키지 비교·추적',testcase:'TC 조회',issue:'이슈 검토',manual:'매뉴얼 검토',generator:'Generator',polarion:'Polarion',redmine:'Redmine',other:'기타',diagnostic:'진단',desktop:'Desktop 연결',http_mcp:'HTTP MCP',web_chat:'웹 채팅',owner_ui:'본인 선택 입력',IN_PROGRESS:'진행 중',ANSWER_RECORDED:'답변 기록(품질 미평가)',ANSWER_GENERATED:'응답 생성(품질 미평가)',INCOMPLETE:'미완료',FAILED:'실패',CANCELLED:'취소',success:'실행 성공',error:'실행 실패',used:'활용함',modified:'수정 후 활용',discarded:'활용 안 함',unrated:'평가 안 함'};
  const el = (tag, text, cls) => {const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(cls)node.className=cls;return node;};
  const by = id => document.getElementById(id);
  const say = text => {status.textContent=text;};
  async function api(path, options={}) {
    const response=await fetch(path,{credentials:'same-origin',cache:'no-store',...options,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf,...options.headers}});
    const data=await response.json();if(!response.ok)throw new Error(data.detail || '요청을 처리하지 못했습니다.');return data;
  }
  function table(target, headings, rows) {
    target.replaceChildren();const t=el('table'),head=el('tr');headings.forEach(v=>head.append(el('th',v)));t.append(head);
    rows.forEach(values=>{const tr=el('tr');values.forEach(v=>tr.append(el('td',v==null?'미집계':String(v))));t.append(tr);});target.append(t);
  }
  // This template is static. Every message, public note and server value uses textContent/value below.
  app.innerHTML=`<nav class="usage-nav"><a href="#usage-dashboard">월별 지표</a><a href="#usage-history">내 이력</a><a href="#usage-export">익명 내보내기</a><button id="logout">로그아웃</button></nav>
  <section id="usage-dashboard"><h2>월별 실행 지표</h2><p>한국 시간(KST) 기준입니다. 도구 실행 성공·답변 기록은 정확성이나 업무 완료 판정이 아닙니다. 실제 업무 ID가 없는 요청은 호출에만 포함합니다.</p><label>조회월 <select id="month"></select></label><div id="cards" class="usage-cards"></div><p id="health"></p><div id="feature-chart"></div><h3>채널·근거별 호출</h3><div id="monthly-table" class="usage-scroll"></div><h3>기록된 업무 상태</h3><div id="task-table"></div></section>
  <section id="usage-history"><h2>내 업무 이력 · 최근 365일</h2><form id="search-form"><label>검색 <input id="search" maxlength="200"></label><label>기능 <select id="feature-filter"><option value="">전체 기능</option></select></label><label>상태 <select id="state-filter"><option value="">전체 상태</option></select></label><label><input type="checkbox" id="bookmarks">대표 사례만</label><button>조회</button><button type="button" id="new-activity">새 선택 기록</button></form><div id="history-list"></div><button id="more">더 보기</button>
  <div class="usage-columns"><form id="activity-form"><h3 id="detail-title">새 선택 기록</h3><label>기능 <select id="feature"></select></label><label><input type="checkbox" id="save-original">이 업무의 질문·답변 원문 저장</label><p id="original-policy">선택하지 않으면 원문은 보관하지 않고 요약만 남깁니다.</p><label>원질문 <textarea id="question" maxlength="30000" rows="5"></textarea></label><label>최종 답변 또는 초안 <textarea id="answer" maxlength="30000" rows="7"></textarea></label><label>활용 요약(본인 전용) <textarea id="summary" maxlength="12000" rows="3"></textarea></label><label>기록 상태 <select id="record-state"><option value="ANSWER_RECORDED">답변 기록(품질 미평가)</option><option value="INCOMPLETE">미완료</option><option value="FAILED">실패</option><option value="CANCELLED">취소</option></select></label><button>선택한 기록 저장</button></form>
  <form id="feedback-form"><h3>활용 평가 · 대표 사례</h3><p>이력을 선택한 뒤 작성합니다. 원문과 공개용 설명은 별도로 관리합니다.</p><label><input type="checkbox" id="bookmark">대표 사례 책갈피</label><label>활용 평가 <select id="evaluation"><option value="unrated">평가 안 함</option><option value="used">활용함</option><option value="modified">수정 후 활용</option><option value="discarded">활용 안 함</option></select></label><label>사용자가 입력한 기준 시간(분) <input type="number" min="0" max="100000" step="0.1" id="baseline"></label><label>사용자가 입력한 실제 시간(분) <input type="number" min="0" max="100000" step="0.1" id="actual"></label><label>공개용 한 줄(선택 · 원문과 분리) <textarea id="portfolio-note" maxlength="1000" rows="3"></textarea></label><p>시간 차이는 입력한 표본의 차이이며 자동 절약 효과가 아닙니다.</p><button>평가 저장</button></form></div>
  <details><summary>현재 브라우저의 내 웹 대화 선택 연결</summary><p>먼저 <a href="/vxvue-chatbot" target="_blank" rel="noopener">같은 보호 연결에서 웹 채팅 열기</a>를 사용합니다. 이 연결은 현재 브라우저 소유 대화만 대상으로 하며 자동으로 원문을 복사하지 않습니다.</p><button id="bind-web">현재 브라우저 연결</button><button id="load-web">내 웹 작업 보기</button><div id="web-jobs"></div></details></section>
  <section id="usage-export"><h2>포트폴리오용 익명 내보내기</h2><p>원질문·답변·본인 활용 요약·연결 식별자는 포함하지 않습니다. 대표 사례는 책갈피를 선택한 기록만 제공합니다.</p><label><input type="checkbox" id="include-notes">직접 작성한 공개용 한 줄도 포함</label><button id="preview">마스킹 미리보기</button><a id="csv-download" hidden>검토한 CSV 내려받기</a><p id="export-notice"></p><div id="export-table" class="usage-scroll"></div></section>`;
  const features=['documents','specification','testcase','issue','manual','generator','polarion','redmine','other'];
  for(const id of ['feature','feature-filter'])features.forEach(v=>{const o=el('option',labels[v]);o.value=v;by(id).append(o);});
  ['IN_PROGRESS','ANSWER_RECORDED','ANSWER_GENERATED','INCOMPLETE','FAILED','CANCELLED'].forEach(v=>{const o=el('option',labels[v]);o.value=v;by('state-filter').append(o);});
  function selectedRows(rows) {const month=by('month').value;return rows.filter(r=>!month||r.month===month);}
  function renderDashboard() {
    const all=selectedRows(stats.monthly), rows=all.filter(r=>!r.diagnostic), taskRows=selectedRows(stats.task_states);
    const sum=key=>rows.reduce((a,r)=>a+r[key],0), calls=sum('calls'), errors=rows.filter(r=>r.status==='error').reduce((a,r)=>a+r.calls,0);
    const cards=[['기록된 도구 호출',calls],['기록된 업무 ID',taskRows.reduce((a,r)=>a+r.tasks,0)],['실행 실패 / 호출',`${errors} / ${calls}`],['재시도 호출(2회차 이상)',sum('retries')],['진단 호출',all.filter(r=>r.diagnostic).reduce((a,r)=>a+r.calls,0)],['평균 실행 지연(ms)',calls?Math.round(sum('duration_ms')/calls):'미집계'],['이달 확인된 가명 연결(사람 수 아님)',by('month').value===stats.active_connections_month?`${stats.active_connections} · 연결 미확인 호출 ${stats.unattributed_connection_calls}`:'해당 월 미집계'],['외부 AI 토큰·비용','미수집']];
    by('cards').replaceChildren(...cards.map(([label,value])=>{const box=el('article',undefined,'usage-card');box.append(el('span',label),el('strong',String(value)));return box;}));
    by('health').textContent=`계측 실패 ${stats.observation_health.failures}건. ${stats.observation_health.scope}`;
    const groups={};rows.forEach(r=>groups[r.feature]=(groups[r.feature]||0)+r.calls);const chart=by('feature-chart');chart.replaceChildren(el('h3','기능별 기록된 호출 수'));
    Object.entries(groups).sort((a,b)=>b[1]-a[1]).forEach(([feature,value])=>{const row=el('div',undefined,'usage-bar');const meter=el('progress');meter.max=calls||1;meter.value=value;row.append(el('span',labels[feature]||feature),meter,el('strong',String(value)));chart.append(row);});
    table(by('monthly-table'),['월(KST)','기능','채널','근거 종류','실행 상태','호출','재시도 호출','평균 ms'],rows.map(r=>[r.month,labels[r.feature]||r.feature,labels[r.channel]||r.channel,r.evidence,labels[r.status],r.calls,r.retries,Math.round(r.duration_ms/r.calls)]));
    table(by('task-table'),['월(KST)','채널','기능','상태','기록된 업무 ID'],taskRows.map(r=>[r.month,labels[r.channel],labels[r.feature],labels[r.status],r.tasks]));
  }
  async function dashboard() {
    stats=await api('/vxvue-usage/api/dashboard');const old=by('month').value;
    const months=[...new Set([stats.active_connections_month,...stats.monthly.map(r=>r.month),...stats.task_states.map(r=>r.month)])].sort().reverse();by('month').replaceChildren();
    months.forEach(v=>{const o=el('option',v);o.value=v;by('month').append(o);});const all=el('option','전체 보관 월(최대 36개월)');all.value='';by('month').append(all);
    by('month').value=months.includes(old)?old:stats.active_connections_month;renderDashboard();
  }
  async function history(append=false) {
    if(!append){offset=0;by('history-list').replaceChildren();}
    const params={search:by('search').value,feature:by('feature-filter').value,status:by('state-filter').value,bookmark:by('bookmarks').checked,offset,limit:50};
    const rows=await api('/vxvue-usage/api/activities/search',{method:'POST',body:JSON.stringify(params)});rows.forEach(row=>{const button=el('button',`${new Date(row.created_at).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'})} · ${labels[row.feature]||row.feature} · ${labels[row.status]}${row.bookmark?' · 대표 사례':''}${row.source==='test'?' · 시험':''}`,'usage-history-item');button.type='button';button.onclick=()=>guard(()=>detail(row.id));by('history-list').append(button);});offset+=rows.length;by('more').hidden=rows.length<50;
    if(!offset)by('history-list').append(el('p','선택 저장한 본인 기록이 없습니다. 팀 원문은 수집하지 않습니다.'));
  }
  async function detail(id) {
    const row=await api('/vxvue-usage/api/activities/'+encodeURIComponent(id));selected=id;by('detail-title').textContent='선택한 본인 기록';
    by('feature').value=row.feature;by('feature').disabled=true;by('question').value=row.question;by('question').disabled=true;by('answer').value=row.answer;by('summary').value=row.summary;
    by('save-original').checked=!!row.save_original;by('save-original').disabled=true;by('original-policy').textContent=row.save_original?'이 업무는 질문·답변 원문 저장을 선택했습니다.':'이 업무는 원문을 보관하지 않습니다. 요약과 상태만 저장합니다.';
    by('record-state').value=['ANSWER_RECORDED','INCOMPLETE','FAILED','CANCELLED'].includes(row.status)?row.status:'INCOMPLETE';
    by('bookmark').checked=!!row.bookmark;by('evaluation').value=row.evaluation;by('baseline').value=row.baseline_minutes??'';by('actual').value=row.actual_minutes??'';by('portfolio-note').value=row.portfolio_note;
  }
  async function guard(operation){try{await operation();}catch(error){say(error.message);}}
  by('month').onchange=renderDashboard;
  by('search-form').onsubmit=e=>{e.preventDefault();guard(()=>history());};
  by('more').onclick=()=>guard(()=>history(true));
  by('new-activity').onclick=()=>{selected='';by('activity-form').reset();by('feedback-form').reset();by('feature').disabled=by('question').disabled=by('save-original').disabled=false;by('detail-title').textContent='새 선택 기록';by('original-policy').textContent='선택하지 않으면 원문은 보관하지 않고 요약만 남깁니다.';};
  by('activity-form').onsubmit=e=>{e.preventDefault();guard(async()=>{
    if(!selected){const r=await api('/vxvue-usage/api/activities',{method:'POST',body:JSON.stringify({question:by('question').value,save_original:by('save-original').checked,feature:by('feature').value})});selected=r.activity_id;}
    await api(`/vxvue-usage/api/activities/${selected}/finish`,{method:'POST',body:JSON.stringify({answer:by('answer').value,summary:by('summary').value,status:by('record-state').value})});await detail(selected);await history();await dashboard();say('선택한 본인 기록을 저장했습니다.');
  });};
  by('feedback-form').onsubmit=e=>{e.preventDefault();guard(async()=>{if(!selected)throw new Error('먼저 이력을 선택하세요.');await api(`/vxvue-usage/api/activities/${selected}/feedback`,{method:'POST',body:JSON.stringify({bookmark:by('bookmark').checked,evaluation:by('evaluation').value,baseline_minutes:by('baseline').value===''?null:Number(by('baseline').value),actual_minutes:by('actual').value===''?null:Number(by('actual').value),portfolio_note:by('portfolio-note').value})});await history();say('활용 평가와 대표 사례 선택을 저장했습니다.');});};
  by('bind-web').onclick=()=>guard(async()=>{const link=await api('/vxvue-usage/api/web-ticket',{method:'POST'});await api('/vxvue-chatbot/usage-bind',{method:'POST',headers:{'X-VXvue-Owner-Ticket':link.ticket}});say('현재 브라우저의 본인 웹 대화를 연결했습니다.');});
  by('load-web').onclick=()=>guard(async()=>{const rows=await api('/vxvue-usage/api/web-jobs');by('web-jobs').replaceChildren();rows.forEach(row=>{const button=el('button',`${row.created_at} · ${row.status} · ${row.question}`);button.type='button';button.onclick=()=>guard(async()=>{const saved=await api('/vxvue-usage/api/web-import/'+encodeURIComponent(row.id),{method:'POST',body:JSON.stringify({save_original:by('save-original').checked,summary:by('summary').value})});await detail(saved.activity_id);await history();say('선택한 본인 웹 작업만 가져왔습니다.');});by('web-jobs').append(button);});if(!rows.length)by('web-jobs').append(el('p','현재 브라우저의 최근 웹 작업이 없습니다.'));});
  by('include-notes').onchange=()=>{lastPreview='';by('csv-download').hidden=true;};
  by('preview').onclick=()=>guard(async()=>{const value=await api('/vxvue-usage/api/export-preview?include_notes='+by('include-notes').checked);lastPreview=value.preview_id;table(by('export-table'),value.columns,value.rows.map(row=>value.columns.map(k=>row[k])));by('export-notice').textContent=value.notice;const link=by('csv-download');link.href='/vxvue-usage/export.csv?preview_id='+encodeURIComponent(lastPreview);link.hidden=false;say('내보낼 열과 행을 확인하세요. 미리보기는 10분 동안 유효합니다.');});
  by('logout').onclick=()=>guard(async()=>{await api('/vxvue-usage/logout',{method:'POST'});location.href='/vxvue-usage';});
  try{csrf=(await api('/vxvue-usage/api/session')).csrf;await dashboard();await history();say('본인 이력과 기록된 실행 지표를 불러왔습니다.');}catch(error){say(error.message);}
})();
