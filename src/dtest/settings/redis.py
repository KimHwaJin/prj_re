from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class RedisSettings(BaseModel):
    """Redis settings owned once by the central snapshot."""

    model_config = ConfigDict(
        populate_by_name=True, extra="forbid", frozen=True, allow_inf_nan=False
    )
    redis_url: str = Field(
        default="redis://127.0.0.1:6379/0",
        validation_alias=AliasChoices("REDIS_URL", "EW_REDIS_URL"),
        repr=False,
    )
