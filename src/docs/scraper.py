"""Scrapes Xenon wiki documentation."""

import asyncio
import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path

import httpx
from bs4 import BeautifulSoup


WIKI_BASE = "https://wiki.xenon.bot"
DEFAULT_DATA_DIR = Path(__file__).parent.parent.parent / "data"

DOC_PAGES = [
    ("home", "/en/home"),
    ("faq", "/en/faq"),
    ("premium", "/en/premium"),
    ("api", "/en/api"),
    ("backups", "/en/backups"),
    ("templates", "/en/templates"),
    ("chatlog", "/en/chatlog"),
    ("sync", "/en/sync"),
    ("whitelabel", "/en/whitelabel"),
    ("import", "/en/import"),
    ("export", "/en/export"),
    ("settings", "/en/settings"),
]


@dataclass
class DocSection:
    heading: str
    content: str


@dataclass
class DocPage:
    slug: str
    title: str
    url: str
    sections: list[DocSection]

    def to_dict(self) -> dict:
        return {
            "slug": self.slug,
            "title": self.title,
            "url": self.url,
            "sections": [asdict(s) for s in self.sections],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DocPage":
        return cls(
            slug=data["slug"],
            title=data["title"],
            url=data["url"],
            sections=[DocSection(**s) for s in data["sections"]],
        )

    @property
    def full_text(self) -> str:
        parts = [f"# {self.title}\n"]
        for section in self.sections:
            if section.heading:
                parts.append(f"\n## {section.heading}\n")
            parts.append(section.content)
        return "\n".join(parts)


def extract_content_html(html: str) -> str:
    """Extract content from template slot='contents' using regex."""
    # The template content may contain nested templates, so we need to be careful
    # Find the start of our template
    start_pattern = r'<template\s+slot="contents">'
    start_match = re.search(start_pattern, html)
    if not start_match:
        return ""

    start_pos = start_match.end()

    # Find matching </template> by counting nesting
    depth = 1
    pos = start_pos
    while depth > 0 and pos < len(html):
        next_open = html.find("<template", pos)
        next_close = html.find("</template>", pos)

        if next_close == -1:
            break

        if next_open != -1 and next_open < next_close:
            depth += 1
            pos = next_open + 9
        else:
            depth -= 1
            if depth == 0:
                return html[start_pos:next_close]
            pos = next_close + 11

    return html[start_pos:]


async def scrape_page(client: httpx.AsyncClient, slug: str, path: str) -> DocPage | None:
    """Scrape a single wiki page."""
    url = f"{WIKI_BASE}{path}"
    try:
        resp = await client.get(url, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        print(f"Failed to fetch {url}: {e}")
        return None

    html = resp.text

    # Extract title from page element attribute
    title_match = re.search(r'<page[^>]+title="([^"]+)"', html)
    if title_match:
        title = title_match.group(1)
    else:
        title = slug.replace("-", " ").title()

    # Extract content HTML from template
    content_html = extract_content_html(html)
    if not content_html:
        print(f"No content found for {slug}")
        return DocPage(slug=slug, title=title, url=url, sections=[])

    # Parse the content HTML
    soup = BeautifulSoup(content_html, "html.parser")

    sections: list[DocSection] = []
    current_heading = ""
    current_content: list[str] = []

    def flush_section():
        nonlocal current_heading, current_content
        if current_content:
            text = "\n".join(current_content).strip()
            text = re.sub(r" {2,}", " ", text)
            text = re.sub(r"\n{3,}", "\n\n", text)
            if text and len(text) > 10:
                sections.append(DocSection(heading=current_heading, content=text))
        current_content = []

    # Process all elements in order
    for el in soup.find_all(["h1", "h2", "h3", "p", "ul", "ol", "blockquote", "pre", "table"]):
        if el.name in ("h1", "h2", "h3"):
            flush_section()
            heading_text = el.get_text(strip=True).lstrip("¶").strip()
            current_heading = heading_text

        elif el.name == "pre":
            code = el.get_text()
            current_content.append(f"```\n{code.strip()}\n```")

        elif el.name in ("ul", "ol"):
            for li in el.find_all("li", recursive=False):
                li_text = li.get_text(separator=" ", strip=True)
                current_content.append(f"- {li_text}")

        elif el.name == "blockquote":
            text = el.get_text(separator=" ", strip=True)
            current_content.append(f"> {text}")

        elif el.name == "table":
            # Extract table content
            rows = []
            for tr in el.find_all("tr"):
                cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
                if cells:
                    rows.append(" | ".join(cells))
            if rows:
                current_content.append("\n".join(rows))

        else:  # p and other elements
            text = el.get_text(separator=" ", strip=True)
            if text:
                current_content.append(text)

    flush_section()

    # If no sections, get all text as one section
    if not sections:
        all_text = soup.get_text(separator="\n", strip=True)
        all_text = re.sub(r"\n{3,}", "\n\n", all_text)
        if all_text and len(all_text) > 20:
            sections = [DocSection(heading="", content=all_text)]

    return DocPage(slug=slug, title=title, url=url, sections=sections)


async def scrape_all_docs() -> list[DocPage]:
    """Scrape all documentation pages and save to database."""
    from src.docs.store import doc_store

    async with httpx.AsyncClient(timeout=30.0) as client:
        tasks = [scrape_page(client, slug, path) for slug, path in DOC_PAGES]
        results = await asyncio.gather(*tasks)

    docs = [doc for doc in results if doc is not None]

    # Save each doc to PostgreSQL
    for doc in docs:
        await doc_store.save_doc(doc)

    print(f"Scraped {len(docs)} documentation pages to database")
    return docs


if __name__ == "__main__":
    asyncio.run(scrape_all_docs())
