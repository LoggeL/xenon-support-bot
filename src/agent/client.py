"""Small seam around the OpenAI Responses API."""

import json
from dataclasses import dataclass
from typing import Any, Protocol, cast

from openai import AsyncOpenAI
from openai.types.shared_params import Reasoning

from src.config import Settings, get_settings

InputItem = dict[str, Any]
JsonSchema = dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A validated function request emitted by the model."""

    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Tool:
    """A strict function tool exposed to the model."""

    name: str
    description: str
    parameters: JsonSchema

    def to_api_format(self) -> dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "strict": True,
        }


@dataclass(frozen=True, slots=True)
class ModelResponse:
    """Provider-neutral response consumed by the agent loop."""

    output_text: str
    tool_calls: tuple[ToolCall, ...]
    output_items: tuple[InputItem, ...]


class ModelClient(Protocol):
    """Interface required by the support agent."""

    async def create_response(
        self,
        *,
        instructions: str,
        input_items: list[InputItem],
        tools: list[Tool],
        output_schema: JsonSchema,
    ) -> ModelResponse: ...

    async def generate_text(self, *, instructions: str, prompt: str) -> str: ...

    async def close(self) -> None: ...


class OpenAIResponsesClient:
    """GPT-5.6 Luna adapter using the native Responses API."""

    def __init__(
        self,
        config: Settings | None = None,
        *,
        client: AsyncOpenAI | None = None,
    ) -> None:
        config = config or get_settings()
        self.model = config.openai_model
        self.reasoning_effort = config.openai_reasoning_effort
        self.max_output_tokens = config.openai_max_output_tokens
        self._client = client or AsyncOpenAI(
            api_key=config.openai_api_key.get_secret_value(),
            timeout=config.openai_timeout_seconds,
            max_retries=2,
        )

    async def create_response(
        self,
        *,
        instructions: str,
        input_items: list[InputItem],
        tools: list[Tool],
        output_schema: JsonSchema,
    ) -> ModelResponse:
        """Generate one agent turn and normalize provider output."""
        reasoning: Reasoning = {"effort": cast(Any, self.reasoning_effort)}
        response = await self._client.responses.create(
            model=self.model,
            instructions=instructions,
            input=input_items,  # pyright: ignore[reportArgumentType]
            tools=[tool.to_api_format() for tool in tools],  # pyright: ignore[reportArgumentType]
            tool_choice="auto",
            parallel_tool_calls=True,
            reasoning=reasoning,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "support_answer",
                    "strict": True,
                    "schema": output_schema,
                },
                "verbosity": "low",
            },
            max_output_tokens=self.max_output_tokens,
            store=False,
            include=["reasoning.encrypted_content"],
        )

        output_items: list[InputItem] = []
        tool_calls: list[ToolCall] = []

        for item in response.output:
            output_items.append(item.model_dump(mode="json", exclude_none=True))
            if item.type != "function_call":
                continue

            try:
                arguments = json.loads(item.arguments)
            except (json.JSONDecodeError, TypeError):
                arguments = {}

            tool_calls.append(
                ToolCall(
                    call_id=item.call_id,
                    name=item.name,
                    arguments=arguments if isinstance(arguments, dict) else {},
                )
            )

        return ModelResponse(
            output_text=response.output_text,
            tool_calls=tuple(tool_calls),
            output_items=tuple(output_items),
        )

    async def generate_text(self, *, instructions: str, prompt: str) -> str:
        """Run a small text-only transformation without exposing agent tools."""
        response = await self._client.responses.create(
            model=self.model,
            instructions=instructions,
            input=prompt,
            reasoning={"effort": "low"},
            text={"verbosity": "low"},
            max_output_tokens=256,
            store=False,
        )
        return response.output_text.strip()

    async def close(self) -> None:
        await self._client.close()
