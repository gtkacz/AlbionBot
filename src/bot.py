from datetime import datetime, time
from operator import itemgetter
from typing import Optional

import discord
from discord.ext import commands, tasks
from loguru._logger import Logger

from database import VoiceDatabase


class VoiceTracker(commands.Cog):
    """
    Tracks and reports users' voice channel activity on a daily basis.
    """

    def __init__(self, bot: commands.Bot, logger: Logger) -> None:
        """
        Initialize the VoiceTracker cog.

        Args:
            bot: The Discord bot instance.
            logger: Logger instance for logging.
        """
        self.logger = logger
        self.bot = bot
        self.db = VoiceDatabase()
        self.sessions: dict[str, int] = {}
        self.logger.info("VoiceTracker cog initialized")

        self.daily_reset.start()
        self.bot.loop.create_task(self.initialize())

    async def initialize(self) -> None:
        """
        Initialize database and track existing users.
        """
        await self.db.connect()
        await self.track_existing_users()

    async def track_existing_users(self) -> None:
        """
        Track users who are already in voice channels when the bot starts.
        """
        self.logger.info("Starting to track existing users in voice channels")

        await self.bot.wait_until_ready()

        tracked_count = 0
        for guild in self.bot.guilds:
            self.logger.debug(f"Checking guild: {guild.name} (ID: {guild.id})")

            for channel in guild.voice_channels:
                self.logger.debug(f"Checking voice channel: {channel.name} with {len(channel.members)} members")

                for member in channel.members:
                    if self.is_user_active(member):
                        await self.start_session(member)
                        tracked_count += 1
                        self.logger.info(f"Started tracking existing user: {member.name} in {channel.name}")

                    else:
                        self.logger.debug(f"User {member.name} not active (muted/idle/invisible)")

        self.logger.info(f"Finished tracking existing users. Started tracking {tracked_count} users.")

    @staticmethod
    def get_today_key() -> str:
        """
        Get today's date as a string key.

        Returns:
            Today's date in 'YYYY-MM-DD' format.
        """
        return datetime.now().strftime("%Y-%m-%d")

    @staticmethod
    def is_user_active(member: discord.Member) -> bool:
        """
        Check if user meets all activity criteria.

        Args:
            member: The member to check.

        Returns:
            True if user is active, False otherwise.
        """
        if not member.voice or not member.voice.channel:
            return False

        if member.voice.self_mute or member.voice.mute:
            return False

        return member.status not in {discord.Status.invisible, discord.Status.idle, discord.Status.offline}

    async def start_session(self, member: discord.Member) -> None:
        """
        Start tracking a voice session.

        Args:
            member: The member to start tracking.
        """
        user_id = str(member.id)

        if user_id not in self.sessions and self.is_user_active(member):
            session_id = await self.db.start_session(user_id, str(member.guild.id), member.name, member.guild.name)
            self.sessions[user_id] = session_id
            self.logger.info(f"Started tracking {member.name} (ID: {user_id}) in guild {member.guild.name}")
            self.logger.debug(
                f"Session ID: {session_id}, Guild ID: {member.guild.id}, Channel: {member.voice.channel.name}",
            )
            self.logger.debug(f"Total active sessions: {len(self.sessions)}")

        elif user_id in self.sessions:
            self.logger.debug(f"Session already exists for {member.name} (ID: {user_id})")

        else:
            self.logger.debug(f"User {member.name} doesn't meet activity criteria")

    async def end_session(self, member: discord.Member) -> None:
        """
        End a voice session and record the time.

        Args:
            member: The member to stop tracking.
        """
        user_id = str(member.id)

        if user_id in self.sessions:
            session_id = self.sessions.pop(user_id)
            await self.db.end_session(session_id)

            self.logger.info(f"Ended tracking {member.name} (ID: {user_id})")
            self.logger.debug(f"Remaining active sessions: {len(self.sessions)}")

        else:
            self.logger.debug(f"No active session found for {member.name} (ID: {user_id})")

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
        self.logger.debug(f"Voice state update for {member.name} (ID: {member.id})")
        self.logger.debug(f"Before channel: {before.channel.name if before.channel else 'None'}")
        self.logger.debug(f"After channel: {after.channel.name if after.channel else 'None'}")
        self.logger.debug(f"Before mute states - Self: {before.self_mute}, Server: {before.mute}")
        self.logger.debug(f"After mute states - Self: {after.self_mute}, Server: {after.mute}")

        if before.channel is None and after.channel is not None:
            self.logger.info(f"{member.name} joined voice channel {after.channel.name}")
            await self.start_session(member)

        elif before.channel is not None and after.channel is None:
            self.logger.info(f"{member.name} left voice channel {before.channel.name}")
            await self.end_session(member)

        elif after.channel is not None and (before.self_mute != after.self_mute or before.mute != after.mute):
            if after.self_mute or after.mute:
                self.logger.info(f"{member.name} muted (self: {after.self_mute}, server: {after.mute})")
                await self.end_session(member)

            elif self.is_user_active(member):
                self.logger.info(f"{member.name} unmuted and is active")
                await self.start_session(member)

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member) -> None:
        """
        Handle status changes.

        Args:
            before: The member before the update.
            after: The member after the update.
        """
        if after.voice and after.voice.channel and before.status != after.status:
            self.logger.debug(f"Presence update for {after.name} (ID: {after.id})")
            self.logger.debug(f"Status changed from {before.status} to {after.status}")

            if after.status in {discord.Status.invisible, discord.Status.idle, discord.Status.offline}:
                self.logger.info(f"{after.name} went {after.status}, ending session")
                await self.end_session(after)

            elif before.status in {
                discord.Status.invisible,
                discord.Status.idle,
                discord.Status.offline,
            } and self.is_user_active(after):
                self.logger.info(f"{after.name} became active from {before.status}")
                await self.start_session(after)

    @tasks.loop(time=time(0, 0))
    async def daily_reset(self) -> None:
        """
        Reset daily tracking at midnight.
        """
        self.logger.info("Starting daily reset task")

        sessions_ended = 0

        for user_id, session_id in list(self.sessions.items()):
            await self.db.end_session(session_id)
            self.sessions.pop(user_id)
            sessions_ended += 1
            self.logger.debug(f"Ended session {session_id} for user {user_id} during daily reset")

        self.logger.info(f"Ended {sessions_ended} active sessions during daily reset")

        await self.db.cleanup_old_data(30)
        self.logger.info(f"Daily reset completed at {datetime.now()}")

    @commands.hybrid_command(name="voicetime", description="Check voice time for today")
    async def voice_time(self, ctx: commands.Context, target: Optional[discord.Member] = None) -> None:
        """
        Check voice time for today.

        Args:
            ctx: The command context.
            target: The member to check. Defaults to the command invoker.
        """
        target = target or ctx.author
        self.logger.debug(f"Voice time command invoked by {ctx.author.name} for target {target.name}")

        today_key = self.get_today_key()
        guild_id = str(ctx.guild.id)
        user_id = str(target.id)

        current_session_time = 0.0

        if user_id in self.sessions:
            current_session_time = await self.db.get_active_session_time(self.sessions[user_id])
            self.logger.debug(f"User has active session with {current_session_time:.2f} seconds")

        stored_time = await self.db.get_user_time_for_date(user_id, guild_id, today_key)
        self.logger.debug(f"User has {stored_time:.2f} seconds stored for today")

        total_seconds = stored_time + current_session_time
        hours = int(total_seconds // 3600)
        minutes = int((total_seconds % 3600) // 60)
        seconds = int(total_seconds % 60)

        self.logger.info(f"Voice time query: {target.name} has {hours}h {minutes}m {seconds}s today")

        embed = discord.Embed(
            title=f"Voice Activity - {target.display_name}",
            description=f"**Today ({today_key})**\n{hours}h {minutes}m {seconds}s",
            color=discord.Color.blue(),
        )

        if user_id in self.sessions:
            embed.add_field(name="Status", value="🎙️ Currently tracking", inline=False)

        await ctx.send(embed=embed)

    @commands.hybrid_command(name="voicestats", description="Show voice activity statistics")
    async def voice_stats(self, ctx: commands.Context, days: int = 7) -> None:
        """
        Show voice activity statistics for the past n days.

        Args:
            ctx: The command context.
            days: Number of days to look back.
        """
        self.logger.debug(f"Voice stats command invoked by {ctx.author.name} for {days} days")

        days = min(days, 30)
        guild_id = str(ctx.guild.id)

        user_stats = await self.db.get_guild_stats(guild_id, days)

        for stat in user_stats:
            user_id = stat["user_id"]
            if user_id in self.sessions:
                current_time = await self.db.get_active_session_time(self.sessions[user_id])
                stat["total_seconds"] += current_time
                self.logger.debug(f"Added {current_time:.2f}s from active session for {stat['username']}")

        sorted_users = sorted(user_stats, key=itemgetter("total_seconds"), reverse=True)
        self.logger.info(f"Generated stats for {len(sorted_users)} users over {days} days")

        embed = discord.Embed(
            title="Voice Activity Leaderboard",
            description=f"Past {days} days",
            color=discord.Color.gold(),
        )

        if sorted_users:
            leaderboard_text = ""

            for i, data in enumerate(sorted_users[:10], 1):
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

    @commands.hybrid_command(name="voiceactive", description="Show currently tracked users (Admin only)")
    @commands.has_permissions(administrator=True)
    async def voice_active(self, ctx: commands.Context) -> None:
        """
        Show currently tracked users.

        Args:
            ctx: The command context.
        """
        self.logger.debug(f"Voice active command invoked by admin {ctx.author.name}")

        if not self.sessions:
            await ctx.send("No users are currently being tracked.")
            self.logger.info("No active sessions to display")
            return

        embed = discord.Embed(title="Currently Active Voice Sessions", color=discord.Color.green())

        guild_sessions = 0

        for user_id, session_id in self.sessions.items():
            member = ctx.guild.get_member(int(user_id))

            if member and member.guild.id == ctx.guild.id:
                duration = await self.db.get_active_session_time(session_id)
                minutes = int(duration // 60)
                seconds = int(duration % 60)
                embed.add_field(name=member.display_name, value=f"⏱️ {minutes}m {seconds}s", inline=True)
                guild_sessions += 1

        self.logger.info(f"Displaying {guild_sessions} active sessions for guild {ctx.guild.name}")
        await ctx.send(embed=embed)

    async def cog_unload(self) -> None:
        """
        Clean up when cog is unloaded.
        """
        self.logger.info("VoiceTracker cog is being unloaded, cleaning up...")

        self.daily_reset.cancel()

        sessions_ended = 0

        for _user_id, session_id in list(self.sessions.items()):
            await self.db.end_session(session_id)
            sessions_ended += 1

        self.logger.info(f"Ended {sessions_ended} sessions during cog unload")
        await self.db.close()
        self.logger.info("VoiceTracker cog unloaded successfully")
