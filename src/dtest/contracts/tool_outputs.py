"""Named Tool outputs map to kernel-object selectors, never Python expressions."""

import ast
from copy import deepcopy
import re

ID = re.compile(r"^[a-z][a-z0-9_.-]{0,149}$")


def output_bindings(value):
    if not isinstance(value, dict) or not value:
        raise ValueError("Tool outputs must be a non-empty named map")
    result = {}
    for name, item in value.items():
        if not isinstance(name, str) or not ID.fullmatch(name):
            raise ValueError("Invalid Tool output name")
        if not isinstance(item, dict) or set(item) != {"selector"}:
            raise ValueError("Tool output requires only selector")
        selector = item["selector"]
        if not isinstance(selector, list) or any(
            not ((type(x) is str and x) or (type(x) is int and x >= 0))
            for x in selector
        ):
            raise ValueError(
                "Output selector must contain non-empty keys or "
                "non-negative "
                "indexes"
            )
        result[name] = {"selector": deepcopy(selector)}
    return result


def derived_outputs(returns):
    """Adapt AST return hints from the existing generator; no eval/name heuristic.

    Explicit registry outputs take precedence. Actual runtime shape still needs
    execution validation; AST hints cannot prove conditional/dynamic returns.
    """
    result = {}
    for name, item in returns["outputs"].items():
        # Python dict keys are not required to follow public alias ID syntax.
        # Do not reject an otherwise valid Tool or silently rename such a key.
        # Maintainers can declare an explicit alias selecting that exact key.
        if not ID.fullmatch(name):
            continue
        raw = item["selector"]
        if raw == "$":
            selector = []
        else:
            node = ast.parse("_result" + raw, mode="eval").body
            selector = []
            while isinstance(node, ast.Subscript):
                part = node.slice
                if not isinstance(part, ast.Constant) or not (
                    (type(part.value) is str and part.value)
                    or (type(part.value) is int and part.value >= 0)
                ):
                    raise ValueError("Unsupported derived output selector")
                selector.insert(0, part.value)
                node = node.value
            if not isinstance(node, ast.Name) or node.id != "_result":
                raise ValueError("Unsupported derived output selector")
        result[name] = {"selector": selector}
    return output_bindings(result or {"result": {"selector": []}})
