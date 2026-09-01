"""PostgreSQL lifecycle and idempotent schema migrations."""

import asyncio

import asyncpg

from src.config import get_settings

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS server_configs (
    guild_id BIGINT PRIMARY KEY,
    support_role_id BIGINT,
    ticket_channel_id BIGINT,
    ephemeral_processing BOOLEAN NOT NULL DEFAULT FALSE,
    support_channel_id BIGINT,
    menu_message_id BIGINT,
    community_support_channel_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS doc_pages (
    slug TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    sections JSONB NOT NULL DEFAULT '[]',
    scraped_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS doc_sections (
    page_slug TEXT NOT NULL REFERENCES doc_pages(slug) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    title TEXT NOT NULL,
    heading TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    url TEXT NOT NULL,
    PRIMARY KEY (page_slug, position)
);

CREATE TABLE IF NOT EXISTS questions (
    id BIGSERIAL PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    question TEXT NOT NULL,
    answered BOOLEAN NOT NULL DEFAULT FALSE,
    community_support_clicked BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tool_calls (
    id BIGSERIAL PRIMARY KEY,
    question_id BIGINT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    tool_name TEXT NOT NULL,
    arguments JSONB,
    result JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_questions_guild_created
    ON questions(guild_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_questions_unanswered
    ON questions(guild_id, created_at DESC) WHERE answered = FALSE;
CREATE INDEX IF NOT EXISTS idx_tool_calls_question ON tool_calls(question_id);
CREATE INDEX IF NOT EXISTS idx_doc_sections_search ON doc_sections USING GIN (
    to_tsvector(
        'english',
        coalesce(title, '') || ' ' || coalesce(heading, '') || ' ' || content
    )
);

INSERT INTO doc_sections (page_slug, position, title, heading, content, url)
SELECT
    page.slug,
    section.ordinality - 1,
    page.title,
    coalesce(section.value->>'heading', ''),
    coalesce(section.value->>'content', ''),
    page.url
FROM doc_pages AS page
CROSS JOIN LATERAL jsonb_array_elements(page.sections) WITH ORDINALITY AS section(value, ordinality)
ON CONFLICT (page_slug, position) DO NOTHING;
"""


class Database:
    """Owns one asyncpg pool and exposes it behind a small lifecycle interface."""

    def __init__(self) -> None:
        self._database_url: str | None = None
        self._pool: asyncpg.Pool | None = None
        self._lock = asyncio.Lock()

    def configure(self, database_url: str) -> None:
        if self._pool is not None and self._database_url != database_url:
            raise RuntimeError("Cannot change DATABASE_URL after the pool has started")
        self._database_url = database_url

    async def connect(self) -> asyncpg.Pool:
        if self._pool is not None:
            return self._pool

        async with self._lock:
            if self._pool is None:
                database_url = self._database_url or get_settings().database_url
                self._pool = await asyncpg.create_pool(
                    database_url,
                    min_size=1,
                    max_size=10,
                    command_timeout=30,
                )
        return self._pool

    async def initialize(self) -> None:
        pool = await self.connect()
        async with pool.acquire() as connection, connection.transaction():
            await connection.execute(SCHEMA_SQL)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


database = Database()


async def get_pool() -> asyncpg.Pool:
    """Compatibility helper for storage modules."""
    return await database.connect()


async def init_schema() -> None:
    await database.initialize()


async def close_pool() -> None:
    await database.close()
