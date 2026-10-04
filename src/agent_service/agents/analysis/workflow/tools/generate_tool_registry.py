"""Generate tool_registry.yaml from Python Tool files using AST only."""

from __future__ import annotations

import argparse
import ast
import inspect
import sys
from pathlib import Path
from typing import Any

import yaml


class LiteralString(str):
    """YAML string rendered with the literal block style."""


class RegistryDumper(yaml.SafeDumper):
    def ignore_aliases(self, data):
        # Keep each Tool policy independently editable in generated YAML.
        return True


def _represent_literal(
    dumper: RegistryDumper,
    value: LiteralString,
) -> yaml.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|")


RegistryDumper.add_representer(LiteralString, _represent_literal)

EXCLUDED_FILES = {"__init__.py", Path(__file__).name}
PACKAGE_NAMES = {"PIL": "Pillow", "sklearn": "scikit-learn"}
MISSING = object()


def _value(node: ast.AST | object) -> Any:
    if node is MISSING:
        return None
    try:
        value = ast.literal_eval(node)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return ast.unparse(node)  # type: ignore[arg-type]
    return list(value) if isinstance(value, tuple) else value


def _annotation(node: ast.AST | None) -> str | None:
    return ast.unparse(node) if node is not None else None


def _tool_function(tree: ast.Module, expected_name: str) -> ast.FunctionDef | None:
    return next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == expected_name
        ),
        None,
    )


def _parameters(function: ast.FunctionDef) -> dict[str, dict[str, Any]]:
    positional = [
        (argument, "positional_only")
        for argument in function.args.posonlyargs
    ] + [
        (argument, "positional_or_keyword")
        for argument in function.args.args
    ]
    defaults = [MISSING] * (
        len(positional) - len(function.args.defaults)
    ) + list(function.args.defaults)

    parameters: dict[str, dict[str, Any]] = {}
    for (argument, kind), default in zip(positional, defaults):
        parameters[argument.arg] = {
            "type": _annotation(argument.annotation),
            "kind": kind,
            "has_default": default is not MISSING,
            "default": _value(default),
        }

    if function.args.vararg is not None:
        argument = function.args.vararg
        parameters[argument.arg] = {
            "type": _annotation(argument.annotation),
            "kind": "var_positional",
            "has_default": False,
            "default": None,
        }

    for argument, default in zip(
        function.args.kwonlyargs,
        function.args.kw_defaults,
    ):
        actual_default = default if default is not None else MISSING
        parameters[argument.arg] = {
            "type": _annotation(argument.annotation),
            "kind": "keyword_only",
            "has_default": actual_default is not MISSING,
            "default": _value(actual_default),
        }

    if function.args.kwarg is not None:
        argument = function.args.kwarg
        parameters[argument.arg] = {
            "type": _annotation(argument.annotation),
            "kind": "var_keyword",
            "has_default": False,
            "default": None,
        }
    return parameters


class BodyVisitor(ast.NodeVisitor):
    """Visit a Tool body without entering nested functions or classes."""

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return None

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return None

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return None


class ReturnVisitor(BodyVisitor):
    def __init__(self) -> None:
        self.returns: list[ast.AST] = []
        self.assigned_dict_keys: dict[str, list[str]] = {}

    @staticmethod
    def dict_keys(node: ast.Dict) -> list[str]:
        return [
            key.value
            for key in node.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        ]

    def visit_Assign(self, node: ast.Assign) -> None:
        if isinstance(node.value, ast.Dict):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.assigned_dict_keys[target.id] = self.dict_keys(node.value)
        for target in node.targets:
            if (
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Name)
                and isinstance(target.slice, ast.Constant)
                and isinstance(target.slice.value, str)
            ):
                self.assigned_dict_keys.setdefault(target.value.id, []).append(
                    target.slice.value
                )
        self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> None:
        if node.value is not None:
            self.returns.append(node.value)

    def keys(self) -> list[str]:
        keys: list[str] = []
        for node in self.returns:
            if isinstance(node, ast.Dict):
                keys.extend(self.dict_keys(node))
            elif isinstance(node, ast.Name):
                keys.extend(self.assigned_dict_keys.get(node.id, []))
        return list(dict.fromkeys(keys))


