"""Execution-local function structure and signature validation; never executes Python.

This is shared by pre-execution free plans and post-failure repairs. It is not
semantic equivalence validation or a Python sandbox.
"""

import ast
from hashlib import sha256

from dtest.contracts.plan_review import require


def source_info(code, *, expected=None):
    tree = ast.parse(code)
    require(
        len(tree.body) == 1 and isinstance(tree.body[0], ast.FunctionDef),
        "An execution-local source must contain exactly one ordinary function",
    )
    function = tree.body[0]
    require(
        not function.decorator_list and not function.args.posonlyargs,
        (
            "Execution-local functions must be keyword-callable without "
            "decorators"
        ),
    )
    if expected:
        old = ast.parse(expected["code"]).body[0]
        same_return = (
            ast.dump(function.returns) if function.returns else None
        ) == (ast.dump(old.returns) if old.returns else None)
        require(
            function.name == old.name
            and ast.dump(function.args) == ast.dump(old.args)
            and same_return,
            "Tool repair must preserve its signature",
        )
    else:
        # Definition-time expressions must not execute arbitrary code before the call.
        for value in [
            *function.args.defaults,
            *[v for v in function.args.kw_defaults if v is not None],
        ]:
            try:
                ast.literal_eval(value)
            except (ValueError, TypeError):
                raise ValueError(
                    "Custom function defaults must be literals"
                ) from None
        for annotation in [
            a.annotation
            for a in [
                *function.args.args,
                *function.args.kwonlyargs,
                *([function.args.vararg] if function.args.vararg else []),
                *([function.args.kwarg] if function.args.kwarg else []),
            ]
        ] + [function.returns]:
            require(
                annotation is None
                or isinstance(annotation, (ast.Name, ast.Constant))
                and (
                    isinstance(annotation, ast.Constant)
                    and isinstance(annotation.value, (str, type(None)))
                    or isinstance(annotation, ast.Name)
                    and annotation.id
                    in {
                        "str",
                        "int",
                        "float",
                        "bool",
                        "list",
                        "dict",
                        "tuple",
                        "object",
                    }
                ),
                "Custom annotations must not evaluate expressions",
            )
    if (
        function.body
        and isinstance(function.body[0], ast.Expr)
        and isinstance(function.body[0].value, ast.Constant)
        and isinstance(function.body[0].value.value, str)
    ):
        function.body.pop(0)
    require(
        bool(function.body), "Execution-local function has no executable body"
    )
    normalized = ast.unparse(ast.fix_missing_locations(tree)) + "\n"
    if expected:
        prior = ast.parse(expected["code"])
        previous = prior.body[0]
        if (
            previous.body
            and isinstance(previous.body[0], ast.Expr)
            and isinstance(previous.body[0].value, ast.Constant)
            and isinstance(previous.body[0].value.value, str)
        ):
            previous.body.pop(0)
        if ast.dump(prior) == ast.dump(tree):
            normalized = expected["code"]
    positional = function.args.args
    parameters = [a.arg for a in positional + function.args.kwonlyargs]
    required = [
        a.arg
        for a in positional[: len(positional) - len(function.args.defaults)]
    ]
    required += [
        a.arg
        for a, default in zip(
            function.args.kwonlyargs, function.args.kw_defaults
        )
        if default is None
    ]
    metadata = {
        "function_name": function.name,
        "description": "실행별 수정 함수",
        "signature": ast.unparse(function.args),
        "parameters": parameters,
        "required_parameters": required,
        "allows_extra_arguments": function.args.kwarg is not None,
    }
    return {
        "function_name": function.name,
        "code": normalized,
        "code_sha256": sha256(normalized.encode()).hexdigest(),
        "source_sha256": sha256(code.encode()).hexdigest(),
    }, metadata
