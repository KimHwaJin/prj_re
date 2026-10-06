"""Private YAML initialization/import and Compose exports, using the central loader."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

import yaml

from dtest.settings.loader import (
    ROOT,
    _read_yaml,
    load_settings,
    read_local_env,
)


def plain(value: Any) -> Any:
    from pydantic import SecretStr

    if isinstance(value, SecretStr):
        return value.get_secret_value()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    return value


def yaml_document(values: Mapping[str, Any]) -> dict:
    preferred = {
        "SERVER_PORT": "PORT",
        "MODEL_NAME": "PRIVATE_LLM_MODEL_NAME",
        "API_BASE_URL": "PRIVATE_LLM_ENDPOINT",
        "MODEL_API_KEY": "PRIVATE_LLM_API_KEY",
    }
    return {
        preferred.get(key, key): plain(value)
        for key, value in sorted(values.items())
        if key != "APP_ENV"
    }


def write_private(
    path: Path, content: str, *, overwrite: bool = False
) -> None:
    """Do not truncate an existing credential file; replace atomically only on request."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".config-", dir=path.parent)
    candidate = Path(temporary)
    try:
        os.fchmod(handle, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as output:
            output.write(content)
        if overwrite:
            os.replace(candidate, path)
        else:
            # Atomic create without replacing a file created concurrently.
            os.link(candidate, path)
    finally:
        candidate.unlink(missing_ok=True)


def initialize_profile(
    profile: str,
    *,
    root: Path = ROOT,
    source_env: Path | None = None,
    output: Path | None = None,
    overwrite: bool = False,
) -> Path:
    if profile not in {"local", "dev", "stg", "prd"}:
        raise ValueError("Choose local, dev, stg or prd")
    target = output or root / (
        "config.yml" if profile == "local" else f"config.{profile}.yml"
    )
    template = root / (
        "config.example.yml"
        if profile == "local"
        else f"config.{profile}.example.yml"
    )
    if source_env is None:
        # Preserve explanatory comments in the normal copy path.
        content = template.read_text(encoding="utf-8")
        load_settings(config=_read_yaml(template), environ={}, profile=profile)
    else:
        # Legacy dotenv is imported once, explicitly; the source file is untouched.
        # Env spelling/aliases/types are canonicalized and checked by the same loader.
        base = _read_yaml(template)
        imported = load_settings(
            config={}, environ=read_local_env(source_env), profile=profile
        )
        # Only explicit recognized legacy input overrides the target profile/defaults.
        explicit = {
            key: value
            for key, value in imported.inputs.items()
            if imported.sources.get(key) == "environment" and key != "APP_ENV"
        }
        # Normalize template aliases before applying imported canonical values.
        baseline = load_settings(config=base, environ={}, profile=profile)
        settings = load_settings(
            config={**baseline.inputs, **explicit}, environ={}, profile=profile
        )
        content = (
            "# Private YAML imported from legacy dotenv; source "
            "unchanged. Do not "
            "commit.\n"
        ) + yaml.safe_dump(
            {**base, **yaml_document(settings.inputs)},
            allow_unicode=True,
            sort_keys=False,
        )
    write_private(target, content, overwrite=overwrite)
    return target
