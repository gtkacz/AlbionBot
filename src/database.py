from datetime import datetime, timedelta
from typing import Optional

import peewee
from peewee import (
    BooleanField,
    CharField,
    DateTimeField,
    FloatField,
    ForeignKeyField,
    Model,
    SqliteDatabase,
)

from custom_types import GuildStat

database = SqliteDatabase(None)


class _BaseModel(Model):
    class Meta:
        database = database


class User(_BaseModel):
    """An user in the database."""

    user_id = CharField(primary_key=True)
    username = CharField()


class Guild(_BaseModel):
    """A guild (server) in the database."""

    guild_id = CharField(primary_key=True)
    guild_name = CharField()


class Channel(_BaseModel):
    """A voice channel in the database."""

    channel_id = CharField(primary_key=True)
    channel_name = CharField()
    guild = ForeignKeyField(Guild, backref="channels")


class VoiceSession(_BaseModel):
    """A voice session in the database."""

    user = ForeignKeyField(User, backref="sessions")
    guild = ForeignKeyField(Guild, backref="sessions")
    channel = ForeignKeyField(Channel, backref="sessions")
    start_time = DateTimeField()
    end_time = DateTimeField(null=True)
    duration_seconds = FloatField(null=True)
    is_active = BooleanField(default=True)

    class Meta:  # noqa: D106
        indexes = ((("user", "guild"), False),)


class VoiceDatabase:
    """Database handler for voice activity tracking."""

    def __init__(self, db_path: str = "voice_activity.db") -> None:
        """
        Initialize the database connection.

        Args:
            db_path (optional): Path to the SQLite database file. Defaults to "voice_activity.db".
        """
        self.db_path = db_path
        database.init(db_path)

    @staticmethod
    async def connect() -> None:
        """Connect to the database and create tables if they don't exist."""
        database.connect()
        database.create_tables([User, Guild, Channel, VoiceSession])

    @staticmethod
    async def close() -> None:
        """Close the database connection."""
        database.close()

    @staticmethod
    async def upsert_user(user_id: str, username: str) -> None:
        """Insert or update a user in the database."""
        User.insert(user_id=user_id, username=username).on_conflict_replace().execute()

    @staticmethod
    async def upsert_guild(guild_id: str, guild_name: str) -> None:
        """Insert or update a guild in the database."""
        Guild.insert(guild_id=guild_id, guild_name=guild_name).on_conflict_replace().execute()

    @staticmethod
    async def upsert_channel(channel_id: str, channel_name: str, guild_id: str) -> None:
        """Insert or update a channel in the database."""
        guild = Guild.get(Guild.guild_id == guild_id)
        Channel.insert(channel_id=channel_id, channel_name=channel_name, guild=guild).on_conflict_replace().execute()

    async def start_session(  # noqa: PLR0913, PLR0917
        self,
        user_id: str,
        guild_id: str,
        channel_id: str,
        username: str,
        guild_name: str,
        channel_name: str,
        *,
        is_active: bool = True,
    ) -> int:
        """
        Start a new voice session.

        Args:
            user_id: The ID of the user.
            guild_id: The ID of the guild.
            channel_id: The ID of the channel.
            username: The username of the user.
            guild_name: The name of the guild.
            channel_name: The name of the channel.
            is_active (optional): Whether the session is active. Defaults to True.

        Returns:
            int: The ID of the created voice session.
        """
        await self.upsert_user(user_id, username)
        await self.upsert_guild(guild_id, guild_name)
        await self.upsert_channel(channel_id, channel_name, guild_id)

        user = User.get(User.user_id == user_id)
        guild = Guild.get(Guild.guild_id == guild_id)
        channel = Channel.get(Channel.channel_id == channel_id)

        session = VoiceSession.create(
            user=user,
            guild=guild,
            channel=channel,
            start_time=datetime.now(),
            is_active=is_active,
        )

        return session.id

    @staticmethod
    async def end_session(session_id: int) -> None:
        """
        End a voice session.

        Args:
            session_id: The ID of the voice session to end.
        """
        session = VoiceSession.get_by_id(session_id)
        end_time = datetime.now()
        duration = (end_time - session.start_time).total_seconds()

        session.end_time = end_time
        session.duration_seconds = duration
        session.save()

    @staticmethod
    async def get_user_time_for_date(
        user_id: str,
        guild_id: str,
        date: str,
        *,
        is_active: Optional[bool] = None,
    ) -> float:
        """
        Get the total voice time for a user on a specific date.

        Args:
            user_id: The ID of the user.
            guild_id: The ID of the guild.
            date: The date in 'YYYY-MM-DD' format.
            is_active (optional): Whether to filter by active sessions. Defaults to None.

        Returns:
            float: Total voice time in seconds.
        """
        query = (
            VoiceSession.select(peewee.fn.Coalesce(peewee.fn.Sum(VoiceSession.duration_seconds), 0).alias("total"))
            .join(User)
            .switch(VoiceSession)
            .join(Guild)
            .where(
                (User.user_id == user_id)
                & (Guild.guild_id == guild_id)
                & (peewee.fn.Date(VoiceSession.start_time) == date),
            )
        )

        if is_active is not None:
            query = query.where(VoiceSession.is_active == is_active)

        return query.scalar() or 0.0

    @staticmethod
    async def get_guild_stats(guild_id: str, days: int) -> list[GuildStat]:
        """
        Get total voice time for all users in a guild over the past specified days.

        Args:
            guild_id: The ID of the guild.
            days: Number of days to look back.

        Returns:
            list[GuildStat]: The list of user stats sorted by total voice time in descending order.
        """
        start_date = (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")

        query = (
            User.select(
                User.user_id,
                User.username,
                peewee.fn.Coalesce(peewee.fn.Sum(VoiceSession.duration_seconds), 0).alias("total_seconds"),
            )
            .join(VoiceSession, peewee.JOIN.LEFT_OUTER)
            .join(Guild)
            .where((Guild.guild_id == guild_id) & (peewee.fn.Date(VoiceSession.start_time) >= start_date))
            .group_by(User.user_id, User.username)
            .order_by(peewee.fn.Sum(VoiceSession.duration_seconds).desc())
        )

        return [{"user_id": r.user_id, "username": r.username, "total_seconds": r.total_seconds} for r in query]

    @staticmethod
    async def cleanup_old_data(days: int = 30) -> None:
        """
        Delete voice sessions older than the specified number of days.

        Args:
            days (optional): Number of days to retain data. Defaults to 30.
        """
        cutoff_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        VoiceSession.delete().where(peewee.fn.Date(VoiceSession.start_time) < cutoff_date).execute()

    @staticmethod
    async def get_active_session_time(session_id: int) -> float:
        """
        Get the elapsed time for an active session.

        Args:
            session_id: The ID of the voice session.

        Returns:
            float: Elapsed time in seconds, or 0.0 if the session does not exist or is not active.
        """
        try:
            session = VoiceSession.get((VoiceSession.id == session_id) & (VoiceSession.end_time.is_null()))
            return (datetime.now() - session.start_time).total_seconds()

        except VoiceSession.DoesNotExist:
            return 0.0
