"""Admin permissions for the bot."""

import discord

# Hardcoded whitelist of admin user IDs (always have admin access)
WHITELISTED_ADMIN_IDS: set[int] = {
    320909318767509505,
}


class AdminStore:
    """Manages admin permission checks."""

    def is_admin(self, user_id: int) -> bool:
        """Check if a user is in the admin whitelist."""
        return user_id in WHITELISTED_ADMIN_IDS

    def is_admin_in_context(self, user_id: int, member: discord.Member | None) -> bool:
        """Check if a user has admin access.

        Returns True if:
        - User is in the whitelist
        - User has Administrator permission in the guild
        """
        if self.is_admin(user_id):
            return True

        if member is not None and member.guild_permissions.administrator:
            return True

        return False

    def get_all(self) -> set[int]:
        """Get all whitelisted admin user IDs."""
        return WHITELISTED_ADMIN_IDS.copy()


# Global instance
admin_store = AdminStore()
