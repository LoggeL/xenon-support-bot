from typing import Any, cast

import pytest
from pydantic import ValidationError

from src.config import Settings


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "discord_token": "discord-secret",
        "openai_api_key": "openai-secret",
        "database_url": "postgresql://user:pass@localhost/xenon",
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**cast(Any, values))


def test_defaults_to_requested_model_and_balanced_reasoning() -> None:
    settings = make_settings()

    assert settings.openai_model == "gpt-5.6-luna"
    assert settings.openai_reasoning_effort == "medium"


def test_rejects_a_different_model() -> None:
    with pytest.raises(ValidationError):
        make_settings(openai_model="gpt-5.6-sol")


def test_parses_admin_ids() -> None:
    settings = make_settings(admin_user_ids="123, 456,123")

    assert settings.parsed_admin_user_ids == frozenset({123, 456})


def test_rejects_non_postgres_database_url() -> None:
    with pytest.raises(ValidationError):
        make_settings(database_url="sqlite:///local.db")