class ImportVisitor(BodyVisitor):
    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_Import(self, node: ast.Import) -> None:
        self.names.update(alias.name.split(".")[0] for alias in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self.names.add(node.module.split(".")[0])


def _visit_body(function: ast.FunctionDef, visitor: ast.NodeVisitor) -> None:
    for statement in function.body:
        visitor.visit(statement)


def _returns(function: ast.FunctionDef) -> dict[str, Any]:
    visitor = ReturnVisitor()
    _visit_body(function, visitor)
    keys = visitor.keys()
    outputs = (
        {
            key: {"selector": f'["{key}"]'}
            for key in keys
        }
        if keys
        else {"result": {"selector": "$"}}
    )
    return {
        "type": _annotation(function.returns),
        "outputs": outputs,
    }


def _packages(function: ast.FunctionDef) -> list[str]:
    visitor = ImportVisitor()
    _visit_body(function, visitor)
    return sorted(
        {
            PACKAGE_NAMES.get(name, name)
            for name in visitor.names
            if name not in sys.stdlib_module_names
        }
    )


def _entry(path: Path, root: Path, function: ast.FunctionDef) -> dict[str, Any]:
    relative = path.relative_to(root)
    raw_docstring = ast.get_docstring(function, clean=False) or ""
    docstring = LiteralString(inspect.cleandoc(raw_docstring))
    signature = f"{function.name}({ast.unparse(function.args)})"
    if function.returns is not None:
        signature += f" -> {ast.unparse(function.returns)}"
    return {
        "category": relative.parent.as_posix(),
        "function_name": function.name,
        "source": relative.as_posix(),
        "signature": signature,
        "docstring": docstring,
        "inputs": _parameters(function),
        "returns": _returns(function),
        "packages": _packages(function),
    }


def build_registry(root: Path) -> dict[str, Any]:
    tools: dict[str, Any] = {}
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if (
            path.name in EXCLUDED_FILES
            or "past" in relative.parts
            or "tmp" in relative.parts
            or "__pycache__" in relative.parts
        ):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        function = _tool_function(tree, path.stem)
        if function is None:
            continue
        if path.stem in tools:
            raise ValueError(f"Duplicate Tool id: {path.stem!r}")
        tools[path.stem] = _entry(path, root, function)

    # Availability is a deliberate maintainer policy, not inferable from AST.
    # Preserve it when refreshing signatures/docstrings, including --output to
    # a scratch location. Newly registered Tools default to ready.
    previous_path = root / 'tool_registry.yaml'
    if previous_path.is_file():
        previous = yaml.safe_load(previous_path.read_text(encoding='utf-8')) or {}
        for key, item in tools.items():
            status = previous.get('tools', {}).get(key, {}).get('availability')
            if status is not None:
                if status not in {'ready', 'test_only'}:
                    raise ValueError('Invalid Tool availability')
                item['availability'] = status
            controls = previous.get('tools', {}).get(key, {}).get('parameter_controls')
            if controls is not None:
                # UI policy is authored, not inferred from annotation/docstring.
                from agent_service.agents.analysis.planning.parameters import parameter_controls
                tree = ast.parse((root / item['source']).read_text(encoding='utf-8'))
                parameter_controls(_tool_function(tree, item['function_name']), controls)
                item['parameter_controls'] = controls

    return {
        "schema_version": "2.0",
        "registry_type": "tool_registry",
        "description": (
            "Python Tool 파일에서 AST로 추출한 함수 호출 정보다. "
            "Registry에 등록된 Tool은 모두 Workflow에서 사용할 수 있다."
        ),
        "generation": {
            "method": "python_ast",
            "llm_used": False,
            "source_root": "agent_service/agents/analysis/workflow/tools",
        },
        "tools": tools,
    }


def write_registry(registry: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        yaml.dump(
            registry,
            Dumper=RegistryDumper,
            allow_unicode=True,
            sort_keys=False,
            width=100,
        ),
        encoding="utf-8",
    )
    temporary.replace(output)


def main() -> None:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools-dir", type=Path, default=root)
    parser.add_argument(
        "--output",
        type=Path,
        default=root / "tool_registry.yaml",
    )
    arguments = parser.parse_args()
    registry = build_registry(arguments.tools_dir.resolve())
    write_registry(registry, arguments.output.resolve())
    print(f"generated {len(registry['tools'])} tools: {arguments.output.resolve()}")


if __name__ == "__main__":
    # Direct script invocation must read contracts from this checkout too.
    sys.path.insert(0, str(Path(__file__).resolve().parents[5]))
    main()
