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
        self.db = VoiceDatabase(logger)
        self.active_sessions: dict[str, int] = {}
        self.inactive_sessions: dict[str, int] = {}
        self.logger.info("VoiceTracker cog initialized")

        self.daily_reset.start()
        self.bot.loop.create_task(self.initialize())

    async def initialize(self) -> None:
        """
        Initialize database and track existing users.
        """
        await self.db.connect()
        await self.db.import_from_json()
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
                        await self.start_active_session(member)
                        tracked_count += 1
                        self.logger.info(f"Started tracking existing active user: {member.name} in {channel.name}")

                    else:
                        await self.start_inactive_session(member)
                        tracked_count += 1
                        self.logger.info(f"Started tracking existing inactive user: {member.name} in {channel.name}")

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

    async def start_active_session(self, member: discord.Member) -> None:
        """
        Start tracking a voice session as active.

        Args:
            member: The member to start tracking.
        """
        user_id = str(member.id)

        if user_id not in self.active_sessions and self.is_user_active(member):
            if user_id in self.inactive_sessions:
                await self.end_inactive_session(member)

            session_id = await self.db.start_session(
                user_id,
                str(member.guild.id),
                str(member.voice.channel.id),
                member.name,
                member.guild.name,
                member.voice.channel.name,
                is_active=True,
            )

            self.active_sessions[user_id] = session_id
            self.logger.info(f"Started active tracking {member.name} (ID: {user_id}) in {member.voice.channel.name}")
            self.logger.debug(f"Total active sessions: {len(self.active_sessions)}")

    async def start_inactive_session(self, member: discord.Member) -> None:
        """
        Start tracking a voice session as active.

        Args:
            member: The member to start tracking.
        """
        user_id = str(member.id)

        if user_id not in self.inactive_sessions and member.voice and member.voice.channel:
            if user_id in self.active_sessions:
                await self.end_active_session(member)

            session_id = await self.db.start_session(
                user_id,
                str(member.guild.id),
                str(member.voice.channel.id),
                member.name,
                member.guild.name,
                member.voice.channel.name,
                is_active=False,
            )

            self.inactive_sessions[user_id] = session_id

            self.logger.info(f"Started inactive tracking {member.name} (ID: {user_id}) in {member.voice.channel.name}")

    async def end_active_session(self, member: discord.Member) -> None:
        """
        End an active voice session and record the time.

        Args:
            member: The member to stop tracking.
        """
        user_id = str(member.id)

        if user_id in self.active_sessions:
            session_id = self.active_sessions.pop(user_id)
            await self.db.end_session(session_id)
            self.logger.info(f"Ended active tracking {member.name} (ID: {user_id})")

    async def end_inactive_session(self, member: discord.Member) -> None:
        """
        End an inactive voice session and record the time.

        Args:
            member: The member to stop tracking.
        """
        user_id = str(member.id)

        if user_id in self.inactive_sessions:
            session_id = self.inactive_sessions.pop(user_id)
            await self.db.end_session(session_id)
            self.logger.info(f"Ended inactive tracking {member.name} (ID: {user_id})")

    async def end_all_sessions(self, member: discord.Member) -> None:
        """
        End all voice sessions for a member.

        Args:
            member: The member whose sessions to end.
        """
        await self.end_active_session(member)
        await self.end_inactive_session(member)

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

        if before.channel is None and after.channel is not None:
            self.logger.info(f"{member.name} joined voice channel {after.channel.name}")

            if self.is_user_active(member):
                await self.start_active_session(member)

            else:
                await self.start_inactive_session(member)

        elif before.channel is not None and after.channel is None:
            self.logger.info(f"{member.name} left voice channel {before.channel.name}")
            await self.end_all_sessions(member)

        elif before.channel != after.channel and after.channel is not None:
            self.logger.info(
                f"{member.name} moved from {before.channel.name if before.channel else 'None'} to {after.channel.name}",
            )

            await self.end_all_sessions(member)

            if self.is_user_active(member):
                await self.start_active_session(member)

            else:
                await self.start_inactive_session(member)

        elif after.channel is not None and (before.self_mute != after.self_mute or before.mute != after.mute):
            if after.self_mute or after.mute:
                self.logger.info(f"{member.name} muted")
                await self.end_active_session(member)
                await self.start_inactive_session(member)

            elif self.is_user_active(member):
                self.logger.info(f"{member.name} unmuted and is active")
                await self.end_inactive_session(member)
                await self.start_active_session(member)

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

            if after.status in {discord.Status.invisible, discord.Status.idle, discord.Status.offline}:
                self.logger.info(f"{after.name} went {after.status}")
                await self.end_active_session(after)
                await self.start_inactive_session(after)

            elif before.status in {
                discord.Status.invisible,
                discord.Status.idle,
                discord.Status.offline,
            } and self.is_user_active(after):
                self.logger.info(f"{after.name} became active from {before.status}")
                await self.end_inactive_session(after)
                await self.start_active_session(after)

    @tasks.loop(time=time(0, 0))
    async def daily_reset(self) -> None:
        """
        Reset daily tracking at midnight.
        """
        self.logger.info("Starting daily reset task")

        for user_id, session_id in list(self.active_sessions.items()):
            await self.db.end_session(session_id)
            self.active_sessions.pop(user_id)

        for user_id, session_id in list(self.inactive_sessions.items()):
            await self.db.end_session(session_id)
            self.inactive_sessions.pop(user_id)

        await self.db.cleanup_old_data(30)
        self.logger.info(f"Daily reset completed at {datetime.now()}")

    @commands.hybrid_command(name="voicetime", description="Check voice time for today")
    async def voice_time(self, ctx: commands.Context, target: Optional[discord.Member] = None) -> None:  # noqa: PLR0914
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

        current_active_time = 0.0
        current_inactive_time = 0.0

        if user_id in self.active_sessions:
            current_active_time = await self.db.get_active_session_time(self.active_sessions[user_id])

        if user_id in self.inactive_sessions:
            current_inactive_time = await self.db.get_active_session_time(self.inactive_sessions[user_id])

        stored_active_time = await self.db.get_user_time_for_date(user_id, guild_id, today_key, is_active=True)
        stored_inactive_time = await self.db.get_user_time_for_date(user_id, guild_id, today_key, is_active=False)

        seconds_in_day = 24 * 3600

        total_active_seconds = stored_active_time + current_active_time
        total_inactive_seconds = stored_inactive_time + current_inactive_time

        total_offline_seconds = seconds_in_day - total_active_seconds - total_inactive_seconds

        offline_hours = int(total_offline_seconds // 3600)
        offline_minutes = int((total_offline_seconds % 3600) // 60)
        offline_seconds = int(total_offline_seconds % 60)
        offline_pct = (total_offline_seconds / seconds_in_day) * 100 if total_offline_seconds > 0 else 0

        online_hours = 24 - int(total_offline_seconds // 3600)
        online_minutes = 60 - int((total_offline_seconds % 3600) // 60)
        online_seconds = 60 - int(total_offline_seconds % 60)
        online_pct = 100 - ((total_offline_seconds / seconds_in_day) * 100 if total_offline_seconds > 0 else 0)

        active_hours = int(total_active_seconds // 3600)
        active_minutes = int((total_active_seconds % 3600) // 60)
        active_seconds = int(total_active_seconds % 60)
        active_pct = (
            (total_active_seconds / (total_active_seconds + total_inactive_seconds)) * 100
            if total_active_seconds > 0
            else 0
        )

        inactive_hours = int(total_inactive_seconds // 3600)
        inactive_minutes = int((total_inactive_seconds % 3600) // 60)
        inactive_seconds = int(total_inactive_seconds % 60)
        inactive_pct = (
            (total_inactive_seconds / (total_active_seconds + total_inactive_seconds)) * 100
            if total_inactive_seconds > 0
            else 0
        )

        self.logger.info(
            f"Voice time query: {target.name} has {active_hours}h {active_minutes}m {active_seconds}s active today",
        )

        embed = discord.Embed(
            title=f"Voice Activity - {target.display_name}",
            description=f"**Today ({today_key})**",
            color=discord.Color.blue(),
        )

        embed.add_field(
            name="🗣️ Active Time",
            value=f"{active_hours}h {active_minutes}m {active_seconds}s ({active_pct:.1f}%)",
            inline=False,
        )

        embed.add_field(
            name="🗣️ AFK Time",
            value=f"{inactive_hours}h {inactive_minutes}m {inactive_seconds}s ({inactive_pct:.1f}%)",
            inline=False,
        )

        embed.add_field(
            name="🌐 Online",
            value=f"{online_hours}h {online_minutes}m {online_seconds}s ({online_pct:.1f}%)",
            inline=False,
        )

        embed.add_field(
            name="🌐 Offline",
            value=f"{offline_hours}h {offline_minutes}m {offline_seconds}s ({offline_pct:.1f}%)",
            inline=False,
        )

        if user_id in self.active_sessions:
            embed.add_field(name="Status", value="🎙️ Currently active", inline=False)

        elif user_id in self.inactive_sessions:
            embed.add_field(name="Status", value="🔇 Currently inactive", inline=False)

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
            stat["inactive_seconds"] = 0.0

            if user_id in self.active_sessions:
                current_time = await self.db.get_active_session_time(self.active_sessions[user_id])
                stat["total_seconds"] += current_time

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

        if not self.active_sessions and not self.inactive_sessions:
            await ctx.send("No users are currently being tracked.")
            self.logger.info("No sessions to display")
            return

        embed = discord.Embed(title="Currently Tracked Voice Sessions", color=discord.Color.green())

        guild_sessions = 0

        for user_id, session_id in self.active_sessions.items():
            member = ctx.guild.get_member(int(user_id))

            if member and member.guild.id == ctx.guild.id:
                duration = await self.db.get_active_session_time(session_id)
                minutes = int(duration // 60)
                seconds = int(duration % 60)
                channel_name = member.voice.channel.name if member.voice and member.voice.channel else "Unknown"
                embed.add_field(
                    name=f"{member.display_name} (Active)",
                    value=f"📍 {channel_name}\n⏱️ {minutes}m {seconds}s",
                    inline=True,
                )

                guild_sessions += 1

        for user_id, session_id in self.inactive_sessions.items():
            member = ctx.guild.get_member(int(user_id))

            if member and member.guild.id == ctx.guild.id:
                duration = await self.db.get_active_session_time(session_id)
                minutes = int(duration // 60)
                seconds = int(duration % 60)
                channel_name = member.voice.channel.name if member.voice and member.voice.channel else "Unknown"
                embed.add_field(
                    name=f"{member.display_name} (Inactive)",
                    value=f"📍 {channel_name}\n⏱️ {minutes}m {seconds}s",
                    inline=True,
                )
                guild_sessions += 1

        self.logger.info(f"Displaying {guild_sessions} sessions for guild {ctx.guild.name}")
        await ctx.send(embed=embed)

    async def cog_unload(self) -> None:
        """
        Clean up when cog is unloaded.
        """
        self.logger.info("VoiceTracker cog is being unloaded, cleaning up...")

        self.daily_reset.cancel()

        for _user_id, session_id in list(self.active_sessions.items()):
            await self.db.end_session(session_id)

        for _user_id, session_id in list(self.inactive_sessions.items()):
            await self.db.end_session(session_id)

        await self.db.export_to_json()
        await self.db.close()
        self.logger.info("VoiceTracker cog unloaded successfully")
