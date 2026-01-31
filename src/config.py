from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Discord
    discord_token: str

    # OpenRouter
    openrouter_api_key: str
    openrouter_model: str = "openai/gpt-5.1"

    # Database
    database_url: str  # postgresql://user:pass@host:port/dbname

    # Rate limiting
    rate_limit_per_minute: int = 5

    # Paths
    data_dir: Path = Path(__file__).parent.parent / "data"

    @property
    def docs_dir(self) -> Path:
        return self.data_dir / "docs"

    @property
    def index_dir(self) -> Path:
        return self.data_dir / "index"

    @property
    def servers_dir(self) -> Path:
        return self.data_dir / "servers"


settings = Settings()
