from datetime import datetime, timedelta
from typing import Optional

import aiosqlite


# TODO(gtkacz): Switch to coleifer/peewee  # noqa: FIX002
# https://github.com/gtkacz/AlbionBot/issues/1
class VoiceDatabase:
    """
    Manages voice activity data in SQLite database.
    """

    def __init__(self, db_path: str = "voice_activity.db") -> None:
        """
        Initialize database connection.

        Args:
            db_path (str, optional): Path to SQLite database file. Defaults to "voice_activity.db".
        """
        self.db_path = db_path
        self.db: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        """
        Connect to database and create tables if needed.
        """
        self.db = await aiosqlite.connect(self.db_path)
        self.db.row_factory = aiosqlite.Row

        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                username TEXT NOT NULL
            )
        """)

        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS guilds (
                guild_id TEXT PRIMARY KEY,
                guild_name TEXT NOT NULL
            )
        """)

        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS voice_sessions (
                session_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                guild_id TEXT NOT NULL,
                start_time TIMESTAMP NOT NULL,
                end_time TIMESTAMP,
                duration_seconds REAL,
                FOREIGN KEY (user_id) REFERENCES users (user_id),
                FOREIGN KEY (guild_id) REFERENCES guilds (guild_id)
            )
        """)

        await self.db.execute("""
            CREATE INDEX IF NOT EXISTS idx_sessions_date
            ON voice_sessions(date(start_time))
        """)

        await self.db.execute("""
            CREATE INDEX IF NOT EXISTS idx_sessions_user_guild
            ON voice_sessions(user_id, guild_id)
        """)

        await self.db.commit()

    async def close(self) -> None:
        """
        Close database connection.
        """
        if self.db:
            await self.db.close()

    async def upsert_user(self, user_id: str, username: str) -> None:
        """
        Insert or update user.

        Args:
            user_id: Discord user ID.
            username: Discord username.
        """
        await self.db.execute("INSERT OR REPLACE INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
        await self.db.commit()

    async def upsert_guild(self, guild_id: str, guild_name: str) -> None:
        """
        Insert or update guild.

        Args:
            guild_id: Discord guild ID.
            guild_name: Discord guild name.
        """
        await self.db.execute(
            "INSERT OR REPLACE INTO guilds (guild_id, guild_name) VALUES (?, ?)", (guild_id, guild_name),
        )
        await self.db.commit()

    async def start_session(self, user_id: str, guild_id: str, username: str, guild_name: str) -> int:
        """
        Start a new voice session.

        Args:
            user_id: Discord user ID.
            guild_id: Discord guild ID.
            username: Discord username.
            guild_name: Discord guild name.

        Returns:
            Session ID of the created session.
        """
        await self.upsert_user(user_id, username)
        await self.upsert_guild(guild_id, guild_name)

        cursor = await self.db.execute(
            """INSERT INTO voice_sessions (user_id, guild_id, start_time)
               VALUES (?, ?, ?)""",
            (user_id, guild_id, datetime.now()),
        )
        await self.db.commit()
        return cursor.lastrowid

    async def end_session(self, session_id: int) -> None:
        """
        End a voice session.

        Args:
            session_id: ID of the session to end.
        """
        end_time = datetime.now()

        cursor = await self.db.execute("SELECT start_time FROM voice_sessions WHERE session_id = ?", (session_id,))
        row = await cursor.fetchone()

        if row:
            start_time = datetime.fromisoformat(row["start_time"])
            duration = (end_time - start_time).total_seconds()

            await self.db.execute(
                """UPDATE voice_sessions
                   SET end_time = ?, duration_seconds = ?
                   WHERE session_id = ?""",
                (end_time, duration, session_id),
            )
            await self.db.commit()

    async def get_user_time_for_date(self, user_id: str, guild_id: str, date: str) -> float:
        """
        Get total voice time for user on specific date.

        Args:
            user_id: Discord user ID.
            guild_id: Discord guild ID.
            date: Date in YYYY-MM-DD format.

        Returns:
            Total seconds of voice time.
        """
        cursor = await self.db.execute(
            """SELECT COALESCE(SUM(duration_seconds), 0) as total
               FROM voice_sessions
               WHERE user_id = ? AND guild_id = ?
               AND date(start_time) = ?""",
            (user_id, guild_id, date),
        )
        row = await cursor.fetchone()
        return row["total"] if row else 0.0

    async def get_guild_stats(self, guild_id: str, days: int) -> list[dict[str, any]]:
        """
        Get voice statistics for guild over specified days.

        Args:
            guild_id: Discord guild ID.
            days: Number of days to look back.

        Returns:
            List of user statistics.
        """
        start_date = (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")

        cursor = await self.db.execute(
            """SELECT u.user_id, u.username,
                      COALESCE(SUM(v.duration_seconds), 0) as total_seconds
               FROM users u
               LEFT JOIN voice_sessions v ON u.user_id = v.user_id
               WHERE v.guild_id = ?
               AND date(v.start_time) >= ?
               GROUP BY u.user_id, u.username
               ORDER BY total_seconds DESC""",
            (guild_id, start_date),
        )

        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def cleanup_old_data(self, days: int = 30) -> None:
        """
        Remove sessions older than specified days.

        Args:
            days: Number of days to keep.
        """
        cutoff_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        await self.db.execute("DELETE FROM voice_sessions WHERE date(start_time) < ?", (cutoff_date,))
        await self.db.commit()

    async def get_active_session_time(self, session_id: int) -> float:
        """
        Get current duration of an active session.

        Args:
            session_id: ID of the active session.

        Returns:
            Current duration in seconds.
        """
        cursor = await self.db.execute(
            "SELECT start_time FROM voice_sessions WHERE session_id = ? AND end_time IS NULL", (session_id,),
        )
        row = await cursor.fetchone()

        if row:
            start_time = datetime.fromisoformat(row["start_time"])
            return (datetime.now() - start_time).total_seconds()
        return 0.0
