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
    owner_user_id: int  # Bot owner - can manage admins
    admin_user_ids: str = ""  # Deprecated - use /admin commands instead

    # OpenRouter
    openrouter_api_key: str
    openrouter_model: str = "openai/gpt-5.1"

    # Rate limiting
    rate_limit_per_minute: int = 5

    # Paths
    data_dir: Path = Path(__file__).parent.parent / "data"

    @property
    def admin_ids(self) -> set[int]:
        if not self.admin_user_ids:
            return set()
        return {int(uid.strip()) for uid in self.admin_user_ids.split(",") if uid.strip()}

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
