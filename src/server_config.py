"""Per-server configuration storage using PostgreSQL."""

from dataclasses import asdict, dataclass, replace
from typing import Any

from src.database import get_pool


@dataclass(frozen=True, slots=True)
class ServerSettings:
    """Configuration for a single server."""

    guild_id: int
    support_role_id: int | None = None
    ticket_channel_id: int | None = None
    ephemeral_processing: bool = False
    support_channel_id: int | None = None
    menu_message_id: int | None = None
    community_support_channel_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Any) -> "ServerSettings":
        return cls(
            guild_id=row["guild_id"],
            support_role_id=row["support_role_id"],
            ticket_channel_id=row["ticket_channel_id"],
            ephemeral_processing=row["ephemeral_processing"],
            support_channel_id=row["support_channel_id"],
            menu_message_id=row["menu_message_id"],
            community_support_channel_id=row["community_support_channel_id"],
        )


class ServerConfigStore:
    """Manages per-server configuration in PostgreSQL."""

    def __init__(self):
        self._cache: dict[int, ServerSettings] = {}

    async def get(self, guild_id: int) -> ServerSettings:
        """Get server settings, creating defaults if not exists."""
        if guild_id in self._cache:
            return self._cache[guild_id]

        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM server_configs WHERE guild_id = $1",
                guild_id,
            )

            if row:
                server_settings = ServerSettings.from_row(row)
            else:
                server_settings = ServerSettings(guild_id=guild_id)

            self._cache[guild_id] = server_settings
            return server_settings

    async def save(self, server_settings: ServerSettings) -> None:
        """Save server settings to database."""
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO server_configs (
                    guild_id, support_role_id, ticket_channel_id,
                    ephemeral_processing, support_channel_id,
                    menu_message_id, community_support_channel_id
                ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (guild_id) DO UPDATE SET
                    support_role_id = $2,
                    ticket_channel_id = $3,
                    ephemeral_processing = $4,
                    support_channel_id = $5,
                    menu_message_id = $6,
                    community_support_channel_id = $7,
                    updated_at = NOW()
                """,
                server_settings.guild_id,
                server_settings.support_role_id,
                server_settings.ticket_channel_id,
                server_settings.ephemeral_processing,
                server_settings.support_channel_id,
                server_settings.menu_message_id,
                server_settings.community_support_channel_id,
            )
        self._cache[server_settings.guild_id] = server_settings

    async def update(self, guild_id: int, **kwargs) -> ServerSettings:
        """Update specific fields for a server."""
        allowed_fields = set(ServerSettings.__dataclass_fields__) - {"guild_id"}
        unknown_fields = set(kwargs) - allowed_fields
        if unknown_fields:
            raise ValueError(f"Unknown server setting(s): {', '.join(sorted(unknown_fields))}")

        current = await self.get(guild_id)
        updated = replace(current, **kwargs)
        await self.save(updated)
        return updated


# Global instance
server_config = ServerConfigStore()
