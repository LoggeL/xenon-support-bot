"""Full-text search over documentation using Whoosh."""

from pathlib import Path

from whoosh import index
from whoosh.fields import Schema, TEXT, ID, STORED
from whoosh.qparser import MultifieldParser, OrGroup
from whoosh.analysis import StemmingAnalyzer

from src.docs.scraper import DEFAULT_DATA_DIR


# Schema for the search index
DOC_SCHEMA = Schema(
    slug=ID(stored=True),
    title=TEXT(stored=True, analyzer=StemmingAnalyzer()),
    heading=TEXT(stored=True, analyzer=StemmingAnalyzer()),
    content=TEXT(stored=True, analyzer=StemmingAnalyzer()),
    url=STORED(),
)


class DocSearch:
    """Full-text search over Xenon documentation."""

    def __init__(self, index_dir: Path | None = None):
        self.index_dir = index_dir or (DEFAULT_DATA_DIR / "index")
        self._ix: index.Index | None = None

    def _get_or_create_index(self) -> index.Index:
        """Get existing index or create new one."""
        if self._ix is not None:
            return self._ix

        self.index_dir.mkdir(parents=True, exist_ok=True)

        if index.exists_in(str(self.index_dir)):
            self._ix = index.open_dir(str(self.index_dir))
        else:
            self._ix = index.create_in(str(self.index_dir), DOC_SCHEMA)

        return self._ix

    async def rebuild_index(self) -> int:
        """Rebuild the search index from stored docs in PostgreSQL."""
        from src.docs.store import doc_store

        self.index_dir.mkdir(parents=True, exist_ok=True)

        # Always create fresh index
        ix = index.create_in(str(self.index_dir), DOC_SCHEMA)
        self._ix = ix

        writer = ix.writer()
        doc_count = 0

        for doc in await doc_store.get_all_docs():
            for section in doc.sections:
                writer.add_document(
                    slug=doc.slug,
                    title=doc.title,
                    heading=section.heading,
                    content=section.content,
                    url=doc.url,
                )
                doc_count += 1

        writer.commit()
        return doc_count

    def search(self, query: str, limit: int = 5) -> list[dict]:
        """
        Search documentation for a query.

        Returns list of matching sections with slug, title, heading, snippet.
        """
        ix = self._get_or_create_index()

        parser = MultifieldParser(
            ["title", "heading", "content"],
            schema=ix.schema,
            group=OrGroup,
        )

        try:
            parsed_query = parser.parse(query)
        except Exception:
            # If query parsing fails, try as simple phrase
            parsed_query = parser.parse(f'"{query}"')

        results = []
        with ix.searcher() as searcher:
            hits = searcher.search(parsed_query, limit=limit)

            for hit in hits:
                # Create snippet from content
                content = hit.get("content", "")
                snippet = content[:300] + "..." if len(content) > 300 else content

                results.append(
                    {
                        "slug": hit["slug"],
                        "title": hit["title"],
                        "heading": hit.get("heading", ""),
                        "snippet": snippet,
                        "score": hit.score,
                    }
                )

        return results


# Global instance
doc_search = DocSearch()
