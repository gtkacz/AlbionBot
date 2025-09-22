import json
import pathlib
from datetime import datetime, time, timedelta
from typing import Optional

import discord
from discord.ext import commands, tasks
from loguru._logger import Logger


class VoiceTracker(commands.Cog):
    """Tracks and reports users' voice channel activity on a daily basis."""

    def __init__(self, bot: commands.Bot, logger: Logger) -> None:
        """
        Initialize the VoiceTracker cog.

        Args:
            bot: The Discord bot instance.
            logger: Logger instance for logging.
        """
        self.logger = logger
        self.bot = bot
        self.data_file = "voice_activity.json"
        self.sessions = {}
        self.daily_data = self.load_data()
        self.daily_reset.start()
        self.save_task.start()

    def load_data(self) -> dict:
        """
        Load saved data from JSON file.

        Returns:
            dict: The loaded data.
        """
        if pathlib.Path(self.data_file).exists():
            with pathlib.Path(self.data_file).open(encoding="utf-8") as f:
                self.logger.debug("Loaded existing voice activity data.")
                return json.load(f)

        return {}

    def save_data(self) -> None:
        """Save data to JSON file."""
        with pathlib.Path(self.data_file).open("w", encoding="utf-8") as f:
            self.logger.debug("Saving voice activity data.")
            json.dump(self.daily_data, f, indent=2)

    @staticmethod
    def get_today_key() -> str:
        """
        Get today's date as a string key.

        Returns:
            str: Today's date in 'YYYY-MM-DD' format.
        """
        return datetime.now().strftime("%Y-%m-%d")

    @staticmethod
    def is_user_active(member: discord.Member) -> bool:
        """
        Check if user meets all activity criteria.

        Args:
            member: The member to check.

        Returns:
            bool: True if user is active, False otherwise.
        """
        if not member.voice or not member.voice.channel:
            return False

        if member.voice.self_mute or member.voice.mute:
            return False

        return member.status not in {discord.Status.invisible, discord.Status.idle, discord.Status.offline}

    def start_session(self, member: discord.Member) -> None:
        """
        Start tracking a voice session.

        Args:
            member: The member to start tracking.
        """
        user_id = str(member.id)

        if user_id not in self.sessions and self.is_user_active(member):
            self.sessions[user_id] = {"start_time": datetime.now(), "guild_id": str(member.guild.id)}
            self.logger.info(f"Started tracking {member.name} (ID: {user_id})")

    def end_session(self, member: discord.Member) -> None:
        """
        End a voice session and record the time.

        Args:
            member: The member to stop tracking.
        """
        user_id = str(member.id)

        if user_id in self.sessions:
            session = self.sessions.pop(user_id)
            duration = (datetime.now() - session["start_time"]).total_seconds()

            today_key = self.get_today_key()
            guild_id = session["guild_id"]

            if today_key not in self.daily_data:
                self.daily_data[today_key] = {}

            if guild_id not in self.daily_data[today_key]:
                self.daily_data[today_key][guild_id] = {}

            if user_id not in self.daily_data[today_key][guild_id]:
                self.daily_data[today_key][guild_id][user_id] = {"username": member.name, "total_seconds": 0}

            self.daily_data[today_key][guild_id][user_id]["total_seconds"] += duration

            self.logger.info(f"Ended tracking {member.name} (ID: {user_id}) - Duration: {duration:.2f}s")

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """
        Handle voice channel changes.

        Args:
            member: The member whose voice state changed.
            before: The previous voice state.
            after: The new voice state.
        """
        if before.channel is None and after.channel is not None:
            self.start_session(member)

        elif before.channel is not None and after.channel is None:
            self.end_session(member)

        elif after.channel is not None and (before.self_mute != after.self_mute or before.mute != after.mute):
            if after.self_mute or after.mute:
                self.end_session(member)

            elif self.is_user_active(member):
                self.start_session(member)

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.VoiceState, after: discord.VoiceState) -> None:
        """
        Handle status changes.

        Args:
            before: The previous voice state.
            after: The new voice state.
        """
        if after.voice and after.voice.channel and before.status != after.status:
            if after.status in {discord.Status.invisible, discord.Status.idle, discord.Status.offline}:
                self.end_session(after)

            elif before.status in {
                discord.Status.invisible,
                discord.Status.idle,
                discord.Status.offline,
            } and self.is_user_active(after):
                self.start_session(after)

    @tasks.loop(time=time(0, 0))
    async def daily_reset(self) -> None:
        """Reset daily tracking at midnight."""
        for user_id in list(self.sessions.keys()):
            guild = self.bot.get_guild(int(self.sessions[user_id]["guild_id"]))

            if guild:
                member = guild.get_member(int(user_id))

                if member:
                    self.end_session(member)

        cutoff_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

        keys_to_remove = [key for key in self.daily_data if key < cutoff_date]

        for key in keys_to_remove:
            del self.daily_data[key]

        self.save_data()
        self.logger.info(f"Daily reset completed at {datetime.now()}")

    @tasks.loop(minutes=5)
    async def save_task(self) -> None:
        """Periodically save data."""
        self.save_data()

    @commands.command(name="voicetime")
    async def voice_time(self, ctx: discord.ext.commands.Context, target: Optional[discord.Member] = None) -> None:
        """
        Check voice time for today.

        Args:
            ctx: The command context.
            target (optional): The member to check. Defaults to the command invoker.
        """
        target = target or ctx.author
        today_key = self.get_today_key()
        guild_id = str(ctx.guild.id)
        user_id = str(target.id)

        current_session_time = 0
        if user_id in self.sessions and self.sessions[user_id]["guild_id"] == guild_id:
            current_session_time = (datetime.now() - self.sessions[user_id]["start_time"]).total_seconds()

        stored_time = 0
        if (
            today_key in self.daily_data
            and guild_id in self.daily_data[today_key]
            and user_id in self.daily_data[today_key][guild_id]
        ):
            stored_time = self.daily_data[today_key][guild_id][user_id]["total_seconds"]

        total_seconds = stored_time + current_session_time
        hours = int(total_seconds // 3600)
        minutes = int((total_seconds % 3600) // 60)
        seconds = int(total_seconds % 60)

        embed = discord.Embed(
            title=f"Voice Activity - {target.display_name}",
            description=f"**Today ({today_key})**\n{hours}h {minutes}m {seconds}s",
            color=discord.Color.blue(),
        )

        if user_id in self.sessions:
            embed.add_field(name="Status", value="🎙️ Currently tracking", inline=False)

        await ctx.send(embed=embed)

    @commands.command(name="voicestats")
    async def voice_stats(self, ctx: discord.ext.commands.Context, days: int = 7) -> None:  # noqa: C901, PLR0914
        """
        Show voice activity statistics for the past `n` days.

        Args:
            ctx: The command context.
            days (optional): Number of days to look back.
        """
        days = min(days, 30)

        guild_id = str(ctx.guild.id)
        user_totals = {}

        end_date = datetime.now()
        start_date = end_date - timedelta(days=days - 1)

        for date_key in self.daily_data:
            date_obj = datetime.strptime(date_key, "%Y-%m-%d")  # noqa: DTZ007

            if start_date.date() <= date_obj.date() <= end_date.date() and guild_id in self.daily_data[date_key]:
                for user_id, data in self.daily_data[date_key][guild_id].items():
                    if user_id not in user_totals:
                        user_totals[user_id] = {"username": data["username"], "total_seconds": 0}

                    user_totals[user_id]["total_seconds"] += data["total_seconds"]

        for user_id, session in self.sessions.items():
            if session["guild_id"] == guild_id:
                if user_id not in user_totals:
                    member = ctx.guild.get_member(int(user_id))

                    if member:
                        user_totals[user_id] = {"username": member.name, "total_seconds": 0}

                if user_id in user_totals:
                    current_time = (datetime.now() - session["start_time"]).total_seconds()
                    user_totals[user_id]["total_seconds"] += current_time

        sorted_users = sorted(user_totals.items(), key=lambda x: x[1]["total_seconds"], reverse=True)

        embed = discord.Embed(
            title="Voice Activity Leaderboard",
            description=f"Past {days} days",
            color=discord.Color.gold(),
        )

        if sorted_users:
            leaderboard_text = ""
            for i, (_user_id, data) in enumerate(sorted_users[:10], 1):
                total_seconds = data["total_seconds"]
                hours = int(total_seconds // 3600)
                minutes = int((total_seconds % 3600) // 60)
                username = data["username"]

                medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."

                leaderboard_text += f"{medal} **{username}**: {hours}h {minutes}m\n"

            embed.add_field(name="Top Active Users", value=leaderboard_text, inline=False)

        else:
            embed.add_field(name="No Data", value="No voice activity recorded yet.", inline=False)

        await ctx.send(embed=embed)

    @commands.command(name="voiceactive")
    @commands.has_permissions(administrator=True)
    async def voice_active(self, ctx: discord.ext.commands.Context) -> None:
        """
        Show currently tracked users.

        Args:
            ctx: The command context.
        """
        if not self.sessions:
            await ctx.send("No users are currently being tracked.")
            return

        embed = discord.Embed(title="Currently Active Voice Sessions", color=discord.Color.green())

        for user_id, session in self.sessions.items():
            if session["guild_id"] == str(ctx.guild.id):
                member = ctx.guild.get_member(int(user_id))
                if member:
                    duration = (datetime.now() - session["start_time"]).total_seconds()
                    minutes = int(duration // 60)
                    seconds = int(duration % 60)
                    embed.add_field(name=member.display_name, value=f"⏱️ {minutes}m {seconds}s", inline=True)

        await ctx.send(embed=embed)

    def cog_unload(self) -> None:
        """Clean up when cog is unloaded."""
        self.daily_reset.cancel()
        self.save_task.cancel()

        for user_id in list(self.sessions.keys()):
            guild_id = self.sessions[user_id]["guild_id"]
            guild = self.bot.get_guild(int(guild_id))

            if guild:
                member = guild.get_member(int(user_id))

                if member:
                    self.end_session(member)

        self.save_data()
