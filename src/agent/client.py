"""OpenRouter API client with function calling support."""

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

from src.config import settings


OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"


@dataclass
class ToolCall:
    """Represents a tool call from the LLM."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Message:
    """Chat message."""

    role: str  # "system", "user", "assistant", "tool"
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None  # For tool responses
    name: str | None = None  # Tool name for tool responses
    images: list[str] = field(default_factory=list)  # Base64 images

    def to_api_format(self) -> dict:
        """Convert to OpenRouter API format."""
        msg: dict[str, Any] = {"role": self.role}

        if self.role == "user" and self.images:
            # Multi-modal message with images
            content_parts: list[dict] = []
            if self.content:
                content_parts.append({"type": "text", "text": self.content})
            for img_b64 in self.images:
                content_parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{img_b64}"},
                    }
                )
            msg["content"] = content_parts
        elif self.content is not None:
            msg["content"] = self.content

        if self.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                }
                for tc in self.tool_calls
            ]

        if self.tool_call_id:
            msg["role"] = "tool"
            msg["tool_call_id"] = self.tool_call_id
            if self.name:
                msg["name"] = self.name

        return msg


@dataclass
class Tool:
    """Tool definition for function calling."""

    name: str
    description: str
    parameters: dict[str, Any]

    def to_api_format(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class CompletionResponse:
    """Response from the LLM."""

    content: str | None
    tool_calls: list[ToolCall]
    finish_reason: str


class OpenRouterClient:
    """Client for OpenRouter API with function calling."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
    ):
        self.api_key = api_key or settings.openrouter_api_key
        self.model = model or settings.openrouter_model
        self._client = httpx.AsyncClient(timeout=120.0)

    async def chat(
        self,
        messages: list[Message],
        tools: list[Tool] | None = None,
        temperature: float = 0.3,
    ) -> CompletionResponse:
        """Send chat completion request."""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [m.to_api_format() for m in messages],
            "temperature": temperature,
        }

        if tools:
            payload["tools"] = [t.to_api_format() for t in tools]
            payload["tool_choice"] = "auto"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/xenon-support-bot",
            "X-Title": "Xenon Support Bot",
        }

        resp = await self._client.post(
            OPENROUTER_API_URL,
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()

        choice = data["choices"][0]
        message = choice["message"]

        # Parse tool calls if present
        tool_calls: list[ToolCall] = []
        if "tool_calls" in message and message["tool_calls"]:
            for tc in message["tool_calls"]:
                func = tc["function"]
                try:
                    args = json.loads(func["arguments"])
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append(
                    ToolCall(
                        id=tc["id"],
                        name=func["name"],
                        arguments=args,
                    )
                )

        return CompletionResponse(
            content=message.get("content"),
            tool_calls=tool_calls,
            finish_reason=choice.get("finish_reason", "stop"),
        )

    async def close(self):
        await self._client.aclose()
