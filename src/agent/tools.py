"""Documentation tools available to the support agent."""

from typing import Any

from src.agent.client import Tool
from src.docs.search import doc_search
from src.docs.store import doc_store

TOOLS: list[Tool] = [
    Tool(
        name="search_docs",
        description=(
            "Search official Xenon documentation. Use concise keywords and inspect the most "
            "relevant full page with get_doc before answering."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Concise Xenon documentation search query.",
                    "minLength": 1,
                    "maxLength": 200,
                }
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="get_doc",
        description="Read one complete official Xenon documentation page by slug.",
        parameters={
            "type": "object",
            "properties": {
                "slug": {
                    "type": "string",
                    "description": "Exact slug returned by search_docs or the manifest.",
                    "minLength": 1,
                    "maxLength": 100,
                }
            },
            "required": ["slug"],
            "additionalProperties": False,
        },
    ),
]


async def execute_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Execute one allowlisted read-only documentation tool."""
    if name == "search_docs":
        query = str(arguments.get("query", "")).strip()
        if not query:
            return {"error": "A non-empty query is required."}

        results = await doc_search.search(query, limit=5)
        return {
            "results": [
                {
                    "slug": result.slug,
                    "title": result.title,
                    "heading": result.heading,
                    "snippet": result.snippet,
                    "url": result.url,
                }
                for result in results
            ]
        }

    if name == "get_doc":
        slug = str(arguments.get("slug", "")).strip()
        if not slug:
            return {"error": "A non-empty slug is required."}

        document = await doc_store.get_doc(slug)
        if document is None:
            manifest = await doc_store.get_manifest()
            return {
                "error": f"Document '{slug}' was not found.",
                "available_slugs": [item.slug for item in manifest],
            }

        return {
            "slug": document.slug,
            "title": document.title,
            "url": document.url,
            "content": document.full_text,
        }

    return {"error": f"Unknown tool: {name}"}


def get_tool_emoji(name: str) -> str:
    return {"search_docs": "🔍", "get_doc": "📖"}.get(name, "🔧")


def get_tool_description(name: str, arguments: dict[str, Any]) -> str:
    if name == "search_docs":
        return f'Searching docs for "{arguments.get("query", "")}"…'
    if name == "get_doc":
        return f'Reading "{arguments.get("slug", "")}" documentation…'
    return f"Running {name}…"
