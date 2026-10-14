(() => {
  'use strict';
  const $ = id => document.getElementById(id), base = '/vxvue-chatbot';
  let current = null, history = [], files = [], job = null, stream = null, timer = null, chat = null, selection = 0, retryTimer = null;
  const maxFileBytes = Number(document.querySelector('.vx-chat').dataset.maxFileBytes), fileMiB = Math.round(maxFileBytes/1024/1024);
  const escape = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const safeLink = url => { try { const u = new URL(url, location.origin); return ['http:','https:'].includes(u.protocol) ? escape(u.href) : '#'; } catch { return '#'; } };
  function inline(text) {
    return escape(text).replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>')
      .replace(/\[([^\]]+)\]\(([^\s)]+)\)/g, (_, title, url) => `<a href="${safeLink(url.replace(/&amp;/g,'&'))}" target="_blank" rel="noopener noreferrer">${title}</a>`);
  }
  function markdown(text) {
    const lines = text.split('\n'); let output = '', code = [], inCode = false, list = null;
    const closeList = () => { if(list) { output += `</${list}>`; list = null; } };
    for(let i=0;i<lines.length;i++) {
      const line = lines[i];
      if(line.startsWith('```')) { closeList(); if(inCode) { output += `<pre><code>${escape(code.join('\n'))}</code></pre>`; code=[]; } inCode=!inCode; continue; }
      if(inCode) { code.push(line); continue; }
      if(line.includes('|') && i+1<lines.length && /^\s*\|?\s*:?-{3,}/.test(lines[i+1])) {
        closeList(); const cells = l => l.trim().replace(/^\||\|$/g,'').split('|');
        output += '<div class="table-wrap"><table><thead><tr>'+cells(line).map(c=>`<th>${inline(c.trim())}</th>`).join('')+'</tr></thead><tbody>'; i++;
        while(i+1<lines.length && lines[i+1].includes('|') && lines[i+1].trim()) { i++; output += '<tr>'+cells(lines[i]).map(c=>`<td>${inline(c.trim())}</td>`).join('')+'</tr>'; }
        output += '</tbody></table></div>'; continue;
      }
      const heading = line.match(/^(#{1,4})\s+(.*)/), item = line.match(/^\s*(?:([-*])|\d+\.)\s+(.*)/);
      if(item) { const type=item[1]?'ul':'ol'; if(list!==type) { closeList(); output+=`<${type}>`; list=type; } output+=`<li>${inline(item[2])}</li>`; continue; }
      closeList();
      if(heading) output+=`<h${heading[1].length}>${inline(heading[2])}</h${heading[1].length}>`;
      else if(line.startsWith('>')) output+=`<blockquote>${inline(line.replace(/^>\s?/,''))}</blockquote>`;
      else if(line.trim()) output+=`<p>${inline(line)}</p>`;
    }
    closeList(); if(inCode) output+=`<pre><code>${escape(code.join('\n'))}</code></pre>`;
    return output;
  }
  async function api(path, options={}) {
    const response=await fetch(base+path,{credentials:'same-origin',cache:'no-store',...options});
    if(!response.ok) { let detail='요청을 처리하지 못했습니다.'; try { detail=(await response.json()).detail || detail; } catch {} throw new Error(typeof detail==='string'?detail:JSON.stringify(detail)); }
    return response.status===204?null:response.json();
  }
  function showError(error) { $('error-banner').textContent=error.message || String(error); $('error-banner').hidden=false; }
  function clearError(){ $('error-banner').hidden=true; }
  function sidebar(open){ $('sidebar').classList.toggle('open',open); $('sidebar-backdrop').hidden=!open; }
  function stopWatch(){ if(retryTimer)clearTimeout(retryTimer);retryTimer=null; if(stream) stream.close(); stream=null; if(timer) clearInterval(timer); timer=null; }
  function setBusy(value){ $('send-button').disabled=value; $('attach-button').disabled=value; }
  function renderFiles(){ $('attached-files').replaceChildren(); files.forEach((file,i)=>{ const node=document.createElement('div'); node.className='attached-file'; const name=document.createElement('span'); name.textContent=file.name; const remove=document.createElement('button'); remove.type='button'; remove.textContent='×'; remove.setAttribute('aria-label',file.name+' 제거'); remove.onclick=()=>{files.splice(i,1);renderFiles();}; node.append(name,remove);$('attached-files').append(node); }); $('file-count').textContent=files.length?`${files.length}/10개 · 파일당 ${fileMiB}MiB`:`최대 10개 · 파일당 ${fileMiB}MiB`; }
  function addFiles(items){ if(files.length+items.length>10){showError(new Error('첨부파일은 한 번에 최대 10개입니다.'));return;} if(items.some(f=>f.size>maxFileBytes)){showError(new Error(`파일당 ${fileMiB}MiB까지 첨부할 수 있습니다.`));return;} files.push(...items); renderFiles(); }
  async function refreshList(){ history=await api('/conversations');renderList(); }
  function renderList(){
    const nav=$('conversation-list');nav.replaceChildren(); const query=$('history-search').value.toLowerCase(); let group='';
    history.filter(c=>c.title.toLowerCase().includes(query)).forEach(c=>{
      const days=(Date.now()-new Date(c.updated_at).getTime())/86400000, label=days<1?'최근 대화':days<7?'지난 7일':'이전 대화';
      if(label!==group){ const el=document.createElement('div');el.className='history-group';el.textContent=label;nav.append(el);group=label; }
      const row=document.createElement('div');row.className='history-row'+(c.id===current?' active':'');
      const open=document.createElement('button');open.className='history-open';open.textContent=c.title;open.title=c.title;open.onclick=()=>select(c.id).catch(showError);
      const rename=document.createElement('button');rename.className='history-action';rename.textContent='✎';rename.title='제목 변경';rename.onclick=async()=>{const title=window.prompt('대화 제목',c.title);if(title?.trim()){try{await api(`/conversations/${c.id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:title.trim()})});await refreshList();if(current===c.id)$('chat-title').textContent=title.trim();}catch(e){showError(e);}}};
      const remove=document.createElement('button');remove.className='history-action';remove.textContent='×';remove.title='대화 삭제';remove.onclick=async()=>{if(!confirm('이 대화와 첨부파일을 삭제할까요?'))return;try{await api(`/conversations/${c.id}`,{method:'DELETE'});if(current===c.id)newChat();await refreshList();}catch(e){showError(e);}};
      row.append(open,rename,remove);nav.append(row);
    });
  }
  const JOB_STAGES=['대기 중','자료 검색 준비','자료 검색','원문 확인','답변 작성'];
  let reached=-1;
  function updateCard(card,active,compact){
    const index=JOB_STAGES.indexOf(active.stage);if(index>reached)reached=index;
    card.classList.toggle('compact',!!compact);card.querySelector('#job-stage').textContent=active.stage;
    card.querySelector('#job-time').textContent=Math.max(0,Math.floor((Date.now()-new Date(active.created_at).getTime())/1000))+'초';
    card.querySelectorAll('.job-steps li').forEach((li,i)=>{li.classList.toggle('current',li.dataset.stage===active.stage);li.classList.toggle('done',i<reached&&li.dataset.stage!==active.stage);});
  }
  function progressCard(active,compact){
    const card=$('job-progress-template').content.firstElementChild.cloneNode(true);
    const cancel=card.querySelector('.job-cancel');
    cancel.onclick=async()=>{cancel.disabled=true;cancel.textContent='중지 중';try{await api('/jobs/'+active.id+'/cancel',{method:'POST'});}catch(e){cancel.disabled=false;cancel.textContent='중지';showError(e);}};
    updateCard(card,active,compact);return card;
  }
  // 진행 중에는 카드 요소를 그대로 두고 나머지 메시지 부분만 바꾼다(스피너·중지 버튼 유지).
  function refreshRunningMessage(existing,message){
    const card=existing.querySelector('.job-progress');
    if(!card){existing.replaceWith(messageNode(message));return;}
    updateCard(card,job,!!message.content&&job.stage==='답변 작성');
    const fresh=messageNode(message,true),parts=[...fresh.children],label=parts.shift();
    [...existing.children].forEach(child=>{if(child!==card)child.remove();});
    existing.insertBefore(label,card);card.after(...parts);
  }
  function messageNode(message,withoutCard){
    const node=document.createElement('article');node.className='chat-message '+message.role;node.dataset.messageId=message.id;
    const label=document.createElement('div');label.className='message-label';label.innerHTML=message.role==='user'?'나':'<span class="mini-mark">V</span> VXvue AI';node.append(label);
    const running=message.role==='assistant'&&job&&job.assistant_message_id===message.id&&['QUEUED','RUNNING'].includes(job.status);
    if(running&&withoutCard!==true)node.append(progressCard(job,!!message.content&&job.stage==='답변 작성'));
    const body=document.createElement('div');body.className='message-body';if(message.role==='user')body.textContent=message.content;else body.innerHTML=markdown(message.content);if(!running||message.content)node.append(body);
    if(message.attachments?.length){ const list=document.createElement('div');list.className='message-files';message.attachments.forEach(a=>{const link=document.createElement('a');link.href=base+'/attachments/'+a.id;link.textContent='↧ '+a.name;list.append(link);});node.append(list); }
    if(message.role==='assistant' && message.content){
      const tools=document.createElement('div');tools.className='message-tools';const copy=document.createElement('button');copy.textContent='답변 복사';copy.onclick=async()=>{try{if(navigator.clipboard)await navigator.clipboard.writeText(message.content);else{const area=document.createElement('textarea');area.value=message.content;document.body.append(area);area.select();document.execCommand('copy');area.remove();}copy.textContent='복사됨';}catch(e){showError(new Error('복사하지 못했습니다. 답변 내용을 선택해 직접 복사해주세요.'));}};tools.append(copy);
      const task=chat?.jobs.find(j=>j.assistant_message_id===message.id);if(task){if(task.knowledge?.skill_version){const version=document.createElement('span');version.textContent='적용 Skill '+task.knowledge.skill_version+(task.knowledge.skill_code_review_needed?' · 도구 갱신 검토 필요':'');tools.append(version);}}node.append(tools);
      if(message.citations?.length){const detail=document.createElement('details');detail.className='citations';const summary=document.createElement('summary');summary.textContent=`조회한 근거 ${message.citations.length}개`;detail.append(summary);const list=document.createElement('ul');message.citations.forEach(c=>{const row=document.createElement('li'),link=document.createElement('a');link.className='citation-link';link.href=safeLink(c.reference_url);link.target='_blank';link.rel='noopener noreferrer';link.textContent=(c.document_name||c.document_id)+' · '+JSON.stringify(c.location||{});row.append(link);list.append(row);});detail.append(list);node.append(detail);}
    }return node;
  }
  function renderMessages(){ const scroll=$('scroll-area'),nearBottom=scroll.scrollHeight-scroll.scrollTop-scroll.clientHeight<180; $('messages').replaceChildren(...chat.messages.map(m=>messageNode(m)));$('welcome').hidden=chat.messages.length>0;if(nearBottom)scroll.scrollTop=scroll.scrollHeight; }
  async function select(id){ const ticket=++selection;stopWatch();clearError();files=[];renderFiles();const received=await api('/conversations/'+id);if(ticket!==selection)return;chat=received;current=id;localStorage.setItem('vxvue-last-chat',id);$('chat-title').textContent=chat.title;renderList();job=chat.jobs.find(j=>['QUEUED','RUNNING'].includes(j.status))||null;reached=-1;renderMessages();sidebar(false);setBusy(!!job);if(job)watch(job);$('scroll-area').scrollTop=$('scroll-area').scrollHeight; }
  function newChat(){ ++selection;stopWatch();current=null;chat=null;job=null;localStorage.removeItem('vxvue-last-chat');$('chat-title').textContent='새 대화';$('messages').replaceChildren();$('welcome').hidden=false;files=[];renderFiles();setBusy(false);renderList();sidebar(false);clearError();$('message-input').focus(); }
  function watch(value){
    job=value;setBusy(true);const started=new Date(value.created_at).getTime();
    timer=setInterval(()=>{const clock=$('job-time');if(clock)clock.textContent=Math.max(0,Math.floor((Date.now()-started)/1000))+'초';},1000);
    stream=new EventSource(base+'/jobs/'+value.id+'/events');
    stream.onmessage=event=>{
      const data=JSON.parse(event.data);job=data.job;chat.jobs=chat.jobs.map(saved=>saved.id===job.id?job:saved);
      const message=chat.messages.find(m=>m.id===job.assistant_message_id);if(message){message.content=data.message.content;message.citations=data.message.citations;const existing=$('messages').querySelector(`[data-message-id="${message.id}"]`);if(existing){const scroll=$('scroll-area'),near=scroll.scrollHeight-scroll.scrollTop-scroll.clientHeight<180;(job&&['QUEUED','RUNNING'].includes(job.status)?refreshRunningMessage(existing,message):existing.replaceWith(messageNode(message)));if(near)scroll.scrollTop=scroll.scrollHeight;}}
      if(!['QUEUED','RUNNING'].includes(job.status)){stopWatch();setBusy(false);job=null;refreshList().then(()=>{const c=history.find(x=>x.id===current);if(c)$('chat-title').textContent=c.title;}).catch(showError);knowledgeStatus();refreshUsage();}
    };
    stream.onerror=()=>{const ticket=selection;stopWatch();retryTimer=setTimeout(async()=>{if(current&&ticket===selection){try{await select(current);}catch(e){showError(e);setBusy(false);}}},2000);};
  }
  function knowledgeStatusPanel(state){
    const when=v=>v?new Date(v).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'}):'기록 없음';
    const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
    const spec=state.specification_sync||{},skill=state.skill||{},manuals=state.manuals||[];
    const specBad=['FAILED','PARTIAL'].includes(spec.status);
    const warn=!!(state.stale||skill.status==='error'||skill.code_review_needed||specBad||state.failed_documents?.length||manuals.some(m=>m.status==='failed')||(state.last_attempt&&state.last_attempt.status!=='SUCCESS'));
    const details=node('details','','knowledge-details');
    let saved='';try{saved=localStorage.getItem('vxvue-knowledge-open')||'';}catch{}
    details.open=saved==='1';
    details.addEventListener('toggle',()=>{try{localStorage.setItem('vxvue-knowledge-open',details.open?'1':'0');}catch{}});
    details.appendChild(node('summary',`서버 지식 ${state.documents}개 · 마지막 정상 동기화 ${state.last_success_at?when(state.last_success_at):'아직 정상 동기화 기록 없음'}${warn?' · 확인 필요':''}`));
    const body=node('div','','knowledge-body');
    const label={ready:'정상',failed:'읽기 실패',excluded:'제외'};
    body.appendChild(node('h4','매뉴얼'));
    if(manuals.length){
      const table=node('table','','knowledge-table'),head=table.createTHead().insertRow();
      ['이름','상태','마지막 업데이트'].forEach(t=>head.appendChild(node('th',t)));
      const rows=table.createTBody();
      manuals.forEach(m=>{const r=rows.insertRow(),name=r.insertCell(),st=r.insertCell(),upd=r.insertCell();
        name.textContent=m.name;st.textContent=label[m.status]||m.status;st.className='knowledge-'+m.status;
        if(m.reason){st.appendChild(document.createElement('br'));st.appendChild(node('small',m.reason));}
        upd.textContent=when(m.modified_at);});
      const wrap=node('div','','knowledge-table-wrap');wrap.appendChild(table);body.appendChild(wrap);
    }else body.appendChild(node('p','등록된 매뉴얼이 없습니다.','knowledge-empty'));
    body.appendChild(node('h4','사양서 자동 업데이트'));
    const specList=node('ul');
    [`일정: ${spec.schedule||'예약 정보 없음'}`,`마지막 성공: ${when(spec.last_success_at)}`,`마지막 시도: ${spec.last_attempt_at?when(spec.last_attempt_at)+' · '+(spec.status||''):'기록 없음'}`].forEach((t,i)=>{const li=node('li',t);if(i===2&&specBad)li.className='knowledge-bad';specList.appendChild(li);});
    body.appendChild(specList);
    body.appendChild(node('h4','로컬 Skill'));
    const skillList=node('ul');
    skillList.appendChild(node('li',skill.version?`최신 버전 ${skill.version} · 동기화 ${when(skill.synced_at)}`:(skill.status==='error'?'Skill 지침 읽기 실패':'Skill 지침 동기화 대기'),skill.status==='error'?'knowledge-bad':''));
    if(skill.code_review_needed)skillList.appendChild(node('li','도구 검토 필요: 검색 도구 코드가 검토한 판과 다릅니다.','knowledge-bad'));
    body.appendChild(skillList);
    const att=state.last_attempt;
    const otherFailed=(state.failed_documents||[]).filter(d=>d.kind!=='manual'),otherExcluded=(state.excluded||[]).filter(d=>d.kind!=='manual');
    if(att&&att.status!=='SUCCESS'||state.stale||otherFailed.length||otherExcluded.length){
      body.appendChild(node('h4','지식 동기화'));
      const syncList=node('ul');
      if(att&&att.status!=='SUCCESS')syncList.appendChild(node('li',`마지막 시도 ${att.status}${att.detail?' · '+att.detail:''}${state.next_retry_at?' · 다음 시도 '+when(state.next_retry_at):''}`,'knowledge-bad'));
      if(state.stale)syncList.appendChild(node('li','지식 최신화 확인 필요','knowledge-bad'));
      otherFailed.forEach(d=>syncList.appendChild(node('li',`읽기 실패: ${d.name}${d.reason?' · '+d.reason:''}`,'knowledge-bad')));
      otherExcluded.forEach(d=>syncList.appendChild(node('li',`제외: ${d.name}${d.reason?' ('+d.reason+')':''}`)));
      body.appendChild(syncList);
    }
    details.appendChild(body);
    return {details,warn};
  }
  async function knowledgeStatus(){
    const box=$('knowledge-status');
    try{
      const {details,warn}=knowledgeStatusPanel(await api('/knowledge-status'));
      box.replaceChildren(details);box.classList.toggle('warning',warn);
    }catch{box.textContent='지식 갱신 상태를 확인하지 못했습니다.';}
  }
  let usageTicket=0;
  function usageChip(name,w,u,label){
    const chip=document.querySelector(`.usage-chip[data-window="${name}"]`);if(!chip)return;
    chip.classList.remove('warn','empty','unknown');
    if(!w||typeof w.used_percent!=='number'){chip.textContent=label+' 사용량 알 수 없음';chip.classList.add('unknown');chip.title=(u&&u.error)||'사용량을 아직 알 수 없습니다';return;}
    chip.textContent=`${label} ${w.used_percent}%`+(w.resets_at?` · ${shortReset(w.resets_at)} 초기화`:'');
    const source={live:'지금 조회',last_run:'마지막 점검 값'}[u.source]||'';
    chip.title=[`Claude ${label} 한도 ${w.used_percent}% 사용 (${w.remaining_percent}% 남음)`,w.resets_at&&`${w.resets_at} 초기화`,source,u.measured_at&&`${u.measured_at} 측정`].filter(Boolean).join(' · ');
    if(w.remaining_percent<=0)chip.classList.add('empty');else if(w.remaining_percent<20)chip.classList.add('warn');
  }
  function shortReset(text){
    const m=/^(\d{4})-(\d{2})-(\d{2}) (\d{2}:\d{2})$/.exec(String(text));if(!m)return String(text);
    const today=new Date().toLocaleDateString('sv-SE',{timeZone:'Asia/Seoul'});
    return `${m[1]}-${m[2]}-${m[3]}`===today?m[4]:`${m[2]}/${m[3]} ${m[4]}`;
  }
  async function refreshUsage(){
    const ticket=++usageTicket;
    try{
      const response=await fetch('/claude/usage',{credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'}});
      if(!response.ok)throw new Error('사용량 조회 실패 ('+response.status+')');
      const usage=await response.json();if(ticket!==usageTicket)return;
      usageChip('five_hour',usage.five_hour,usage,'5시간');usageChip('seven_day',usage.seven_day,usage,'주간');
    }catch(error){if(ticket!==usageTicket)return;const u={error:error.message||'사용량을 불러오지 못했습니다'};usageChip('five_hour',null,u,'5시간');usageChip('seven_day',null,u,'주간');}
  }
  $('composer').onsubmit=async event=>{event.preventDefault();if(job)return;clearError();const text=$('message-input').value.trim();if(!text&&!files.length)return;$('send-button').disabled=true;try{if(!current){const made=await api('/conversations',{method:'POST'});current=made.id;}const data=new FormData();data.set('text',text);files.forEach(file=>data.append('files',file));await api('/conversations/'+current+'/messages',{method:'POST',body:data});$('message-input').value='';$('message-input').style.height='';files=[];renderFiles();await refreshList();await select(current);}catch(e){showError(e);setBusy(false);}};
  $('message-input').onkeydown=event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();$('composer').requestSubmit();}};
  $('message-input').oninput=()=>{const input=$('message-input');input.style.height='auto';input.style.height=Math.min(180,input.scrollHeight)+'px';};
  $('attach-button').onclick=()=>$('file-input').click();$('file-input').onchange=()=>{addFiles(Array.from($('file-input').files));$('file-input').value='';};
  $('composer').ondragover=event=>event.preventDefault();$('composer').ondrop=event=>{event.preventDefault();if(!job)addFiles(Array.from(event.dataTransfer.files));};
  $('new-chat').onclick=newChat;$('history-search').oninput=renderList;$('toggle-sidebar').onclick=()=>sidebar(!$('sidebar').classList.contains('open'));$('sidebar-backdrop').onclick=()=>sidebar(false);
  document.querySelectorAll('[data-prompt]').forEach(button=>button.onclick=()=>{$('message-input').value=button.dataset.prompt;$('message-input').focus();});
  refreshUsage();setInterval(()=>{if(!document.hidden)refreshUsage();},300000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshUsage();});
  (async()=>{try{await refreshList();await knowledgeStatus();const saved=localStorage.getItem('vxvue-last-chat');if(saved&&history.some(c=>c.id===saved))await select(saved);}catch(e){showError(e);}})();
})();
