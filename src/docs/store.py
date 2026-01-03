"""Document storage and retrieval."""

import json
from dataclasses import dataclass
from pathlib import Path

from src.docs.scraper import DocPage, DEFAULT_DATA_DIR


@dataclass
class DocInfo:
    """Lightweight doc info (no content)."""

    slug: str
    title: str
    url: str


class DocStore:
    """Manages stored documentation."""

    def __init__(self, docs_dir: Path | None = None):
        self.docs_dir = docs_dir or (DEFAULT_DATA_DIR / "docs")

    def get_manifest(self) -> list[DocInfo]:
        """Get list of all available docs (titles only, no content)."""
        manifest_path = self.docs_dir / "manifest.json"
        if not manifest_path.exists():
            return []

        data = json.loads(manifest_path.read_text())
        return [DocInfo(**item) for item in data]

    def get_doc_titles_for_prompt(self) -> str:
        """Get formatted list of doc titles for system prompt."""
        docs = self.get_manifest()
        if not docs:
            return "No documentation available. Run /scrape command first."

        lines = ["Available Xenon documentation pages:"]
        for doc in docs:
            lines.append(f"- {doc.title} (slug: {doc.slug})")
        return "\n".join(lines)

    def get_doc(self, slug: str) -> DocPage | None:
        """Get full document content by slug."""
        doc_path = self.docs_dir / f"{slug}.json"
        if not doc_path.exists():
            return None

        data = json.loads(doc_path.read_text())
        return DocPage.from_dict(data)

    def get_doc_text(self, slug: str) -> str | None:
        """Get document as formatted text."""
        doc = self.get_doc(slug)
        if not doc:
            return None
        return doc.full_text

    def get_all_docs(self) -> list[DocPage]:
        """Get all documents with full content."""
        docs = []
        for info in self.get_manifest():
            doc = self.get_doc(info.slug)
            if doc:
                docs.append(doc)
        return docs

    def is_initialized(self) -> bool:
        """Check if docs have been scraped."""
        manifest_path = self.docs_dir / "manifest.json"
        return manifest_path.exists()


# Global instance
doc_store = DocStore()
