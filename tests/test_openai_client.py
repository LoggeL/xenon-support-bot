from typing import Any, cast

from pydantic import SecretStr

from src.agent.client import OpenAIResponsesClient, Tool
from src.config import Settings


class FakeFunctionCall:
    type = "function_call"
    call_id = "call-1"
    name = "search_docs"
    arguments = '{"query":"backup"}'

    def model_dump(self, **_: Any) -> dict[str, str]:
        return {
            "type": self.type,
            "call_id": self.call_id,
            "name": self.name,
            "arguments": self.arguments,
        }


class FakeResponses:
    def __init__(self) -> None:
        self.request: dict[str, Any] = {}

    async def create(self, **request: Any) -> Any:
        self.request = request
        return type(
            "Response",
            (),
            {"output": [FakeFunctionCall()], "output_text": ""},
        )()


class FakeSDK:
    def __init__(self) -> None:
        self.responses = FakeResponses()
        self.closed = False

    async def close(self) -> None:
        self.closed = True


async def test_uses_luna_responses_api_with_strict_tools() -> None:
    config = Settings(
        discord_token=SecretStr("discord-secret"),
        openai_api_key=SecretStr("openai-secret"),
        database_url="postgresql://user:pass@localhost/xenon",
    )
    sdk = FakeSDK()
    client = OpenAIResponsesClient(config, client=cast(Any, sdk))
    tool = Tool(
        name="search_docs",
        description="Search",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    )

    response = await client.create_response(
        instructions="Use docs.",
        input_items=[{"role": "user", "content": "Help"}],
        tools=[tool],
        output_schema={"type": "object"},
    )

    request = sdk.responses.request
    assert request["model"] == "gpt-5.6-luna"
    assert request["store"] is False
    assert request["reasoning"] == {"effort": "medium"}
    assert request["tools"][0]["strict"] is True
    assert request["text"]["format"]["type"] == "json_schema"
    assert response.tool_calls[0].arguments == {"query": "backup"}

    await client.close()
    assert sdk.closed
