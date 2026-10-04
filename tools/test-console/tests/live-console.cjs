// Executes the shipped controller with a development DOM double and real HTTP.
// Explicit opt-in, loopback diagnostic server only; this is not a browser screenshot test.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {harness}=require('./dom-harness.cjs');
const base=process.env.TEST_CONSOLE_API_URL;if(!base)throw Error('Set TEST_CONSOLE_API_URL to the loopback diagnostic app /api/v1');
if(!['localhost','127.0.0.1'].includes(new URL(base).hostname))throw Error('Loopback only');
const cookies=new Map(),calls=[];
async function http(url,options={}){
 const headers=new Headers(options.headers||{});if(cookies.size)headers.set('Cookie',[...cookies].map(([k,v])=>`${k}=${v}`).join('; '));
 const r=await fetch(url,{...options,headers});for(const line of r.headers.getSetCookie()){const [pair]=line.split(';');const pos=pair.indexOf('=');cookies.set(pair.slice(0,pos),pair.slice(pos+1));}
 calls.push({url,method:options.method||'GET',body:options.body,key:headers.get('Idempotency-Key'),status:r.status});return r;
}
const delay=ms=>new Promise(r=>setTimeout(r,ms));
async function until(predicate,why,timeout=30000){const t=Date.now();while(!predicate()){if(Date.now()-t>timeout)throw Error('Timeout: '+why);await delay(50);}}
const checks=[];function passed(name){checks.push(name);console.log('PASS '+name);}
const html=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
const storage=new Map();const app=harness(html,{base,fetch:http,storage});const id=name=>app.ids.get(name);
(async()=>{try{
 await until(()=>calls.some(c=>c.url.endsWith('/users/me')&&c.status===401),'unauthenticated boot');
 await id('loginButton').click();assert.match(app.window.location.assigned,/return_to=%2Ftest-console/);
 const login=await http(app.window.location.assigned,{redirect:'manual'});assert.equal(login.status,302);assert.equal(login.headers.get('location'),new URL(base).origin+'/test-console');
 await id('authRefresh').click();assert.match(id('identity').textContent,process.env.TEST_CONSOLE_EXPECT_ADMIN?/admin/:/user/);passed('SSO fixture login uses real cookie, CSRF and default project');
 await id('newProject').click();await app.field(id('editFields').querySelectorAll('input')[0],'HTML controller test');await id('editSubmit').click();
 await app.field(id('systemPrompt'),'보고서는 실제 실행 근거를 먼저 설명한다.');await id('saveProject').click();assert.match(id('projectSummary').textContent,/2/);
 await app.field(id('memoryText'),'## 보고서 선호\n핵심 결론과 근거를 먼저 보여준다.');await id('saveMemory').click();assert.equal(id('memoryVersion').textContent,'version 1');passed('project CRUD prompt and LangGraph Store memory save');
 await id('newSession').click();const f=id('editFields').querySelectorAll('input');await app.field(f[0],'HTML integration');await app.field(f[1],'default');await id('editSubmit').click();assert.equal(id('sendButton').disabled,false);
 await app.field(id('message'),'default-nce 품질과 통계를 확인하고 보고서를 작성해줘');await id('sendButton').click();await until(()=>id('interactionBadge').textContent==='plan_review'&&app.find(id('review'),'편집 반영')&&!app.find(id('review'),'편집 반영').disabled,'HITL readiness');
 assert.equal(id('sendButton').disabled,true);const firstRun=/Run ([0-9a-f-]+)/.exec(id('runMeta').textContent)[1];assert.match(id('runs').textContent,/waiting_input/);passed('session create and real GET SSE plan arrival with input lock and current list status');
 const method=id('review').querySelectorAll('select').find(s=>s.children.some(o=>o.value==='"zscore"'));await app.field(method,'"zscore"');
 const statistics=app.all('.step').find(card=>card.textContent.includes('compute_statistics'));
 const columns=statistics.querySelectorAll('textarea')[0];assert.equal(columns.value,'null');assert.match(statistics.textContent,/함수 기본값/);
 await app.field(columns,'["max_val"]');await app.find(id('review'),'편집 반영').click();
 await until(()=>id('interactionBadge').textContent==='plan_review'&&app.find(id('review'),'이 계획으로 승인')&&!app.find(id('review'),'이 계획으로 승인').disabled,'edited HITL readiness');
 const edit=JSON.parse(calls.findLast(c=>c.method==='POST'&&c.url.endsWith('/runs')).body);assert.equal(edit.run_id,firstRun);assert.equal(edit.command.resume.step_changes.find(c=>c.parameter==='method').value,'zscore');
 assert.deepEqual(edit.command.resume.step_changes.find(c=>c.step_id==='statistics'&&c.parameter==='columns').value,['max_val']);
 const editedCard=app.all('.step').find(card=>card.textContent.includes('compute_statistics'));assert.match(editedCard.textContent,/사용자가 설정/);assert.equal(editedCard.querySelectorAll('textarea')[0].value,'["max_val"]');
 passed('typed plan parameter edit keeps Run ID and latest token, catalogue defaults and user columns');
 const check=id('review').querySelectorAll('input').find(i=>i.type==='checkbox'&&i.parentElement.textContent.includes('detect_outliers'));check.checked=false;await check.fire('change');
 await app.find(id('review'),'이 계획으로 승인').click();await until(()=>id('resultStatus').textContent==='analysis_completed'&&!id('sendButton').disabled,'actual Executor completion',120000);
 assert.match(id('runs').textContent,/success/);assert.ok(!id('sessions').textContent.includes('busy'));assert.match(id('result').textContent,/SUCCEEDED/);assert.match(id('result').textContent,/사용자 제외: outliers/);assert.match(id('result').textContent,/서버 파일 등록 보류/);assert.ok(!id('result').textContent.includes('def data_load'));
 const selected=JSON.parse(storage.get('test-console:'+base));
 const finalRun=await(await http(base+'/sessions/'+selected.session+'/runs/'+firstRun)).json();
 const statisticalOutput=finalRun.result.final_response.observations.find(o=>o.step_id==='statistics');
 assert.deepEqual(Object.keys(statisticalOutput.summary.items.statistics.items),['max_val']);
 passed('actual Executor statistics output contains only the user-selected max_val column');
 const approvalCall=calls.findLast(c=>c.method==='POST'&&c.url.endsWith('/runs'));const approval=JSON.parse(approvalCall.body);assert.deepEqual(approval.command.resume.excluded_step_ids,['outliers']);passed('actual Executor analysis and finalize, report display and input unlock');
 await id('retryRequest').click();const retried=calls.findLast(c=>c.method==='POST'&&c.url.endsWith('/runs'));assert.equal(retried.key,approvalCall.key);assert.equal(retried.body,approvalCall.body);passed('same request retransmission preserves exact key and body');
 await id('streamDisconnect').click();await id('streamReconnect').click();await until(()=>id('streamStatus').textContent.includes('완료'),'SSE reconnect');passed('manual stream disconnect and durable cursor reconnect');
 await app.field(id('message'),'[answer] 방금 결과를 설명해줘');id('postStream').checked=true;await id('sendButton').click();assert.equal(id('sendButton').disabled,true);assert.match(id('runs').textContent,/pending|running/);await until(()=>id('resultStatus').textContent==='answer'&&!id('sendButton').disabled,'POST SSE answer');assert.ok(calls.some(c=>c.method==='POST'&&c.url.endsWith('/runs/stream')));await until(()=>id('streamStatus').textContent==='완료 · 스트림 종료','POST SSE terminal connection label');passed('POST SSE new Run locks input, updates lists and marks completed stream');
 await id('loadOpenapi').click();const option=id('apiOperation').children.find(o=>o.textContent==='GET /api/v1/users');assert.ok(option);id('apiOperation').value=option.value;await id('apiOperation').fire('change');await id('apiExecute').click();assert.equal(id('apiResponseStatus').textContent,process.env.TEST_CONSOLE_EXPECT_ADMIN?'성공':'HTTP 403');passed('OpenAPI explorer and real role enforcement');
 const saved=JSON.parse(storage.get('test-console:'+base));assert.deepEqual(Object.keys(saved).sort(),['project','run','session']);assert.ok(!JSON.stringify(saved).includes('token'));
 const me=await(await http(base+'/users/me')).json();
 const remote=await http(base+'/projects/'+saved.project+'/memory',{method:'PUT',headers:{'Content-Type':'application/json','X-CSRF-Token':me.csrf_token},body:JSON.stringify({content:'다른 탭의 메모리',expected_version:1})});assert.equal(remote.status,200);
 await app.field(id('memoryText'),'보존해야 할 편집 내용');await id('saveMemory').click();assert.equal(id('memoryText').value,'보존해야 할 편집 내용');assert.ok(!id('memoryConflict').classList.contains('hide'));assert.match(id('memoryConflict').textContent,/다른 탭/);passed('memory conflict preserves local edits and shows latest server document');
 await id('streamDisconnect').click();const restored=harness(html,{base,fetch:http,storage});try{await until(()=>restored.ids.get('runMeta').textContent.includes(saved.run),'selection restoration');assert.equal(restored.ids.get('chatTitle').textContent,'HTML integration');passed('page return restores project session and Run IDs without persisting tokens');}finally{await restored.ids.get('streamDisconnect').click();restored.close();}

 const out={passed:true,checks,mode:'Node controller / development DOM double / actual loopback API DB Worker Executor',browser_visual_verified:false};
 const output=process.env.TEST_CONSOLE_RESULT;if(output)fs.writeFileSync(output,JSON.stringify(out,null,2)+'\n',{mode:0o600});
 console.log(JSON.stringify({passed:true,checks:checks.length}));
 }catch(e){console.error('UI toast:',id('toast').textContent);throw e;}finally{await id('streamDisconnect').click();app.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
