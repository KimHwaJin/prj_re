const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const {harness}=require('./dom-harness.cjs');
const html=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
const source=/<script id="console-app">([\s\S]*?)<\/script>/.exec(html)[1];
const box={module:{exports:{}}};vm.runInNewContext(source,box);const C=box.module.exports;
const plain=v=>JSON.parse(JSON.stringify(v));
test('SSE CRLF / arbitrary chunks / multiline JSON / heartbeat / snapshot cursor',()=>{
 const result=[],parse=C.makeSSEParser(f=>result.push(plain(f)));
 const payload=': heartbeat\r\n\r\nid: 7\r\nevent: message.completed\r\ndata: {"sequence":7,\r\ndata: "type":"message.completed"}\r\n\r\ndata: {"type":"run.snapshot","cursor":99}\n\n';
 for(const c of payload)parse(c);
 assert.equal(result.length,2);assert.equal(result[0].id,'7');assert.equal(result[0].data.sequence,7);assert.equal(result[1].id,null);
});
test('typed values preserve false/0/null and distinguish omission',()=>{
 assert.deepEqual(plain(C.readValue('false',{type:'boolean'},true)),{present:true,value:false});assert.equal(C.readValue('0',{type:'integer'}).value,0);
 assert.equal(C.readValue('null',{anyOf:[{type:'string'},{type:'null'}]}).value,null);assert.equal(C.readValue('',{type:'string'}).present,false);
 assert.throws(()=>C.readValue(' ',{type:'number'}));assert.throws(()=>C.readValue('1.2',{type:'integer'}));assert.throws(()=>C.readValue('',{},true));
});
test('plan edits omit untouched and read-only refs but keep exact plan revision',()=>{
 const command=C.planCommand({plan_id:'p',plan_revision:3},'approve_plan',[
 {scope:'input',name:'dataset',editable:true,dirty:false,raw:'x'},
 {scope:'step',step_id:'s',name:'data',editable:false,dirty:true,raw:'source'},
 {scope:'step',step_id:'s',name:'method',editable:true,dirty:true,raw:'"zscore"',schema:{enum:['iqr','zscore']}}
 ],['outliers'],{mode:'MULTI'});
 assert.deepEqual(plain(command),{action:'approve_plan',plan_id:'p',plan_revision:3,input_values:{},step_changes:[{step_id:'s',parameter:'method',value:'zscore'}],excluded_step_ids:['outliers'],execution_overrides:{mode:'MULTI'}});
});
test('trace redacts login/CSRF/resume secrets recursively',()=>{
 const safe=plain(C.redact({csrf_token:'secret',nested:{resume_token:'secret',Cookie:'secret'},run_id:'public'}));assert.equal(JSON.stringify(safe).includes('secret'),false);assert.equal(safe.run_id,'public');
});
test('HTML preview runs without fetch and edits then approves a plan',async()=>{
 const app=harness(html);try{assert.equal(app.ids.get('sendButton').disabled,true);assert.match(app.ids.get('review').textContent,/data_load/);
 const selects=app.ids.get('review').querySelectorAll('select');const method=selects.find(s=>s.children.some(o=>o.value==='"zscore"'));await app.field(method,'"zscore"');
 await app.find(app.ids.get('review'),'편집 반영').click();assert.match(app.ids.get('review').textContent,/계획 선택/);
 await app.find(app.ids.get('review'),'이 계획으로 승인').click();assert.equal(app.ids.get('sendButton').disabled,false);assert.match(app.ids.get('result').textContent,/서버 파일 등록 보류/);assert.match(app.ids.get('result').textContent,/가상 데이터/);
 }finally{app.close();}
});
test('HITL kinds render and runtime wait locks new input',async()=>{
 const app=harness(html);try{for(const kind of ['planning_question','decision_review','repair_review']){await app.all('[data-sample]').find(b=>b.dataset.sample===kind).click();assert.equal(app.ids.get('interactionBadge').textContent,kind);assert.equal(app.ids.get('sendButton').disabled,true);}
 await app.all('[data-sample]').find(b=>b.dataset.sample==='waiting_executor').click();assert.equal(app.ids.get('sendButton').disabled,true);assert.match(app.ids.get('inputHint').textContent,/Executor/);
 }finally{app.close();}
});
test('OpenAPI preview inventory contains current User/Project/Memory/Session/Run/Workflow/Message APIs',()=>{
 const fixtures=JSON.parse(/<script id="fixtures" type="application\/json">([\s\S]*?)<\/script>/.exec(html)[1]);const ops=C.operations(fixtures.openapi);
 for(const part of ['/users','/projects','/memory','/sessions','/runs','/diagnostics','/invocations','/logs','/workflows','/messages'])assert.ok(ops.some(o=>o.path.includes(part)),part);
 assert.ok(!ops.some(o=>o.path.includes('jupyter-servers')||o.path.includes('redis/ping')));
});

test('catalogue defaults are editable nullable arrays with labels and truthful origin',async()=>{
 const app=harness(html);try{
 const statistics=app.all('.step').find(card=>card.textContent.includes('compute_statistics'));
 assert.match(statistics.textContent,/분석할 컬럼/);assert.match(statistics.textContent,/함수 기본값/);
 const columns=statistics.querySelectorAll('textarea')[0];assert.equal(columns.value,'null');
 await app.field(columns,'["max_val"]');await app.find(app.ids.get('review'),'편집 반영').click();
 const updated=app.all('.step').find(card=>card.textContent.includes('compute_statistics'));
 assert.equal(updated.querySelectorAll('textarea')[0].value,'["max_val"]');
 }finally{app.close();}
});
