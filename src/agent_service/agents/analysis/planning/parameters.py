"""Read maintainer form policy and actual defaults without importing Tool code."""
import ast
from copy import deepcopy
import json

from jsonschema_rs import Draft202012Validator
from service_contracts.tool_parameters import ToolParameterControl
from service_contracts.workflow_validation import check_value_schema


def parameter_controls(function, declared):
    defaults = dict(zip([a.arg for a in function.args.args][-len(function.args.defaults):],
                        function.args.defaults)) if function.args.defaults else {}
    defaults.update({a.arg: default for a, default in zip(function.args.kwonlyargs, function.args.kw_defaults)
                     if default is not None})
    names = {a.arg for a in [*function.args.args, *function.args.kwonlyargs]}
    if not isinstance(declared, dict):
        raise ValueError('Tool parameter_controls must be a mapping')
    result = {}
    for name, raw in declared.items():
        if name not in names:
            raise ValueError(f'Unknown controlled Tool parameter: {name}')
        control = ToolParameterControl.model_validate(raw).model_dump()
        errors = []
        check_value_schema(control['value_schema'], name, errors)
        if errors:
            raise ValueError('; '.join(errors))
        control['has_default'] = name in defaults
        if name in defaults:
            try:
                # Roundtrip tuples as JSON arrays; never evaluate a Python call/name.
                value = json.loads(json.dumps(ast.literal_eval(defaults[name]), allow_nan=False))
            except (ValueError, TypeError, OverflowError):
                raise ValueError(f'Controlled Tool default must be literal JSON: {name}') from None
            if not Draft202012Validator(control['value_schema']).is_valid(value):
                raise ValueError(f'Tool default violates parameter schema: {name}')
            control['default'] = deepcopy(value)
        result[name] = control
    return result
