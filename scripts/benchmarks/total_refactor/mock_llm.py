"""Local OpenAI-compatible deterministic model; every completion waits five seconds.

Business graph/adapters/create_agent remain those of each selected Git commit.
No tool calls are invented, and unsupported prompts fail instead of calling out.
"""
import argparse, asyncio, json, time
from uuid import uuid4
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
import uvicorn

parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,required=True)
args=parser.parse_args();app=FastAPI();events=[];active=0;peak=0

def response_for(body):
    messages=body['messages']
    system='\n'.join(str(m.get('content','')) for m in messages if m['role'] in ('system','developer'))
    user=next((m['content'] for m in reversed(messages) if m['role']=='user'),'{}')
    try: payload=json.loads(user)
    except (ValueError,TypeError):payload={}
    if 'Classify the request and return exactly one label' in system:
        if '\nanalysis\nfaq\n' in system:return 'routing','analysis'
        if 'failure_prediction' in system:return 'intent','failure_prediction'
    if 'SkillSelectionOutput' in system:
        return 'skill_selection',json.dumps({'skill_names':['data_quality_check']})
    if 'WorkflowPlanOutput' in system or ('workflow_resources' in system and 'JSON Schema:' in system):
        plan={'plan_version':'1.0','workflow':{'id':'service_load_profile','name':'Service load test profile','description':'Deterministic five-second model API response.','goal':payload.get('user_request','불량 예측'),'status':'ready','context':payload.get('workflow_context',{}),'steps':[{'id':'profile_selected_data','skill':'data_quality_check','depends_on':['load_data_1'],'tools':[{'tool':name,'selection_reason':'Service comparison','arguments':{'data':{'source':'step_output','step_id':'load_data_1','output':'data'}}} for name in ('profile_data','compute_statistics','detect_outliers')]}],'outputs':{'profile':{'step_id':'profile_selected_data','tool':'profile_data','output':'profile'}}}}
        return 'workflow',json.dumps(plan,ensure_ascii=False)
    raise HTTPException(422,detail='Unsupported benchmark role; no external model fallback')

@app.get('/health')
async def health():return {'ready':True}
@app.post('/reset')
async def reset():
    global peak
    if active:raise HTTPException(409,'Models still active')
    events.clear();peak=0;return {'ok':True}
@app.get('/metrics')
async def metrics():return {'events':events,'active':active,'peak':peak,'delay_seconds':5}
@app.post('/v1/chat/completions')
async def complete(request:Request):
    global active,peak
    body=await request.json();role,content=response_for(body)
    began=time.perf_counter();active+=1;peak=max(peak,active);outcome='ok'
    try:await asyncio.sleep(5)
    except BaseException as exc:outcome=type(exc).__name__;raise
    finally:
        active-=1;events.append({'role':role,'started':began,'ms':(time.perf_counter()-began)*1000,'outcome':outcome,'stream':bool(body.get('stream')),'project_context_present':any('BENCH_PROJECT_CONTEXT' in str(message.get('content','')) for message in body['messages'])})
    base={'id':'chatcmpl-'+uuid4().hex,'created':int(time.time()),'model':'benchmark-5s'}
    if body.get('stream'):
        async def chunks():
            yield 'data: '+json.dumps({**base,'object':'chat.completion.chunk','choices':[{'index':0,'delta':{'role':'assistant','content':content},'finish_reason':None}]},ensure_ascii=False)+'\n\n'
            yield 'data: '+json.dumps({**base,'object':'chat.completion.chunk','choices':[{'index':0,'delta':{},'finish_reason':'stop'}]})+'\n\n'
            yield 'data: [DONE]\n\n'
        return StreamingResponse(chunks(),media_type='text/event-stream')
    return {**base,'object':'chat.completion','choices':[{'index':0,'message':{'role':'assistant','content':content},'finish_reason':'stop'}],'usage':{'prompt_tokens':100,'completion_tokens':100,'total_tokens':200}}
uvicorn.run(app,host='127.0.0.1',port=args.port,log_level='error')
