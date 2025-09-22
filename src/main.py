from os import environ
from typing import Any

import discord
from discord.ext import commands
from dotenv import load_dotenv
from loguru import logger

from src.bot import VoiceTracker


def main() -> None:
    """Main function to run the Discord bot."""
    load_dotenv()

    logger.add("discord_{time}.log", rotation="1 MB", retention="10 days")

    intents = discord.Intents.default()
    intents.message_content = True
    intents.voice_states = True
    intents.presences = True
    intents.members = True

    bot = commands.Bot(command_prefix="!", intents=intents)

    @bot.event
    async def on_ready() -> None:
        """Event triggered when the bot is ready."""
        logger.debug(f"{bot.user} has connected to Discord!")
        await bot.add_cog(VoiceTracker(bot, logger=logger))

    @bot.event
    async def on_error(event: str, *args: list[Any], **kwargs: dict[str, Any]) -> None:  # noqa: ARG001, RUF029
        logger.error(f"Error in {event}: {args[0] if args else 'Unknown error'}")

    bot.run(environ.get("DISCORD_TOKEN", ""))


if __name__ == "__main__":
    main()
