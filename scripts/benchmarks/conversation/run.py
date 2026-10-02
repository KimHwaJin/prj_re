"""Replay a private, fixed conversation snapshot through production create_agent.

This measures the model/Agent boundary only, excluding API admission, DB and queue
wait. Input snapshots and model credentials stay in private files outside Git.
Run the same script with PYTHONPATH pointing to each archived source revision.
"""
import argparse
import asyncio
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import socket
import time

from langchain_core.callbacks import AsyncCallbackHandler

from service_settings import load_settings
from agent_service.agents.analysis.agent_builders.conversation.agent import build_agent
from agent_service.agents.analysis.dependencies import create_chat_model
from agent_service.agents.analysis.execution.grounding import completed_context, grounded_message
from agent_service.agents.analysis.planning.catalog import AssetCatalog
from agent_service.context import AgentContext


def write_private_json(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as file:
        os.fchmod(file.fileno(), 0o600)
        json.dump(value, file, ensure_ascii=False, indent=2)


class Meter(AsyncCallbackHandler):
    def __init__(self, validate, *, private_output=None):
        self.validate = validate
        self.starts = {}
        self.calls = []
        self.private_output = private_output
        self.private_responses = []

    async def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        self.starts[run_id] = (time.perf_counter(), sum(len(str(m.content)) for batch in messages for m in batch),
            any('Workflow definition JSON Schema' in str(m.content) for batch in messages for m in batch))

    async def on_llm_end(self, response, *, run_id, **kwargs):
        started, chars, planning = self.starts.pop(run_id)
        message = response.generations[0][0].message
        usage = message.usage_metadata or {}
        if self.private_output is not None:
            self.private_responses.append({'content':message.content, 'tools':[call['name'] for call in message.tool_calls]})
            write_private_json(self.private_output, self.private_responses)
        try:
            selected = json.loads(message.content)
        except (ValueError, TypeError):
            selected = {}
        row = {'planning_selection':isinstance(selected,dict) and selected.get('kind')=='planning','seconds':round(time.perf_counter()-started,4),'message_chars':chars,
               'planning_contract':planning,'tools':[call['name'] for call in message.tool_calls],
               'input_tokens':usage.get('input_tokens'),'output_tokens':usage.get('output_tokens')}
        if not message.tool_calls and isinstance(selected,dict) and selected.get('kind')=='answer':
            try:
                self.validate(message)
                row['validation']='passed'
            except ValueError as exc:
                reason=str(exc)
                row['validation']=('copied_numeric_text' if 'numeric values' in reason else
                    'absent_fact_path' if 'Fact path is absent' in reason else
                    'invalid_fact_id' if 'fact_ids' in reason else
                    'invalid_evidence_step' if 'Step' in reason else 'other_invalid_response')
        self.calls.append(row)
        print(json.dumps({'model_call':len(self.calls),**row}),flush=True)


async def main(args):
    config = json.loads(args.settings_file.read_text())
    config['MODEL_PROVIDER'] = 'openai_compatible'
    settings = load_settings(config=config,environ={}).agent
    model_spec = settings.model_catalog.resolve(settings.model_catalog.select().model_dump())
    agent_settings = model_spec.apply(settings)
    original = socket.getaddrinfo
    # Explicit --host-alias values belong to the caller's local test environment.
    aliases = dict(pair.split('=',1) for pair in args.host_alias)
    socket.getaddrinfo = lambda host,*a,**kw: original(aliases.get(host,host),*a,**kw)
    snapshot = json.loads(args.input_file.read_text())
    case = next(row for row in snapshot['cases'] if row['name']==args.case)
    owner = {'user_id':'benchmark-owner','project_id':'benchmark-project','session_id':snapshot['source_session']}
    record = {'schema_version':1,'owner':owner,'payload':case['analysis']}
    context = AgentContext(**owner,project_system_prompt=case.get('project_system_prompt',''),session_analysis_context=record)
    assert completed_context(context,settings.agent_session_analysis_max_chars)==case['analysis'], 'Evidence changed before replay'
    fingerprint = hashlib.sha256(json.dumps(case,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    agent = build_agent(create_chat_model(agent_settings),AssetCatalog(),
        max_candidates=settings.max_plan_candidates, discovery_max_rounds=settings.agent_discovery_max_rounds,
        repair_limit=settings.agent_repair_level_limit,repair_attempts=settings.agent_max_repair_attempts,
        session_context_max_chars=settings.agent_session_analysis_max_chars,
        structured_output_mode=model_spec.structured_output_mode)
    def validate(message):
        reply=agent.decode({'messages':[message]})
        grounded_message(reply,context,max_chars=settings.agent_session_analysis_max_chars)
    meter = Meter(validate, private_output=getattr(args,'private_responses_output',None))
    agent = replace(agent,agent=agent.agent.with_config(callbacks=[meter]))
    result = {'label':args.label,'case':args.case,'iteration':args.iteration,'input_sha256':fingerprint,
        'model':model_spec.model_name,'temperature':model_spec.temperature,'thinking':model_spec.enable_thinking,
        'measurement_scope':'production create_agent.ainvoke + grounding validation; no API/DB/Executor',
        'passed':False}
    started = time.perf_counter()
    try:
        reply = await asyncio.wait_for(agent.ainvoke(case['payload'],context=context),args.timeout)
        message = grounded_message(reply,context,max_chars=settings.agent_session_analysis_max_chars)
        assert reply.kind=='answer' and reply.grounding.scope=='analysis'
        assert reply.grounding.source_run_id==case['analysis']['source_run_id']
        result.update(passed=True,kind=reply.kind,fact_count=len(reply.grounding.facts)+len(getattr(reply.grounding,'fact_ids',[])),
            evidence_steps=reply.grounding.evidence_steps,answer_chars=len(message))
        if args.private_answer_output:
            write_private_json(args.private_answer_output, {'reply':reply.model_dump(),'rendered_message':message})
    except Exception as exc:
        # Error fragments can include private model output; expose only the type.
        result['error_type']=type(exc).__name__
    finally:
        result['seconds']=round(time.perf_counter()-started,4)
        result['calls']=meter.calls
        result['model_calls']=len(meter.calls)
        result['planning_selections']=sum(row['planning_selection'] for row in meter.calls)
        result['metadata_calls']=sum(bool(row['tools']) for row in meter.calls)
        result['model_seconds']=round(sum(row['seconds'] for row in meter.calls),4)
        result['input_tokens']=sum(row['input_tokens'] or 0 for row in meter.calls)
        result['output_tokens']=sum(row['output_tokens'] or 0 for row in meter.calls)
        result['tokens_available']=all(row['input_tokens'] is not None and row['output_tokens'] is not None for row in meter.calls)
        args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({k:v for k,v in result.items() if k!='calls'},ensure_ascii=False),flush=True)
    return 0 if result['passed'] else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--settings-file',required=True,type=Path)
    parser.add_argument('--input-file',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--private-answer-output',type=Path)
    parser.add_argument('--private-responses-output',type=Path,help='Opt-in private model responses, including rejected drafts; keep outside Git')
    parser.add_argument('--label',required=True)
    parser.add_argument('--case',required=True,choices=['explanation','report_revision'])
    parser.add_argument('--iteration',type=int,default=1)
    parser.add_argument('--timeout',type=float,default=300)
    parser.add_argument('--host-alias',action='append',default=[])
    raise SystemExit(asyncio.run(main(parser.parse_args())))
