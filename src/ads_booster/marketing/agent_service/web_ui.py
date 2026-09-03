# ruff: noqa: E501
"""Run-centric browser projection for the on-premises Agent API."""

AGENT_RUN_UI = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Trace Marketing Agent</title>
<style>
:root{font:15px/1.5 system-ui,sans-serif;color:#18181b;background:#f4f4f5}
body{margin:0} header,main{max-width:1180px;margin:auto;padding:20px} header{display:flex;gap:12px;align-items:center}
h1{font-size:22px;margin-right:auto}.card{background:white;border:1px solid #ddd;border-radius:14px;padding:18px;margin-bottom:16px}
input,textarea,button{font:inherit}input,textarea{box-sizing:border-box;width:100%;padding:9px;border:1px solid #bbb;border-radius:8px}
textarea{min-height:80px}button{padding:9px 13px;border:0;border-radius:8px;background:#18181b;color:white;cursor:pointer}
.grid{display:grid;grid-template-columns:340px 1fr;gap:16px}.fields{display:grid;gap:10px}.muted{color:#71717a}.run{padding:10px;border-bottom:1px solid #eee;cursor:pointer}.run:hover{background:#fafafa}
.phase{display:inline-block;margin:3px;padding:4px 8px;border-radius:999px;background:#e4e4e7}.records{max-height:420px;overflow:auto}
pre{white-space:pre-wrap;word-break:break-word;background:#f4f4f5;padding:10px;border-radius:8px}.actions{display:flex;gap:8px;flex-wrap:wrap}
@media(max-width:760px){.grid{grid-template-columns:1fr}header{flex-wrap:wrap}}
</style></head><body>
<header><h1>Trace Marketing Agent</h1><span class="muted">canonical on-prem Agent Runs</span></header>
<main>
<section class="card fields"><label>서비스 토큰 <input id="token" type="password" autocomplete="off"></label>
<label>마케팅 목표 <textarea id="goal" placeholder="AI 잠금화면 기능의 터지는 Threads 포맷을 발굴한다"></textarea></label>
<label>성공 기준 <input id="criteria" value="근거가 연결된 다음 실험을 만든다"></label>
<div class="actions"><button id="create">Run 만들기</button><button id="refresh">새로고침</button></div><div id="message"></div></section>
<div class="grid"><section class="card"><h2>Runs</h2><div id="runs"></div></section>
<section class="card"><h2>Run journey</h2><div id="detail" class="muted">Run을 선택하세요.</div></section></div>
</main><script>
const $=id=>document.getElementById(id); const pathRun=location.pathname.match(/^\/runs\/([^/]+)$/); let selected=pathRun?decodeURIComponent(pathRun[1]):null;
async function api(path,options={}){const token=$('token').value;if(!token)throw Error('서비스 토큰을 입력하세요.');
 const response=await fetch(path,{...options,headers:{authorization:`Bearer ${token}`,'content-type':'application/json',...(options.headers||{})}});
 const body=await response.json();if(!response.ok)throw Error(body.error||`HTTP ${response.status}`);return body}
function esc(value){return String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function list(){try{const body=await api('/v1/runs');$('runs').innerHTML=body.runs.map(run=>`<div class="run" data-id="${esc(run.run_id)}"><b>${esc(run.goal.objective)}</b><br><span class="muted">${esc(run.state)} · rev ${run.revision}</span></div>`).join('')||'<span class="muted">아직 Run이 없습니다.</span>';
 document.querySelectorAll('.run').forEach(el=>el.onclick=()=>load(el.dataset.id))}catch(e){$('message').textContent=e.message}}
async function load(id){selected=id;try{const body=await api(`/v1/runs/${encodeURIComponent(id)}`);const run=body.run;
 const phases=body.steps.map(step=>`<span class="phase">${esc(step.kind)} · ${esc(step.state)}</span>`).join('');
 const records=body.records.map(record=>`<details><summary>${esc(record.kind)} · ${esc(record.payload_schema_version)}</summary><pre>${esc(JSON.stringify(record.payload,null,2))}</pre></details>`).join('');
 const approve=run.state==='awaiting_approval'?'<button id="approve">승인</button><button id="reject">거절</button>':'';
 const input=run.state==='awaiting_input'?'<textarea id="evidence" placeholder="추가 근거 JSON 또는 메모"></textarea><button id="submitInput">근거로 재개</button>':'';
 $('detail').innerHTML=`<h3>${esc(run.goal.objective)}</h3><p><b>${esc(run.state)}</b>${run.blocked_reason?' · '+esc(run.blocked_reason):''}</p><div>${phases}</div><h3>목표와 예산</h3><pre>${esc(JSON.stringify({goal:run.goal,budget:run.budget},null,2))}</pre><div class="actions">${approve}</div>${input}<h3>근거·전략·산출물·성과·학습</h3><div class="records">${records}</div>`;
 if($('approve'))$('approve').onclick=()=>approval('granted');if($('reject'))$('reject').onclick=()=>approval('rejected');if($('submitInput'))$('submitInput').onclick=submitInput;
 }catch(e){$('message').textContent=e.message}}
async function createRun(){try{const id=`run-${Date.now()}`;await api('/v1/runs',{method:'POST',body:JSON.stringify({run_id:id,goal:{objective:$('goal').value,success_criteria:[$('criteria').value],context:{}},budget:{max_tool_calls:8,max_cost_units:50}})});await list();await load(id)}catch(e){$('message').textContent=e.message}}
async function approval(decision){try{const expires_at=decision==='granted'?new Date(Date.now()+300000).toISOString():null;await api(`/v1/runs/${encodeURIComponent(selected)}/approval`,{method:'POST',body:JSON.stringify({decision,expires_at})});await load(selected);await list()}catch(e){$('message').textContent=e.message}}
async function submitInput(){try{let value=$('evidence').value;let evidence;try{evidence=JSON.parse(value)}catch{evidence={note:value}}await api(`/v1/runs/${encodeURIComponent(selected)}/input`,{method:'POST',body:JSON.stringify({evidence})});await load(selected);await list()}catch(e){$('message').textContent=e.message}}
$('create').onclick=createRun;$('refresh').onclick=list;list().then(()=>{if(selected)load(selected)});
</script></body></html>"""

__all__ = ["AGENT_RUN_UI"]
