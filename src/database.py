"""PostgreSQL database connection pool and schema management."""

import asyncpg
from src.config import settings


_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    """Get or create the database connection pool."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            settings.database_url,
            min_size=2,
            max_size=10,
        )
    return _pool


async def close_pool() -> None:
    """Close the database connection pool."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


async def init_schema() -> None:
    """Initialize database schema."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Server configs table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS server_configs (
                guild_id BIGINT PRIMARY KEY,
                support_role_id BIGINT,
                ticket_channel_id BIGINT,
                ephemeral_processing BOOLEAN NOT NULL DEFAULT FALSE,
                support_channel_id BIGINT,
                menu_message_id BIGINT,
                community_support_channel_id BIGINT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # Documentation pages table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS doc_pages (
                slug TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                sections JSONB NOT NULL DEFAULT '[]',
                scraped_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # Questions table (analytics)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS questions (
                id SERIAL PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                channel_id BIGINT NOT NULL,
                question TEXT NOT NULL,
                answered BOOLEAN NOT NULL DEFAULT FALSE,
                community_support_clicked BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # Tool calls table (analytics)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS tool_calls (
                id SERIAL PRIMARY KEY,
                question_id INTEGER NOT NULL REFERENCES questions(id),
                tool_name TEXT NOT NULL,
                arguments JSONB,
                result JSONB,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # Create indexes
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_questions_guild ON questions(guild_id)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_questions_answered ON questions(answered)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_questions_created ON questions(created_at)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tool_calls_question ON tool_calls(question_id)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_doc_pages_title ON doc_pages(title)"
        )
