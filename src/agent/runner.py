"""Agentic loop runner - executes tools one at a time with live updates."""

import json
from dataclasses import dataclass
from typing import AsyncIterator, Callable, Awaitable

from src.agent.client import OpenRouterClient, Message, ToolCall
from src.agent.tools import TOOLS, execute_tool, get_tool_emoji, get_tool_description
from src.docs.store import doc_store


MAX_TOOL_CALLS = 10  # Prevent infinite loops


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


def build_system_prompt() -> str:
    """Build the system prompt with available doc titles."""
    doc_titles = doc_store.get_doc_titles_for_prompt()

    return f"""You are a helpful support assistant for Xenon, a Discord bot for server backups and templates.

Your job is to answer questions about Xenon based on the official documentation.

## Rules:
1. ALWAYS call check_relevance FIRST to determine if the question is about Xenon
2. If the question is NOT about Xenon, respond with exactly: "IRRELEVANT"
3. If the question IS about Xenon, use the tools to find the answer
4. Only answer based on the documentation - don't make things up
5. Be concise but helpful
6. If you can't find the answer in the docs, say so and suggest joining the support server

## Available Documentation:
{doc_titles}

To read a document's content, use the get_doc tool with the slug.
To search across all docs, use search_docs.

Always cite which documentation page your answer comes from."""


class AgentRunner:
    """Runs the agentic loop, yielding steps for live updates."""

    def __init__(self, client: OpenRouterClient | None = None):
        self.client = client or OpenRouterClient()

    async def run(
        self,
        user_message: str,
        history: list[dict] | None = None,
        images: list[str] | None = None,
    ) -> AsyncIterator[AgentStep]:
        """
        Run the agent on a user message.

        Yields AgentStep objects for each step (tool calls, results, final response).

        Args:
            user_message: The user's question
            history: Previous messages [{"role": "user"|"assistant", "content": "..."}]
            images: List of base64-encoded images
        """
        # Build messages
        messages: list[Message] = [Message(role="system", content=build_system_prompt())]

        # Add history (last 5 messages)
        if history:
            for msg in history[-5:]:
                messages.append(Message(role=msg["role"], content=msg["content"]))

        # Add current user message
        messages.append(
            Message(
                role="user",
                content=user_message,
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
                final_response = response.content or ""

                # Check if model decided it's irrelevant
                if "IRRELEVANT" in final_response.upper():
                    yield AgentStep(
                        type="irrelevant",
                        response=final_response,
                    )
                else:
                    yield AgentStep(
                        type="response",
                        response=final_response,
                    )
                return

        # Hit max tool calls
        yield AgentStep(
            type="response",
            response="I apologize, but I couldn't find a complete answer. Please try rephrasing your question or visit the Xenon support server for help.",
        )
