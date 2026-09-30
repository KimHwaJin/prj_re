"""Validate example bodies with an existing Executor checkout's request models.

Run with the Executor development Python (3.12+). This does not initialize the
service, read its environment settings or submit HTTP requests.
"""

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--executor-root', type=Path, required=True)
    parser.add_argument('--examples-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    arguments = parser.parse_args()
    if sys.version_info < (3, 12):
        parser.error('Use the Executor Python environment, version 3.12 or newer.')
    sys.dont_write_bytecode = True
    executor = arguments.executor_root.resolve()
    sys.path.insert(0, str(executor / 'src'))

    from pydantic import ValidationError
    from executor_service.interfaces.http.schemas import (
        ExecutionSubmitRequest, ExecutionOperationCreateRequest, ExecutionFinalizeRequest,
    )

    def read(name):
        return json.loads((arguments.examples_dir / name).read_text())

    checks = []
    for file, model in [
        ('executor-submit.request.json', ExecutionSubmitRequest),
        ('executor-operation.request.json', ExecutionOperationCreateRequest),
        ('executor-finalize.request.json', ExecutionFinalizeRequest),
    ]:
        model.model_validate(read(file))
        checks.append({'name': file, 'passed': True, 'model': model.__name__})

    submit, operation = read('executor-submit.request.json'), read('executor-operation.request.json')
    missing_wait = deepcopy(submit)
    missing_wait['lifecycle'].pop('operation_wait_timeout_seconds')
    wrong_wrapper = deepcopy(operation)
    wrong_wrapper['operation'] = wrong_wrapper.pop('spec')
    sequence_gap = deepcopy(submit)
    sequence_gap['operation']['spec']['steps'][1]['sequence'] = 9
    for name, model, body in [
        ('missing_multi_wait_timeout', ExecutionSubmitRequest, missing_wait),
        ('wrong_operation_wrapper', ExecutionOperationCreateRequest, wrong_wrapper),
        ('noncontiguous_sequences', ExecutionSubmitRequest, sequence_gap),
    ]:
        try:
            model.model_validate(body)
        except ValidationError:
            checks.append({'name': name, 'passed': True, 'expected_rejection': True})
        else:
            checks.append({'name': name, 'passed': False})

    source_files = ['src/executor_service/interfaces/http/schemas.py',
                    'src/executor_service/interfaces/_contracts/execution_inputs.py',
                    'src/executor_service/execution_specs.py']
    result = {'passed': all(item['passed'] for item in checks), 'python': sys.version.split()[0],
              'scope': 'actual current Executor Pydantic request models only; no service/API/DB/runtime validation',
              'source_sha256': {name: hashlib.sha256((executor / name).read_bytes()).hexdigest() for name in source_files},
              'checks': checks}
    if arguments.output:
        arguments.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'passed': result['passed'], 'checks': len(checks), 'python': result['python']}))
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
