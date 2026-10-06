"""Read shared sources and select fields declared by typed settings models.

There is no platform key allowlist. Unconsumed YAML keys remain platform-owned;
the check-config summary reports their names without logging their values.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping, get_origin, get_args

from pydantic import TypeAdapter, ValidationError
import yaml


class ConfigurationError(ValueError):
    """Public diagnostics include field names only, never supplied values."""


@dataclass(frozen=True)
class ModelBinding:
    name: str
    model: Any
    prefix: str = ""

    @property
    def fields(self):
        return (
            getattr(self.model, "model_fields", None)
            or self.model.__pydantic_fields__
        )

    @property
    def keys(self):
        result = {}
        for name, info in self.fields.items():
            if info.exclude:
                continue
            alias = info.validation_alias
            names = (
                tuple(alias.choices)
                if hasattr(alias, "choices")
                else (alias,)
                if isinstance(alias, str)
                else (self.prefix + name.upper(),)
            )
            result[name] = tuple(key.upper() for key in names)
        return result

    def inputs(self, values):
        return {
            name: values[keys[0]]
            for name, keys in self.keys.items()
            if keys[0] in values
        }

    def validate(self, values):
        parsed = dict(values)
        try:
            # Environment strings may encode collections. Native YAML values stay native.
            for name, value in parsed.items():
                annotation = self.fields[name].annotation
                if isinstance(value, bool) and annotation in (int, float):
                    raise ConfigurationError(
                        f"Invalid {self.name} settings: {self.keys[name][0]}"
                    )
                collection = get_origin(annotation) in (
                    tuple,
                    list,
                    dict,
                ) or any(
                    get_origin(arg) in (tuple, list, dict) or arg is dict
                    for arg in get_args(annotation)
                )
                if isinstance(value, str) and collection:
                    parsed[name] = json.loads(value)
            return TypeAdapter(self.model).validate_python(parsed)
        except (ValidationError, ValueError, TypeError) as error:
            if isinstance(error, ValidationError):
                names = sorted(
                    {
                        self.keys.get(
                            str(item["loc"][0]), (str(item["loc"][0]).upper(),)
                        )[0]
                        for item in error.errors()
                        if item["loc"]
                    }
                )
                detail = ": " + ", ".join(names) if names else ""
            else:
                detail = ""
            raise ConfigurationError(
                f"Invalid {self.name} settings{detail}"
            ) from None

    def defaults(self, instance):
        return {
            keys[0]: getattr(instance, name)
            for name, keys in self.keys.items()
        }


def source_aliases(bindings):
    """Derive names from the actual field definitions; no duplicate key catalog."""
    result = {}
    for binding in bindings:
        for names in binding.keys.values():
            for key in names:
                if key in result and result[key] != names[0]:
                    raise RuntimeError(
                        f"Conflicting setting declarations: {key}"
                    )
                result[key] = names[0]
    return result


def flat_values(document):
    result = {}
    for name, value in document.items():
        if not isinstance(name, str):
            raise ConfigurationError("Configuration keys must be strings")
        key = name.upper()
        if key == "SERVICE":
            raise ConfigurationError(
                "Nested service configuration is retired; use flat "
                "uppercase YAML "
                "keys"
            )
        if key in result:
            raise ConfigurationError(f"Duplicate setting: {key}")
        result[key] = value
    return result


def select_values(values, aliases):
    selected, unused = {}, []
    for raw, value in values.items():
        key = aliases.get(raw.upper())
        if key is None:
            unused.append(raw)
            continue
        if key in selected and selected[key] != value:
            raise ConfigurationError(f"Conflicting aliases for {key}")
        selected[key] = value
    return selected, unused


class UniqueKeyLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        self.flatten_mapping(node)
        seen = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str):
                raise ConfigurationError("Configuration keys must be strings")
            if key.upper() in seen:
                raise ConfigurationError(f"Duplicate YAML setting: {key}")
            seen.add(key.upper())
        return super().construct_mapping(node, deep=deep)


def read_yaml(path: Path) -> Mapping[str, Any]:
    try:
        document = yaml.load(
            path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader
        )
    except (OSError, yaml.YAMLError):
        raise ConfigurationError(
            f"Cannot read YAML configuration: {path.name}"
        ) from None
    if document is None:
        return {}
    if not isinstance(document, Mapping):
        raise ConfigurationError(
            f"Configuration must be a mapping: {path.name}"
        )
    return document


def read_local_env(path: Path):
    from dotenv import dotenv_values

    if not path.is_file():
        raise ConfigurationError("Explicit local dotenv file does not exist")
    return {
        key: value
        for key, value in dotenv_values(path, interpolate=False).items()
        if value is not None
    }
