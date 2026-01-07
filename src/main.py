"""Main entry point for the Xenon Support Bot."""

import asyncio
import sys

from src.config import settings
from src.bot import bot


def main():
    """Run the bot."""
    print("Starting Xenon Support Bot...")
    print(f"Model: {settings.openrouter_model}")

    try:
        bot.run(settings.discord_token)
    except KeyboardInterrupt:
        print("\nShutting down...")
    except Exception as e:
        print(f"Fatal error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
