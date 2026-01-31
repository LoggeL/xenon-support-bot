"""Agentic loop runner - executes tools one at a time with live updates."""

import json
import re
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable, Awaitable

from src.agent.client import OpenRouterClient, Message
from src.agent.tools import TOOLS, execute_tool, get_tool_emoji, get_tool_description
from src.docs.store import doc_store


MAX_TOOL_CALLS = 10  # Prevent infinite loops


@dataclass
class ButtonData:
    """A button to include in the response."""

    type: str  # "link" or "action"
    label: str
    url: str | None = None  # For link buttons
    action: str | None = None  # For action buttons: "resolved", "ticket"


@dataclass
class AgentStep:
    """A single step in the agent's execution."""

    type: str  # "tool_call", "tool_result", "response", "irrelevant"
    tool_name: str | None = None
    tool_args: dict | None = None
    tool_result: dict | None = None
    response: str | None = None
    emoji: str = ""
    description: str = ""
    buttons: list[ButtonData] = field(default_factory=list)


def build_system_prompt() -> str:
    """Build the system prompt with available doc titles."""
    doc_titles = doc_store.get_doc_titles_for_prompt()

    return f"""You are a helpful support assistant for Xenon, a Discord bot for server backups and templates.

Your job is to answer questions about Xenon based on the official documentation.

## Rules:
1. ALWAYS call check_relevance FIRST to determine if the question is about Xenon
2. If the question is NOT about Xenon, respond with exactly: "IRRELEVANT"
3. If the question IS about Xenon, use the tools to find the answer
4. **NEVER MAKE THINGS UP.** Only answer based on information you found in the documentation.
5. **NEVER invent email addresses, URLs, contact information, commands, or any other specifics.** If you didn't read it in the docs, don't say it.
6. Be concise but helpful
7. If you can't find the answer in the docs, say "I couldn't find this in the documentation." The user can click "Community Support" to ask the community for help. Do NOT invent support emails or other contact methods.

## Available Documentation:
{doc_titles}

To read a document's content, use the get_doc tool with the slug.
To search across all docs, use search_docs.

## Response Format:
When you have found the answer, respond with a JSON object in this exact format:
```json
{{
  "response": "Your helpful answer here...",
  "buttons": [
    {{"type": "link", "label": "📚 Docs Page", "url": "https://wiki.xenon.bot/page-slug"}}
  ]
}}
```

Button guidelines:
- Only include buttons if they provide genuinely useful links
- Use the "link" type with relevant documentation URLs
- Label should be short and descriptive with an emoji
- Maximum 3 link buttons per response
- If no buttons are helpful, use an empty array: "buttons": []

The documentation base URL is: https://wiki.xenon.bot/

Always cite which documentation page your answer comes from in the response text."""


def parse_response_with_buttons(content: str) -> tuple[str, list[ButtonData]]:
    """Parse agent response, extracting buttons if present."""
    buttons: list[ButtonData] = []

    # Try to find JSON in the response
    # Look for ```json ... ``` blocks first
    json_match = re.search(r"```json\s*(\{.*?\})\s*```", content, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(1))
            if isinstance(data, dict) and "response" in data:
                response_text = data.get("response", "")
                raw_buttons = data.get("buttons", [])
                for btn in raw_buttons:
                    if isinstance(btn, dict) and "type" in btn and "label" in btn:
                        buttons.append(
                            ButtonData(
                                type=btn.get("type", "link"),
                                label=btn.get("label", ""),
                                url=btn.get("url"),
                                action=btn.get("action"),
                            )
                        )
                return response_text, buttons
        except json.JSONDecodeError:
            pass

    # Try parsing the whole content as JSON
    try:
        data = json.loads(content)
        if isinstance(data, dict) and "response" in data:
            response_text = data.get("response", "")
            raw_buttons = data.get("buttons", [])
            for btn in raw_buttons:
                if isinstance(btn, dict) and "type" in btn and "label" in btn:
                    buttons.append(
                        ButtonData(
                            type=btn.get("type", "link"),
                            label=btn.get("label", ""),
                            url=btn.get("url"),
                            action=btn.get("action"),
                        )
                    )
            return response_text, buttons
    except json.JSONDecodeError:
        pass

    # No valid JSON found, return content as-is
    return content, buttons


