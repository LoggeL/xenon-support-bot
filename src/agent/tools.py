"""Agent tools for Xenon support bot."""

from typing import Any

from src.agent.client import Tool
from src.docs.store import doc_store
from src.docs.search import doc_search


# Tool definitions for the LLM
TOOLS: list[Tool] = [
    Tool(
        name="check_relevance",
        description=(
            "Check if the user's question is relevant to Xenon bot support. "
            "Call this FIRST before any other tool. Returns true if the question "
            "is about Xenon (backups, templates, sync, premium, commands, etc.), "
            "false otherwise. If false, you should not answer the question."
        ),
        parameters={
            "type": "object",
            "properties": {
                "reasoning": {
                    "type": "string",
                    "description": "Brief reasoning about why this is or isn't about Xenon",
                },
                "is_relevant": {
                    "type": "boolean",
                    "description": "True if the question is about Xenon bot, false otherwise",
                },
            },
            "required": ["reasoning", "is_relevant"],
        },
    ),
    Tool(
        name="search_docs",
        description=(
            "Full-text search across all Xenon documentation. "
            "Use this to find relevant sections when you don't know which doc to look at. "
            "Returns matching sections with snippets."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (keywords or phrases)",
                },
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="get_doc",
        description=(
            "Get the full content of a specific documentation page by its slug. "
            "Use this when you know which doc you need or after search_docs identifies it."
        ),
        parameters={
            "type": "object",
            "properties": {
                "slug": {
                    "type": "string",
                    "description": "The document slug (e.g., 'backups', 'templates', 'faq')",
                },
            },
            "required": ["slug"],
        },
    ),
]


def execute_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Execute a tool and return the result."""

    if name == "check_relevance":
        # This tool is evaluated by the LLM itself - we just return its decision
        return {
            "is_relevant": arguments.get("is_relevant", False),
            "reasoning": arguments.get("reasoning", ""),
        }

    elif name == "search_docs":
        query = arguments.get("query", "")
        if not query:
            return {"error": "No query provided"}

        results = doc_search.search(query, limit=5)
        if not results:
            return {"results": [], "message": "No matching documentation found"}

        return {
            "results": [
                {
                    "slug": r["slug"],
                    "title": r["title"],
                    "heading": r["heading"],
                    "snippet": r["snippet"],
                }
                for r in results
            ]
        }

    elif name == "get_doc":
        slug = arguments.get("slug", "")
        if not slug:
            return {"error": "No slug provided"}

        text = doc_store.get_doc_text(slug)
        if not text:
            available = [d.slug for d in doc_store.get_manifest()]
            return {
                "error": f"Document '{slug}' not found",
                "available_slugs": available,
            }

        return {"slug": slug, "content": text}

    else:
        return {"error": f"Unknown tool: {name}"}


def get_tool_emoji(name: str) -> str:
    """Get emoji for a tool to display in Discord."""
    emojis = {
        "check_relevance": "🤔",
        "search_docs": "🔍",
        "get_doc": "📖",
    }
    return emojis.get(name, "🔧")


def get_tool_description(name: str, arguments: dict[str, Any]) -> str:
    """Get human-readable description of a tool call."""
    if name == "check_relevance":
        return "Checking if this question is about Xenon..."

    elif name == "search_docs":
        query = arguments.get("query", "")
        return f'Searching docs for "{query}"...'

    elif name == "get_doc":
        slug = arguments.get("slug", "")
        return f'Reading "{slug}" documentation...'

    return f"Running {name}..."
