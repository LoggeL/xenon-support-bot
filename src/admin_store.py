"""Dynamic admin storage for managing bot administrators at runtime."""

import json
from pathlib import Path

import discord

from src.config import settings

# Hardcoded whitelist of admin user IDs (always have admin access)
WHITELISTED_ADMIN_IDS: set[int] = {
    320909318767509505,
}


class AdminStore:
    """Manages the list of bot administrators stored in a JSON file."""

    def __init__(self, data_dir: Path | None = None):
        self.data_dir = data_dir or settings.data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._admins: set[int] | None = None

    @property
    def _file_path(self) -> Path:
        return self.data_dir / "admins.json"

    def _load(self) -> set[int]:
        """Load admins from disk."""
        if self._admins is not None:
            return self._admins

        if self._file_path.exists():
            try:
                data = json.loads(self._file_path.read_text())
                self._admins = set(data.get("admins", []))
            except (json.JSONDecodeError, KeyError) as e:
                print(f"Error loading admin store: {e}")
                self._admins = set()
        else:
            self._admins = set()

        return self._admins

    def _save(self) -> None:
        """Save admins to disk."""
        if self._admins is None:
            return

        data = {"admins": list(self._admins)}
        self._file_path.write_text(json.dumps(data, indent=2))

    def is_admin(self, user_id: int) -> bool:
        """Check if a user is an admin. Owner and whitelisted users are always admins."""
        if user_id == settings.owner_user_id:
            return True
        if user_id in WHITELISTED_ADMIN_IDS:
            return True
        return user_id in self._load()

    def is_admin_in_context(self, user_id: int, member: discord.Member | None) -> bool:
        """Check if a user is an admin, including guild admin check.

        Returns True if:
        - User is the owner
        - User is in the whitelist
        - User is in the dynamic admin list
        - User has Administrator permission in the guild
        """
        # Check global admin status first
        if self.is_admin(user_id):
            return True

        # Check guild admin permission
        if member is not None and member.guild_permissions.administrator:
            return True

        return False

    def is_owner(self, user_id: int) -> bool:
        """Check if a user is the bot owner."""
        return user_id == settings.owner_user_id

    def add_admin(self, user_id: int) -> bool:
        """Add a user as admin. Returns True if added, False if already admin or is owner."""
        if user_id == settings.owner_user_id:
            return False  # Owner is already implicitly an admin

        admins = self._load()
        if user_id in admins:
            return False

        admins.add(user_id)
        self._save()
        return True

    def remove_admin(self, user_id: int) -> bool:
        """Remove admin status. Returns True if removed, False if not admin or is owner."""
        if user_id == settings.owner_user_id:
            return False  # Cannot remove owner

        admins = self._load()
        if user_id not in admins:
            return False

        admins.discard(user_id)
        self._save()
        return True

    def get_all(self) -> set[int]:
        """Get all admin user IDs (including owner and whitelisted)."""
        admins = self._load().copy()
        admins.add(settings.owner_user_id)
        admins.update(WHITELISTED_ADMIN_IDS)
        return admins


# Global instance
admin_store = AdminStore()
