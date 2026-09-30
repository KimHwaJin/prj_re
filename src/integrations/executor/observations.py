"""Bounded text-only observations with manifest and representation integrity checks."""
import hashlib
import json

from integrations.executor.manifest import _safe_resolve, _verified_bytes
from service_contracts.executor_manifest import StepResultManifest


def _text_preview(path, representation, max_chars):
    # Verify even ignored images, without materializing their Base64 in Agent state.
    checksum = hashlib.sha256()
    total = 0
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            checksum.update(chunk); total += len(chunk)
        if total != representation.size_bytes or checksum.hexdigest() != representation.checksum_sha256:
            raise ValueError('Executor output integrity mismatch')
        if representation.encoding != 'UTF8' or representation.media_type not in {'text/plain','application/json','text/markdown'}:
            return None
        stream.seek(0)
        first = stream.read(max_chars * 4)
        stream.seek(max(0, total - max_chars * 4))
        last = stream.read(max_chars * 4)
    # Generated observation is printed last; preserve the tail when user Tool prints a large log.
    first, last = first.decode('utf-8',errors='replace')[:max_chars], last.decode('utf-8',errors='replace')[-max_chars:]
    return {'preview':first,'tail':last,'truncated':total > max_chars * 4}


def read_operation_observations(settings, event, expected_steps):
    root = settings.executor_shared_result_root
    payload = event['payload']
    results = payload.get('step_results')
    if not isinstance(results,list):
        raise ValueError('Operation completion has no Step results')
    expected = {s['sequence']:s for s in expected_steps}
    if len(results) != len(expected) or {r['sequence'] for r in results} != expected.keys():
        raise ValueError('Operation results do not match submitted sequences')
    observations = []
    for result in results:
        sequence = result['sequence']; submitted = expected[sequence]
        if submitted.get('executor_step_id') and submitted['executor_step_id'] != result['step_id']:
            raise ValueError('Completed Step differs from submission receipt')
        logical_id = submitted['plan_step_id']
        observation = {'step_id':logical_id,'sequence':sequence,'status':result['status'],
                       'tool_id':submitted['tool_id'],'summary':None,'text':[],
                       'has_image':False,'incomplete':False,'error':result.get('error')}
        ref = result.get('result_ref')
        if not ref:
            if result['status'] == 'SUCCEEDED':
                raise ValueError('Successful Step has no immutable result reference')
            observation['incomplete'] = True; observations.append(observation); continue
        if ref.get('storage') != 'SHARED_PV' or not 0 <= ref['size_bytes'] <= 1024 * 1024:
            raise ValueError('Unsupported or oversized Executor manifest')
        path = _safe_resolve(root,ref['relative_path'])
        manifest = StepResultManifest.model_validate_json(_verified_bytes(path,
            expected_size=ref['size_bytes'],expected_sha256=ref['checksum_sha256']))
        identity = manifest.identity
        if (str(identity.execution_id) != event['execution_id'] or identity.sequence != sequence
                or str(identity.step_id) != result['step_id']
                or str(identity.operation_id) != payload['operation']['id']
                or str(identity.execution_attempt_id) != result['attempt']['id']
                or manifest.complete != ref['complete']
                or (ref.get('fencing_token') is not None and identity.fencing_token != ref['fencing_token'])):
            raise ValueError('Executor manifest identity or completeness mismatch')
        if result['status']=='SUCCEEDED' and manifest.state != 'FINALIZED':
            raise ValueError('Successful Step references a failed manifest')
        observation['incomplete'] = not manifest.complete
        observation['has_image'] = manifest.output_summary.has_image
        observation['error'] = observation['error'] or manifest.error_message
        observation['result_ref'] = ref
        max_chars = settings.agent_observation_max_chars
        for output in manifest.outputs:
            for rep in output.representations:
                content = _text_preview(_safe_resolve(root,rep.relative_path),rep,max_chars)
                if content is None:
                    continue
                # Keep a bounded aggregate, even when a Tool emits many output chunks.
                if sum(len(t) for t in observation['text']) < max_chars:
                    remaining = max_chars-sum(len(t) for t in observation['text'])
                    observation['text'].append(content['preview'][:remaining])
                for line in content['tail'].splitlines():
                    if not line.startswith('DTEST_OBSERVATION '):
                        continue
                    try:
                        parsed = json.loads(line[len('DTEST_OBSERVATION '):])
                        if parsed['step_id'] == logical_id and len(canonical_summary(parsed['summary']))<=max_chars:
                            observation['summary'] = parsed['summary']
                    except (ValueError,KeyError,TypeError):
                        continue
        observations.append(observation)
    return observations


def canonical_summary(value):
    return json.dumps(value,ensure_ascii=False,allow_nan=False)


def public_observations(observations):
    """Code/path-free facts for public API or role prompts; raw refs stay internal."""
    return [{**{k:obs[k] for k in ('step_id','tool_id','status','summary','has_image','incomplete')},
             'error':'Tool 실행 오류 — 내부 실행 기록에 상세 원인을 보존했습니다.' if obs.get('error') else None}
            for obs in observations]
