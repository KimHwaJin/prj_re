"""Immutable model catalogue and non-secret durable references.

Only service_settings loads sources. Neither graph nodes nor resumes read env.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from hashlib import sha256
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError


class ModelSelectionError(ValueError):
    """Safe public error: contains no supplied settings or credentials."""


class ModelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    provider: Literal["mock", "openai_compatible"] = "openai_compatible"
    model_name: str = Field(min_length=1)
    api_base_url: str | None = None
    api_key: SecretStr | None = Field(default=None, repr=False)
    temperature: float = 0
    timeout_seconds: float = Field(default=60, gt=0)
    max_retries: int = Field(default=2, ge=0)
    enable_thinking: bool | None = None
    structured_output_mode: Literal["prompt_json", "provider_json_schema"] = (
        "prompt_json"
    )
    mock_delay_ms: int = Field(default=0, ge=0, le=60000)

    @property
    def revision(self):
        # Credentials may rotate without changing model identity. Endpoint and
        # request/decoding settings must remain compatible across continuations.
        data = self.model_dump(mode="json", exclude={"api_key"})
        return sha256(
            json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def apply(self, settings):
        return replace(
            settings,
            model_catalog=None,
            model_provider=self.provider,
            model_name=self.model_name,
            api_base_url=self.api_base_url,
            model_api_key=self.api_key.get_secret_value()
            if self.api_key
            else None,
            model_temperature=self.temperature,
            model_timeout_seconds=self.timeout_seconds,
            model_max_retries=self.max_retries,
            model_enable_thinking=self.enable_thinking,
            model_structured_output_mode=self.structured_output_mode,
            model_mock_delay_ms=self.mock_delay_ms,
        )


class ModelSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(
        min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_.-]+$"
    )
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")


@dataclass(frozen=True, repr=False)
class ModelCatalog:
    models: Mapping[str, ModelSpec]
    default: str

    def select(self, name=None):
        alias = self.default if name is None else name
        spec = self.models.get(alias)
        if spec is None:
            raise ModelSelectionError("Unknown main_model_name.")
        return ModelSelection(name=alias, revision=spec.revision)

    def resolve(self, reference):
        try:
            selected = ModelSelection.model_validate(reference)
        except (ValidationError, TypeError):
            raise ModelSelectionError(
                "Run has no valid model selection; explicit "
                "recovery is "
                "required."
            ) from None
        spec = self.models.get(selected.name)
        if spec is None or spec.revision != selected.revision:
            raise ModelSelectionError(
                "Run model is unavailable or its configuration "
                "changed; restore the pinned "
                "configuration."
            )
        return spec


def build_catalog(settings, entries=None, default=None):
    try:
        if entries is None:
            specs = {
                "default": ModelSpec(
                    provider=settings.model_provider,
                    model_name=settings.model_name or "default",
                    api_base_url=settings.api_base_url,
                    api_key=settings.model_api_key,
                    temperature=settings.model_temperature,
                    timeout_seconds=settings.model_timeout_seconds,
                    max_retries=settings.model_max_retries,
                    enable_thinking=settings.model_enable_thinking,
                    structured_output_mode=settings.model_structured_output_mode,
                    mock_delay_ms=settings.model_mock_delay_ms,
                )
            }
        else:
            if isinstance(entries, str):
                entries = json.loads(entries)
            if not isinstance(entries, dict) or not entries:
                raise ValueError()
            specs = {
                name: ModelSpec.model_validate(value)
                for name, value in entries.items()
            }
            for name, spec in specs.items():
                ModelSelection(name=name, revision=spec.revision)
                if spec.provider == "openai_compatible" and (
                    not spec.api_base_url or not spec.api_key
                ):
                    raise ValueError()
        catalog = ModelCatalog(
            MappingProxyType(specs), "default" if default is None else default
        )
        catalog.select()
        return catalog
    except (ValueError, TypeError):
        raise ModelSelectionError(
            "Invalid MODEL_CATALOG or DEFAULT_MODEL; check aliases "
            "and model settings."
        ) from None


def current_catalog():
    from dtest.settings.agent import load_agent_settings

    settings = load_agent_settings()
    return settings.model_catalog or build_catalog(settings)


def validate_checkpoint_selection(values, expected=None):
    reference = values.get("model_selection")
    current_catalog().resolve(reference)
    if expected is not None and reference != expected:
        raise ModelSelectionError(
            "Checkpoint model does not match the Run model selection."
        )