class AgentRunner:
    """Runs the agentic loop, yielding steps for live updates."""

    def __init__(self, client: OpenRouterClient | None = None):
        self.client = client or OpenRouterClient()

    async def run(
        self,
        user_message: str,
        history: list[dict] | None = None,
        images: list[str] | None = None,
        channel_context: list[dict] | None = None,
        on_tool_call: Callable[[str, dict, dict], Awaitable[None]] | None = None,
    ) -> AsyncIterator[AgentStep]:
        """
        Run the agent on a user message.

        Yields AgentStep objects for each step (tool calls, results, final response).

        Args:
            user_message: The user's question
            history: Previous messages [{"role": "user"|"assistant", "content": "..."}]
            images: List of base64-encoded images
            channel_context: Recent channel messages for context [{"author": "...", "content": "..."}]
        """
        # Build messages
        messages: list[Message] = [Message(role="system", content=build_system_prompt())]

        # Add history (last 5 messages)
        if history:
            for msg in history[-5:]:
                messages.append(Message(role=msg["role"], content=msg["content"]))

        # Build user message with channel context
        user_content = user_message
        if channel_context:
            context_text = "\n".join(
                f"[{msg['author']}]: {msg['content']}" for msg in channel_context[-10:]
            )
            user_content = f"""## Recent channel messages for context:
{context_text}

## User's question:
{user_message}"""

        # Add current user message
        messages.append(
            Message(
                role="user",
                content=user_content,
                images=images or [],
            )
        )

        tool_call_count = 0

        while tool_call_count < MAX_TOOL_CALLS:
            # Call LLM
            response = await self.client.chat(messages, tools=TOOLS)

            # Check for tool calls
            if response.tool_calls:
                tool_call = response.tool_calls[0]  # Process one at a time
                tool_call_count += 1

                # Yield the tool call step
                yield AgentStep(
                    type="tool_call",
                    tool_name=tool_call.name,
                    tool_args=tool_call.arguments,
                    emoji=get_tool_emoji(tool_call.name),
                    description=get_tool_description(tool_call.name, tool_call.arguments),
                )

                # Execute the tool
                result = execute_tool(tool_call.name, tool_call.arguments)

                # Call the callback if provided
                if on_tool_call:
                    await on_tool_call(tool_call.name, tool_call.arguments, result)

                # Check for relevance gate
                if tool_call.name == "check_relevance":
                    if not result.get("is_relevant", False):
                        yield AgentStep(
                            type="irrelevant",
                            response="IRRELEVANT",
                            description="Question is not about Xenon",
                        )
                        return

                # Yield the tool result step
                yield AgentStep(
                    type="tool_result",
                    tool_name=tool_call.name,
                    tool_result=result,
                )

                # Add tool call and result to messages
                messages.append(
                    Message(
                        role="assistant",
                        tool_calls=[tool_call],
                    )
                )
                messages.append(
                    Message(
                        role="tool",
                        content=json.dumps(result),
                        tool_call_id=tool_call.id,
                        name=tool_call.name,
                    )
                )

            else:
                # No tool calls - we have a final response
                final_content = response.content or ""

                # Check if model decided it's irrelevant
                if "IRRELEVANT" in final_content.upper():
                    yield AgentStep(
                        type="irrelevant",
                        response=final_content,
                    )
                else:
                    # Parse response for buttons
                    response_text, buttons = parse_response_with_buttons(final_content)
                    yield AgentStep(
                        type="response",
                        response=response_text,
                        buttons=buttons,
                    )
                return

        # Hit max tool calls
        yield AgentStep(
            type="response",
            response="I apologize, but I couldn't find a complete answer. Please try rephrasing your question or visit the Xenon support server for help.",
        )
