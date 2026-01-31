"""Document storage and retrieval using PostgreSQL."""

import json
from dataclasses import dataclass

from src.database import get_pool
from src.docs.scraper import DocPage, DocSection


@dataclass
class DocInfo:
    """Lightweight doc info (no content)."""

    slug: str
    title: str
    url: str


class DocStore:
    """Manages stored documentation in PostgreSQL."""

    async def get_manifest(self) -> list[DocInfo]:
        """Get list of all available docs (titles only, no content)."""
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT slug, title, url FROM doc_pages ORDER BY title"
            )
            return [DocInfo(slug=r["slug"], title=r["title"], url=r["url"]) for r in rows]

    async def get_doc_titles_for_prompt(self) -> str:
        """Get formatted list of doc titles for system prompt."""
        docs = await self.get_manifest()
        if not docs:
            return "No documentation available. Run /scrape command first."

        lines = ["Available Xenon documentation pages:"]
        for doc in docs:
            lines.append(f"- {doc.title} (slug: {doc.slug})")
        return "\n".join(lines)

    async def get_doc(self, slug: str) -> DocPage | None:
        """Get full document content by slug."""
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT slug, title, url, sections FROM doc_pages WHERE slug = $1",
                slug,
            )
            if not row:
                return None

            sections_data = row["sections"]
            if isinstance(sections_data, str):
                sections_data = json.loads(sections_data)

            return DocPage(
                slug=row["slug"],
                title=row["title"],
                url=row["url"],
                sections=[DocSection(**s) for s in sections_data],
            )

    async def get_doc_text(self, slug: str) -> str | None:
        """Get document as formatted text."""
        doc = await self.get_doc(slug)
        if not doc:
            return None
        return doc.full_text

    async def get_all_docs(self) -> list[DocPage]:
        """Get all documents with full content."""
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT slug, title, url, sections FROM doc_pages ORDER BY title"
            )
            docs = []
            for row in rows:
                sections_data = row["sections"]
                if isinstance(sections_data, str):
                    sections_data = json.loads(sections_data)

                docs.append(
                    DocPage(
                        slug=row["slug"],
                        title=row["title"],
                        url=row["url"],
                        sections=[DocSection(**s) for s in sections_data],
                    )
                )
            return docs

    async def is_initialized(self) -> bool:
        """Check if docs have been scraped."""
        pool = await get_pool()
        async with pool.acquire() as conn:
            count = await conn.fetchval("SELECT COUNT(*) FROM doc_pages")
            return count > 0

    async def save_doc(self, doc: DocPage) -> None:
        """Save a document to the database."""
        pool = await get_pool()
        async with pool.acquire() as conn:
            sections_json = json.dumps([{"heading": s.heading, "content": s.content} for s in doc.sections])
            await conn.execute(
                """
                INSERT INTO doc_pages (slug, title, url, sections, scraped_at)
                VALUES ($1, $2, $3, $4, NOW())
                ON CONFLICT (slug) DO UPDATE SET
                    title = $2,
                    url = $3,
                    sections = $4,
                    scraped_at = NOW()
                """,
                doc.slug,
                doc.title,
                doc.url,
                sections_json,
            )

    async def clear_all(self) -> None:
        """Clear all documents from the database."""
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM doc_pages")


# Global instance
doc_store = DocStore()
