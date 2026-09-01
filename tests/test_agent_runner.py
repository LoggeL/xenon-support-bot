import json
from collections import deque
from typing import Any

import pytest

from src.agent.client import InputItem, JsonSchema, ModelResponse, Tool, ToolCall
from src.agent.runner import AgentRunner


class FakeModelClient:
    def __init__(self, responses: list[ModelResponse]) -> None:
        self.responses = deque(responses)
        self.requests: list[dict[str, Any]] = []

    async def create_response(
        self,
        *,
        instructions: str,
        input_items: list[InputItem],
        tools: list[Tool],
        output_schema: JsonSchema,
    ) -> ModelResponse:
        self.requests.append(
            {
                "instructions": instructions,
                "input_items": input_items,
                "tools": tools,
                "output_schema": output_schema,
            }
        )
        return self.responses.popleft()

    async def generate_text(self, *, instructions: str, prompt: str) -> str:
        return prompt

    async def close(self) -> None:
        return None


@pytest.fixture(autouse=True)
def static_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    async def prompt() -> str:
        return "Use only test documentation."

    monkeypatch.setattr("src.agent.runner.build_system_prompt", prompt)


async def test_runs_parallel_tools_and_only_links_read_sources() -> None:
    first = ModelResponse(
        output_text="",
        tool_calls=(
            ToolCall(call_id="search-1", name="search_docs", arguments={"query": "backup"}),
            ToolCall(call_id="doc-1", name="get_doc", arguments={"slug": "backups"}),
        ),
        output_items=(
            {"type": "function_call", "call_id": "search-1"},
            {"type": "function_call", "call_id": "doc-1"},
        ),
    )
    second = ModelResponse(
        output_text=json.dumps(
            {
                "status": "answered",
                "answer": "Use the backup command described on the Backups page.",
                "sources": ["backups", "hallucinated"],
            }
        ),
        tool_calls=(),
        output_items=(),
    )
    client = FakeModelClient([first, second])

    async def execute(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "get_doc":
            return {
                "slug": "backups",
                "title": "Backups",
                "url": "https://wiki.xenon.bot/en/backups",
                "content": "Backup docs",
            }
        return {"results": [{"slug": "backups"}]}

    steps = [step async for step in AgentRunner(client, tool_executor=execute).run("Help")]

    assert [step.type for step in steps] == [
        "tool_call",
        "tool_call",
        "tool_result",
        "tool_result",
        "response",
    ]
    assert [(button.label, button.url) for button in steps[-1].buttons] == [
        ("📚 Backups", "https://wiki.xenon.bot/en/backups")
    ]
    continuation = client.requests[1]["input_items"]
    assert any(item.get("type") == "function_call_output" for item in continuation)


async def test_irrelevant_answer_stops_without_tools() -> None:
    client = FakeModelClient(
        [
            ModelResponse(
                output_text=json.dumps(
                    {"status": "irrelevant", "answer": "Unrelated to Xenon.", "sources": []}
                ),
                tool_calls=(),
                output_items=(),
            )
        ]
    )

    steps = [step async for step in AgentRunner(client).run("How is the weather?")]

    assert len(steps) == 1
    assert steps[0].type == "irrelevant"


async def test_invalid_structured_output_fails_closed() -> None:
    client = FakeModelClient(
        [ModelResponse(output_text="not-json", tool_calls=(), output_items=())]
    )

    steps = [step async for step in AgentRunner(client).run("Question")]

    assert steps[-1].type == "response"
    assert "grounded answer" in (steps[-1].response or "")
    assert steps[-1].buttons == []
