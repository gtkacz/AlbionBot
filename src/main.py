from os import environ
from typing import Any, Optional

import discord
from discord.ext import commands
from dotenv import load_dotenv
from loguru import logger

from bot import VoiceTracker


def main() -> None:  # noqa: C901, PLR0915
    """Main function to run the Discord bot."""
    load_dotenv()

    logger.add(
        "discord_{time:YYYY-MM-DD}.log",
        rotation="1 MB",
        retention="10 days",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        level="DEBUG",
    )

    logger.info("Starting Discord bot initialization")

    intents = discord.Intents.default()
    intents.message_content = True
    intents.voice_states = True
    intents.presences = True
    intents.members = True

    logger.debug(
        f"Configured intents: message_content={intents.message_content}, "
        f"voice_states={intents.voice_states}, presences={intents.presences}, "
        f"members={intents.members}",
    )

    bot = commands.Bot(command_prefix="@", intents=intents)

    @bot.event
    async def on_ready() -> None:
        """Event triggered when the bot is ready."""
        logger.info(f"{bot.user} has connected to Discord!")
        logger.info(f"Bot is in {len(bot.guilds)} guilds")

        for guild in bot.guilds:
            logger.debug(f"Connected to guild: {guild.name} (ID: {guild.id})")

        logger.info("Loading VoiceTracker cog...")

        await bot.add_cog(VoiceTracker(bot, logger=logger))

        logger.info("VoiceTracker cog loaded successfully")

        logger.info("Syncing slash commands...")

        try:
            synced = await bot.tree.sync()
            logger.info(f"Successfully synced {len(synced)} slash commands")

            for cmd in synced:
                logger.debug(f"Synced command: {cmd.name}")

        except Exception:
            logger.exception("Failed to sync commands")

    @bot.event
    async def on_guild_join(guild: discord.Guild) -> None:
        """Event triggered when bot joins a new guild."""
        logger.info(f"Joined new guild: {guild.name} (ID: {guild.id})")
        logger.info(f"Guild has {guild.member_count} members")

        try:
            await bot.tree.sync(guild=guild)
            logger.info(f"Synced commands for guild {guild.name}")

        except Exception:
            logger.exception(f"Failed to sync commands for guild {guild.name}")

    @bot.event
    async def on_guild_remove(guild: discord.Guild) -> None:  # noqa: RUF029
        """Event triggered when bot is removed from a guild."""
        logger.info(f"Removed from guild: {guild.name} (ID: {guild.id})")

    @bot.event
    async def on_command(ctx: commands.Context) -> None:  # noqa: RUF029
        """Event triggered when a command is invoked."""
        logger.info(f"Command '{ctx.command}' invoked by {ctx.author} in {ctx.guild.name if ctx.guild else 'DM'}")

    @bot.event
    async def on_command_error(ctx: commands.Context, error: Exception) -> None:
        """Event triggered when a command raises an error."""
        if isinstance(error, commands.CommandNotFound):
            logger.debug(f"Unknown command attempted by {ctx.author}: {ctx.message.content}")

        elif isinstance(error, commands.MissingPermissions):
            logger.warning(f"Permission denied for {ctx.author} on command {ctx.command}: {error}")
            await ctx.send("You don't have permission to use this command.")

        elif isinstance(error, commands.MissingRequiredArgument):
            logger.warning(f"Missing argument for command {ctx.command}: {error}")
            await ctx.send(f"Missing required argument: {error.param.name}")

        else:
            logger.exception(f"Command error in {ctx.command}")

    @bot.event
    async def on_error(event: str, *args: list[Any], **kwargs: dict[str, Any]) -> None:  # noqa: ARG001, RUF029
        """Event triggered when an error occurs in an event."""
        logger.error(f"Error in {event}: {args[0] if args else 'Unknown error'}", exc_info=True)

    @bot.command(name="sync", hidden=True)
    @commands.is_owner()
    async def sync_commands(ctx: commands.Context, guild_id: Optional[int] = None) -> None:
        """Manually sync slash commands (bot owner only)."""
        logger.info(f"Manual sync requested by {ctx.author}")

        if guild_id:
            guild = bot.get_guild(guild_id)

            if guild:
                synced = await bot.tree.sync(guild=guild)
                await ctx.send(f"Synced {len(synced)} commands to {guild.name}")
                logger.info(f"Manually synced {len(synced)} commands to guild {guild.name}")

            else:
                await ctx.send("Guild not found")
                logger.warning(f"Guild {guild_id} not found for sync")

        else:
            synced = await bot.tree.sync()
            await ctx.send(f"Globally synced {len(synced)} commands")
            logger.info(f"Manually synced {len(synced)} commands globally")

    token = environ.get("DISCORD_TOKEN", "")

    if not token:
        logger.critical("DISCORD_TOKEN not found in environment variables!")
        return

    logger.success("Starting bot with provided token...")

    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
