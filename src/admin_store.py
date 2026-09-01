"""Discord admin policy."""

import discord


class AdminStore:
    """Combines configured bot owners with Discord guild permissions."""

    def __init__(self, whitelisted_user_ids: frozenset[int] = frozenset()) -> None:
        self._whitelisted_user_ids = whitelisted_user_ids

    def is_admin(self, user_id: int) -> bool:
        """Check if a user is in the admin whitelist."""
        return user_id in self._whitelisted_user_ids

    def is_admin_in_context(self, user_id: int, member: discord.Member | None) -> bool:
        """Check if a user has admin access.

        Returns True if:
        - User is in the whitelist
        - User has Administrator permission in the guild
        """
        if self.is_admin(user_id):
            return True

        return member is not None and member.guild_permissions.administrator

    def get_all(self) -> set[int]:
        """Get all whitelisted admin user IDs."""
        return set(self._whitelisted_user_ids)
