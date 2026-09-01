"""Validated, import-safe application configuration."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    discord_token: SecretStr
    openai_api_key: SecretStr
    database_url: str

    openai_model: Literal["gpt-5.6-luna"] = "gpt-5.6-luna"
    openai_reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"] = "medium"
    openai_max_output_tokens: int = Field(default=1800, ge=256, le=16_000)
    openai_timeout_seconds: float = Field(default=120.0, gt=0, le=600)

    rate_limit_per_minute: int = Field(default=5, ge=1, le=60)
    admin_user_ids: str = "320909318767509505"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    @field_validator("database_url")
    @classmethod
    def require_postgres(cls, value: str) -> str:
        if not value.startswith(("postgresql://", "postgres://")):
            raise ValueError("DATABASE_URL must be a PostgreSQL URL")
        return value

    @property
    def parsed_admin_user_ids(self) -> frozenset[int]:
        try:
            return frozenset(
                int(value.strip()) for value in self.admin_user_ids.split(",") if value.strip()
            )
        except ValueError as error:
            raise ValueError(
                "ADMIN_USER_IDS must contain comma-separated Discord user IDs"
            ) from error


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once at application startup or first runtime use."""
    return Settings()  # pyright: ignore[reportCallIssue]
