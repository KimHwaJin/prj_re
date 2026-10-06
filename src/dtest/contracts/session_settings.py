"""Session kernel selection; independent of Run models and execution policies."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

KernelProfile = Annotated[
    str,
    Field(
        strict=True, min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_.-]+$"
    ),
]
KERNEL_PROFILE = TypeAdapter(KernelProfile)


class SessionSettings(BaseModel):
    """Only the kernel is session-scoped; null/omission selects the service default."""

    model_config = ConfigDict(extra="forbid", strict=True)

    kernel_profile: KernelProfile | None = Field(
        default=None,
        description=(
            "Executor runtime profile fixed at session creation. "
            "Omit/null uses EXECUTOR_RUNTIME_PROFILE; the resolved "
            "value is stored. Must belong to "
            "EXECUTOR_RUNTIME_PROFILES."
        ),
    )


def resolve_session_settings(
    value, *, default_profile: str, allowed_profiles: tuple[str, ...]
) -> dict[str, str]:
    settings = (
        value
        if isinstance(value, SessionSettings)
        else SessionSettings.model_validate(value if value is not None else {})
    )
    profile = (
        settings.kernel_profile
        if settings.kernel_profile is not None
        else default_profile
    )
    profile = KERNEL_PROFILE.validate_python(profile)
    if profile not in allowed_profiles:
        raise ValueError(
            "Session kernel_profile is not allowed by the service "
            "configuration"
        )
    return {"kernel_profile": profile}
