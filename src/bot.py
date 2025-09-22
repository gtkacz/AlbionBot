import json
import pathlib
from datetime import datetime, time, timedelta
from typing import Optional

import discord
import loguru
from discord.ext import commands, tasks


class VoiceTracker(commands.Cog):
    """Tracks and reports users' voice channel activity on a daily basis."""

    def __init__(self, bot: commands.Bot, logger: loguru.Logger) -> None:
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
        self.logger.info("VoiceTracker cog initialized")

        self.daily_reset.start()
        self.save_task.start()

        self.bot.loop.create_task(self.track_existing_users())

    async def track_existing_users(self) -> None:
        """Track users who are already in voice channels when the bot starts."""
        self.logger.info("Starting to track existing users in voice channels")

        await self.bot.wait_until_ready()

        tracked_count = 0
        for guild in self.bot.guilds:
            self.logger.debug(f"Checking guild: {guild.name} (ID: {guild.id})")

            for channel in guild.voice_channels:
                self.logger.debug(f"Checking voice channel: {channel.name} with {len(channel.members)} members")

                for member in channel.members:
                    if self.is_user_active(member):
                        self.start_session(member)
                        tracked_count += 1
                        self.logger.info(f"Started tracking existing user: {member.name} in {channel.name}")

                    else:
                        self.logger.debug(f"User {member.name} not active (muted/idle/invisible)")

        self.logger.info(f"Finished tracking existing users. Started tracking {tracked_count} users.")

    def load_data(self) -> dict:
        """
        Load saved data from JSON file.

        Returns:
            dict: The loaded data.
        """
        if pathlib.Path(self.data_file).exists():
            try:
                with pathlib.Path(self.data_file).open(encoding="utf-8") as f:
                    data = json.load(f)
                    self.logger.info(f"Loaded existing voice activity data from {self.data_file}")
                    self.logger.debug(f"Loaded data contains {len(data)} days of records")
                    return data

            except Exception:
                self.logger.exception("Failed to load data file")
                return {}
        else:
            self.logger.info(f"No existing data file found at {self.data_file}, starting fresh")
            return {}

    def save_data(self) -> None:
        """Save data to JSON file."""
        try:
            with pathlib.Path(self.data_file).open("w", encoding="utf-8") as f:
                json.dump(self.daily_data, f, indent=2)
                self.logger.debug(f"Successfully saved voice activity data to {self.data_file}")
                self.logger.debug(f"Saved data for {len(self.daily_data)} days")

        except Exception:
            self.logger.exception("Failed to save data")

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
            self.logger.info(f"Started tracking {member.name} (ID: {user_id}) in guild {member.guild.name}")
            self.logger.debug(f"Session details: Guild ID: {member.guild.id}, Channel: {member.voice.channel.name}")
            self.logger.debug(f"Total active sessions: {len(self.sessions)}")

        elif user_id in self.sessions:
            self.logger.debug(f"Session already exists for {member.name} (ID: {user_id})")

        else:
            self.logger.debug(f"User {member.name} doesn't meet activity criteria")

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

            self.logger.debug(f"Ending session for {member.name} (ID: {user_id})")
            self.logger.debug(f"Session duration: {duration:.2f} seconds ({duration / 60:.2f} minutes)")

            if today_key not in self.daily_data:
                self.daily_data[today_key] = {}
                self.logger.debug(f"Created new date entry for {today_key}")

            if guild_id not in self.daily_data[today_key]:
                self.daily_data[today_key][guild_id] = {}
                self.logger.debug(f"Created new guild entry for guild {guild_id} on {today_key}")

            if user_id not in self.daily_data[today_key][guild_id]:
                self.daily_data[today_key][guild_id][user_id] = {"username": member.name, "total_seconds": 0}
                self.logger.debug(f"Created new user entry for {member.name} in guild {guild_id}")

            previous_total = self.daily_data[today_key][guild_id][user_id]["total_seconds"]
            self.daily_data[today_key][guild_id][user_id]["total_seconds"] += duration
            new_total = self.daily_data[today_key][guild_id][user_id]["total_seconds"]

            self.logger.info(f"Ended tracking {member.name} (ID: {user_id}) - Duration: {duration:.2f}s")
            self.logger.debug(f"Previous total: {previous_total:.2f}s, New total: {new_total:.2f}s")
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
            self.start_session(member)

        elif before.channel is not None and after.channel is None:
            self.logger.info(f"{member.name} left voice channel {before.channel.name}")
            self.end_session(member)

        elif after.channel is not None and (before.self_mute != after.self_mute or before.mute != after.mute):
            if after.self_mute or after.mute:
                self.logger.info(f"{member.name} muted (self: {after.self_mute}, server: {after.mute})")
                self.end_session(member)

            elif self.is_user_active(member):
                self.logger.info(f"{member.name} unmuted and is active")
                self.start_session(member)

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
                self.end_session(after)

            elif before.status in {
                discord.Status.invisible,
                discord.Status.idle,
                discord.Status.offline,
            } and self.is_user_active(after):
                self.logger.info(f"{after.name} became active from {before.status}")
                self.start_session(after)

    @tasks.loop(time=time(0, 0))
    async def daily_reset(self) -> None:
        """Reset daily tracking at midnight."""
        self.logger.info("Starting daily reset task")

        # End all active sessions
        sessions_ended = 0
        for user_id in list(self.sessions.keys()):
            guild = self.bot.get_guild(int(self.sessions[user_id]["guild_id"]))

            if guild:
                member = guild.get_member(int(user_id))

                if member:
                    self.end_session(member)
                    sessions_ended += 1
                    self.logger.debug(f"Ended session for {member.name} during daily reset")

        self.logger.info(f"Ended {sessions_ended} active sessions during daily reset")

        # Clean up old data (>30 days)
        cutoff_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        keys_to_remove = [key for key in self.daily_data if key < cutoff_date]

        for key in keys_to_remove:
            self.logger.debug(f"Removing old data for date: {key}")
            del self.daily_data[key]

        self.logger.info(f"Removed {len(keys_to_remove)} old date entries")

        self.save_data()
        self.logger.info(f"Daily reset completed at {datetime.now()}")

    @tasks.loop(minutes=5)
    async def save_task(self) -> None:
        """Periodically save data."""
        self.logger.debug("Running periodic save task")
        self.save_data()

    @commands.hybrid_command(name="voicetime", description="Check voice time for today")
    async def voice_time(self, ctx: commands.Context, target: Optional[discord.Member] = None) -> None:
        """
        Check voice time for today.

        Args:
            ctx: The command context.
            target (optional): The member to check. Defaults to the command invoker.
        """
        target = target or ctx.author
        self.logger.debug(f"Voice time command invoked by {ctx.author.name} for target {target.name}")

        today_key = self.get_today_key()
        guild_id = str(ctx.guild.id)
        user_id = str(target.id)

        current_session_time = 0
        if user_id in self.sessions and self.sessions[user_id]["guild_id"] == guild_id:
            current_session_time = (datetime.now() - self.sessions[user_id]["start_time"]).total_seconds()
            self.logger.debug(f"User has active session with {current_session_time:.2f} seconds")

        stored_time = 0
        if (
            today_key in self.daily_data
            and guild_id in self.daily_data[today_key]
            and user_id in self.daily_data[today_key][guild_id]
        ):
            stored_time = self.daily_data[today_key][guild_id][user_id]["total_seconds"]
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
    async def voice_stats(self, ctx: commands.Context, days: int = 7) -> None:  # noqa: C901, PLR0914
        """
        Show voice activity statistics for the past `n` days.

        Args:
            ctx: The command context.
            days (optional): Number of days to look back.
        """
        self.logger.debug(f"Voice stats command invoked by {ctx.author.name} for {days} days")

        days = min(days, 30)
        guild_id = str(ctx.guild.id)
        user_totals = {}

        end_date = datetime.now()
        start_date = end_date - timedelta(days=days - 1)

        self.logger.debug(f"Calculating stats from {start_date.date()} to {end_date.date()}")

        dates_processed = 0
        for date_key in self.daily_data:
            date_obj = datetime.strptime(date_key, "%Y-%m-%d")  # noqa: DTZ007

            if start_date.date() <= date_obj.date() <= end_date.date() and guild_id in self.daily_data[date_key]:
                dates_processed += 1
                self.logger.debug(f"Processing data for {date_key}")

                for user_id, data in self.daily_data[date_key][guild_id].items():
                    if user_id not in user_totals:
                        user_totals[user_id] = {"username": data["username"], "total_seconds": 0}

                    user_totals[user_id]["total_seconds"] += data["total_seconds"]

        self.logger.debug(f"Processed {dates_processed} days of data")

        # Add current sessions
        for user_id, session in self.sessions.items():
            if session["guild_id"] == guild_id:
                if user_id not in user_totals:
                    member = ctx.guild.get_member(int(user_id))

                    if member:
                        user_totals[user_id] = {"username": member.name, "total_seconds": 0}

                if user_id in user_totals:
                    current_time = (datetime.now() - session["start_time"]).total_seconds()
                    user_totals[user_id]["total_seconds"] += current_time
                    self.logger.debug(
                        f"Added {current_time:.2f}s from active session for {user_totals[user_id]['username']}",
                    )

        sorted_users = sorted(user_totals.items(), key=lambda x: x[1]["total_seconds"], reverse=True)
        self.logger.info(f"Generated stats for {len(sorted_users)} users over {days} days")

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
        for user_id, session in self.sessions.items():
            if session["guild_id"] == str(ctx.guild.id):
                member = ctx.guild.get_member(int(user_id))
                if member:
                    duration = (datetime.now() - session["start_time"]).total_seconds()
                    minutes = int(duration // 60)
                    seconds = int(duration % 60)
                    embed.add_field(name=member.display_name, value=f"⏱️ {minutes}m {seconds}s", inline=True)
                    guild_sessions += 1

        self.logger.info(f"Displaying {guild_sessions} active sessions for guild {ctx.guild.name}")
        await ctx.send(embed=embed)

    def cog_unload(self) -> None:
        """Clean up when cog is unloaded."""
        self.logger.info("VoiceTracker cog is being unloaded, cleaning up...")

        self.daily_reset.cancel()
        self.save_task.cancel()

        sessions_ended = 0
        for user_id in list(self.sessions.keys()):
            guild_id = self.sessions[user_id]["guild_id"]
            guild = self.bot.get_guild(int(guild_id))

            if guild:
                member = guild.get_member(int(user_id))

                if member:
                    self.end_session(member)
                    sessions_ended += 1

        self.logger.info(f"Ended {sessions_ended} sessions during cog unload")
        self.save_data()
        self.logger.info("VoiceTracker cog unloaded successfully")
