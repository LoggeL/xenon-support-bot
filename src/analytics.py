"""Analytics storage using PostgreSQL for question tracking."""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from src.database import get_pool


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
    """PostgreSQL-based analytics for question tracking."""

    async def log_question(
        self,
        guild_id: int,
        user_id: int,
        channel_id: int,
        question: str,
    ) -> int:
        """Log a new question, returns question_id."""
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO questions (guild_id, user_id, channel_id, question)
                VALUES ($1, $2, $3, $4)
                RETURNING id
                """,
                guild_id,
                user_id,
                channel_id,
                question,
            )
            return row["id"]

    async def log_tool_call(
        self,
        question_id: int,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        """Log a tool call for a question."""
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO tool_calls (question_id, tool_name, arguments, result)
                VALUES ($1, $2, $3, $4)
                """,
                question_id,
                tool_name,
                json.dumps(arguments),
                json.dumps(result),
            )

    async def mark_answered(self, question_id: int) -> None:
        """Mark a question as answered."""
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE questions SET answered = TRUE WHERE id = $1",
                question_id,
            )

    async def mark_community_support(self, question_id: int) -> None:
        """Mark that community support was clicked."""
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE questions SET community_support_clicked = TRUE WHERE id = $1",
                question_id,
            )

    async def get_unanswered(
        self,
        guild_id: int,
        days: int = 7,
        limit: int = 10,
    ) -> list[QuestionRecord]:
        """Get recent unanswered questions."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM questions
                WHERE guild_id = $1 AND answered = FALSE AND created_at >= $2
                ORDER BY created_at DESC
                LIMIT $3
                """,
                guild_id,
                cutoff,
                limit,
            )
            return [
                QuestionRecord(
                    id=row["id"],
                    guild_id=row["guild_id"],
                    user_id=row["user_id"],
                    channel_id=row["channel_id"],
                    question=row["question"],
                    answered=row["answered"],
                    community_support_clicked=row["community_support_clicked"],
                    created_at=row["created_at"],
                )
                for row in rows
            ]

    async def get_stats(self, guild_id: int, days: int = 7) -> dict[str, Any]:
        """Get analytics stats for a guild."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        pool = await get_pool()
        async with pool.acquire() as conn:
            # Total questions
            total = await conn.fetchval(
                "SELECT COUNT(*) FROM questions WHERE guild_id = $1 AND created_at >= $2",
                guild_id,
                cutoff,
            )

            # Answered questions
            answered = await conn.fetchval(
                """
                SELECT COUNT(*) FROM questions
                WHERE guild_id = $1 AND answered = TRUE AND created_at >= $2
                """,
                guild_id,
                cutoff,
            )

            # Community support clicked
            community_clicked = await conn.fetchval(
                """
                SELECT COUNT(*) FROM questions
                WHERE guild_id = $1 AND community_support_clicked = TRUE AND created_at >= $2
                """,
                guild_id,
                cutoff,
            )

            return {
                "total": total,
                "answered": answered,
                "unanswered": total - answered,
                "answer_rate": (answered / total * 100) if total > 0 else 0,
                "community_support_clicked": community_clicked,
                "days": days,
            }

    async def get_global_stats(self) -> dict[str, Any]:
        """Get global analytics stats across all guilds."""
        pool = await get_pool()
        async with pool.acquire() as conn:
            # Total questions all time
            total_questions = await conn.fetchval("SELECT COUNT(*) FROM questions")

            # Answered questions all time
            total_answered = await conn.fetchval(
                "SELECT COUNT(*) FROM questions WHERE answered = TRUE"
            )

            # Total tool calls
            total_tool_calls = await conn.fetchval("SELECT COUNT(*) FROM tool_calls")

            # Unique users
            unique_users = await conn.fetchval(
                "SELECT COUNT(DISTINCT user_id) FROM questions"
            )

            # Unique guilds
            unique_guilds = await conn.fetchval(
                "SELECT COUNT(DISTINCT guild_id) FROM questions"
            )

            # Questions today
            today = datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            questions_today = await conn.fetchval(
                "SELECT COUNT(*) FROM questions WHERE created_at >= $1",
                today,
            )

            # Questions this week
            week_ago = datetime.now(timezone.utc) - timedelta(days=7)
            questions_week = await conn.fetchval(
                "SELECT COUNT(*) FROM questions WHERE created_at >= $1",
                week_ago,
            )

            return {
                "total_questions": total_questions,
                "total_answered": total_answered,
                "total_tool_calls": total_tool_calls,
                "unique_users": unique_users,
                "unique_guilds": unique_guilds,
                "questions_today": questions_today,
                "questions_week": questions_week,
                "answer_rate": (
                    (total_answered / total_questions * 100) if total_questions > 0 else 0
                ),
            }


# Global instance
analytics = Analytics()
