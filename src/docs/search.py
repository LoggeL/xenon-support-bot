"""PostgreSQL full-text search over Xenon documentation."""

from dataclasses import dataclass

from src.database import get_pool


@dataclass(frozen=True, slots=True)
class SearchResult:
    slug: str
    title: str
    heading: str
    snippet: str
    url: str
    score: float


class DocSearch:
    """Search adapter backed by the same durable store as scraped documents."""

    async def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        query = query.strip()
        if not query:
            return []

        safe_limit = max(1, min(limit, 10))
        pool = await get_pool()
        async with pool.acquire() as connection:
            rows = await connection.fetch(
                """
                WITH ranked AS (
                    SELECT
                        page_slug,
                        title,
                        heading,
                        content,
                        url,
                        ts_rank_cd(
                            to_tsvector(
                                'english',
                                coalesce(title, '') || ' ' ||
                                coalesce(heading, '') || ' ' || content
                            ),
                            websearch_to_tsquery('english', $1)
                        ) AS score
                    FROM doc_sections
                    WHERE to_tsvector(
                        'english',
                        coalesce(title, '') || ' ' ||
                        coalesce(heading, '') || ' ' || content
                    ) @@ websearch_to_tsquery('english', $1)
                )
                SELECT page_slug, title, heading, content, url, score
                FROM ranked
                ORDER BY score DESC, title, heading
                LIMIT $2
                """,
                query,
                safe_limit,
            )

            if not rows:
                rows = await connection.fetch(
                    """
                    SELECT page_slug, title, heading, content, url, 0.0 AS score
                    FROM doc_sections
                    WHERE title ILIKE '%' || $1 || '%'
                       OR heading ILIKE '%' || $1 || '%'
                       OR content ILIKE '%' || $1 || '%'
                    ORDER BY title, heading
                    LIMIT $2
                    """,
                    query,
                    safe_limit,
                )

        return [
            SearchResult(
                slug=row["page_slug"],
                title=row["title"],
                heading=row["heading"],
                snippet=_snippet(row["content"]),
                url=row["url"],
                score=float(row["score"]),
            )
            for row in rows
        ]

    async def rebuild_index(self) -> int:
        """Synchronize searchable rows from the canonical JSON document records."""
        pool = await get_pool()
        async with pool.acquire() as connection, connection.transaction():
            await connection.execute("TRUNCATE doc_sections")
            status = await connection.execute(
                """
                INSERT INTO doc_sections (page_slug, position, title, heading, content, url)
                SELECT
                    page.slug,
                    section.ordinality - 1,
                    page.title,
                    coalesce(section.value->>'heading', ''),
                    coalesce(section.value->>'content', ''),
                    page.url
                FROM doc_pages AS page
                CROSS JOIN LATERAL jsonb_array_elements(page.sections)
                    WITH ORDINALITY AS section(value, ordinality)
                """
            )
        return int(status.rsplit(" ", 1)[-1])


def _snippet(content: str, limit: int = 420) -> str:
    compact = " ".join(content.split())
    return compact if len(compact) <= limit else f"{compact[: limit - 1].rstrip()}…"


doc_search = DocSearch()
