import math

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DatabaseSettings(BaseModel):
    """Database settings owned once by the central snapshot."""

    model_config = ConfigDict(
        populate_by_name=True, extra="forbid", frozen=True, allow_inf_nan=False
    )
    database_url: str = Field(
        default="postgresql+asyncpg://postgres@127.0.0.1:5432/chat_app",
        repr=False,
    )
    sql_echo: bool = False
    database_pool_size: int = 10
    database_max_overflow: int = 10
    database_pool_timeout_seconds: float = 30.0
    database_pool_recycle_seconds: int = 300
    database_prepared_statement_cache_size: int = Field(
        default=0, ge=0, le=1000
    )

    @model_validator(mode="after")
    def validate_limits(self):
        if self.database_pool_size < 1 or self.database_max_overflow < 0:
            raise ValueError(
                "database pool sizes must be bounded and non-negative"
            )
        if (
            not math.isfinite(self.database_pool_timeout_seconds)
            or self.database_pool_timeout_seconds <= 0
        ):
            raise ValueError(
                "database_pool_timeout_seconds must be finite and positive"
            )
        return self
