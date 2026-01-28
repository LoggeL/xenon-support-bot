"""Per-server configuration storage."""

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from src.config import settings


@dataclass
class ServerSettings:
    """Configuration for a single server."""

    guild_id: int
    support_role_id: int | None = None
    ticket_channel_id: int | None = None
    ephemeral_processing: bool = False  # Default to public processing messages
    support_channel_id: int | None = None
    menu_message_id: int | None = None
    community_support_channel_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ServerSettings":
        return cls(
            guild_id=data.get("guild_id", 0),
            support_role_id=data.get("support_role_id"),
            ticket_channel_id=data.get("ticket_channel_id"),
            ephemeral_processing=data.get("ephemeral_processing", False),
            support_channel_id=data.get("support_channel_id"),
            menu_message_id=data.get("menu_message_id"),
            community_support_channel_id=data.get("community_support_channel_id"),
        )


class ServerConfigStore:
    """Manages per-server configuration stored as JSON files."""

    def __init__(self, config_dir: Path | None = None):
        self.config_dir = config_dir or (settings.data_dir / "servers")
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[int, ServerSettings] = {}

    def _get_path(self, guild_id: int) -> Path:
        return self.config_dir / f"{guild_id}.json"

    def get(self, guild_id: int) -> ServerSettings:
        """Get server settings, creating defaults if not exists."""
        if guild_id in self._cache:
            return self._cache[guild_id]

        path = self._get_path(guild_id)
        if path.exists():
            try:
                data = json.loads(path.read_text())
                server_settings = ServerSettings.from_dict(data)
                self._cache[guild_id] = server_settings
                return server_settings
            except (json.JSONDecodeError, KeyError) as e:
                print(f"Error loading server config for {guild_id}: {e}")

        # Return defaults
        server_settings = ServerSettings(guild_id=guild_id)
        self._cache[guild_id] = server_settings
        return server_settings

    def save(self, server_settings: ServerSettings) -> None:
        """Save server settings to disk."""
        path = self._get_path(server_settings.guild_id)
        path.write_text(json.dumps(server_settings.to_dict(), indent=2))
        self._cache[server_settings.guild_id] = server_settings

    def update(self, guild_id: int, **kwargs) -> ServerSettings:
        """Update specific fields for a server."""
        server_settings = self.get(guild_id)
        for key, value in kwargs.items():
            if hasattr(server_settings, key):
                setattr(server_settings, key, value)
        self.save(server_settings)
        return server_settings


# Global instance
server_config = ServerConfigStore()
