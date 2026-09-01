"""Application entry point."""

import logging

from pydantic import ValidationError

from src.bot import XenonSupportBot
from src.config import get_settings


def main() -> None:
    try:
        config = get_settings()
    except ValidationError as error:
        raise SystemExit(f"Invalid configuration:\n{error}") from error

    logging.basicConfig(
        level=config.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger(__name__).info("Starting with model %s", config.openai_model)

    bot = XenonSupportBot(config)
    bot.run(config.discord_token.get_secret_value(), log_handler=None)


if __name__ == "__main__":
    main()
