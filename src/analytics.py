"""Analytics storage using SQLite for question tracking."""

import aiosqlite
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from src.config import settings


@dataclass
class QuestionRecord:
    """A logged question with metadata."""
    id: int
    guild_id: int
    user_id: int
    channel_id: int
    question: str
    answered: bool
    community_support_clicked: bool
    created_at: datetime


class Analytics:
    """SQLite-based analytics for question tracking."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or (settings.data_dir / "analytics.db")
        self._initialized = False

    async def init(self) -> None:
        """Initialize database schema."""
        if self._initialized:
            return

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS questions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    question TEXT NOT NULL,
                    answered INTEGER NOT NULL DEFAULT 0,
                    community_support_clicked INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS tool_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    question_id INTEGER NOT NULL REFERENCES questions(id),
                    tool_name TEXT NOT NULL,
                    arguments TEXT,
                    result TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_questions_guild ON questions(guild_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_questions_answered ON questions(answered)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_tool_calls_question ON tool_calls(question_id)"
            )
            await db.commit()

        self._initialized = True

    async def log_question(
        self,
        guild_id: int,
        user_id: int,
        channel_id: int,
        question: str,
    ) -> int:
        """Log a new question, returns question_id."""
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO questions (guild_id, user_id, channel_id, question)
                VALUES (?, ?, ?, ?)
                """,
                (guild_id, user_id, channel_id, question),
            )
            await db.commit()
            return cursor.lastrowid or 0

    async def log_tool_call(
        self,
        question_id: int,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        """Log a tool call for a question."""
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO tool_calls (question_id, tool_name, arguments, result)
                VALUES (?, ?, ?, ?)
                """,
                (question_id, tool_name, json.dumps(arguments), json.dumps(result)),
            )
            await db.commit()

    async def mark_answered(self, question_id: int) -> None:
        """Mark a question as answered."""
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE questions SET answered = 1 WHERE id = ?",
                (question_id,),
            )
            await db.commit()

    async def mark_community_support(self, question_id: int) -> None:
        """Mark that community support was clicked."""
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE questions SET community_support_clicked = 1 WHERE id = ?",
                (question_id,),
            )
            await db.commit()

    async def get_unanswered(
        self,
        guild_id: int,
        days: int = 7,
        limit: int = 10,
    ) -> list[QuestionRecord]:
        """Get recent unanswered questions."""
        await self.init()
        cutoff = datetime.utcnow() - timedelta(days=days)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT * FROM questions
                WHERE guild_id = ? AND answered = 0 AND created_at >= ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (guild_id, cutoff.isoformat(), limit),
            )
            rows = await cursor.fetchall()
            return [
                QuestionRecord(
                    id=row["id"],
                    guild_id=row["guild_id"],
                    user_id=row["user_id"],
                    channel_id=row["channel_id"],
                    question=row["question"],
                    answered=bool(row["answered"]),
                    community_support_clicked=bool(row["community_support_clicked"]),
                    created_at=datetime.fromisoformat(row["created_at"]),
                )
                for row in rows
            ]

    async def get_stats(self, guild_id: int, days: int = 7) -> dict[str, Any]:
        """Get analytics stats for a guild."""
        await self.init()
        cutoff = datetime.utcnow() - timedelta(days=days)
        async with aiosqlite.connect(self.db_path) as db:
            # Total questions
            cursor = await db.execute(
                "SELECT COUNT(*) FROM questions WHERE guild_id = ? AND created_at >= ?",
                (guild_id, cutoff.isoformat()),
            )
            total = (await cursor.fetchone())[0]

            # Answered questions
            cursor = await db.execute(
                """
                SELECT COUNT(*) FROM questions
                WHERE guild_id = ? AND answered = 1 AND created_at >= ?
                """,
                (guild_id, cutoff.isoformat()),
            )
            answered = (await cursor.fetchone())[0]

            # Community support clicked
            cursor = await db.execute(
                """
                SELECT COUNT(*) FROM questions
                WHERE guild_id = ? AND community_support_clicked = 1 AND created_at >= ?
                """,
                (guild_id, cutoff.isoformat()),
            )
            community_clicked = (await cursor.fetchone())[0]

            return {
                "total": total,
                "answered": answered,
                "unanswered": total - answered,
                "answer_rate": (answered / total * 100) if total > 0 else 0,
                "community_support_clicked": community_clicked,
                "days": days,
            }


# Global instance
analytics = Analytics()
