"""Anthropic API provider with streaming support."""

from __future__ import annotations

from typing import Any

import anthropic

from src.llm.provider import LLMProvider, ToolCall, LLMResponse


class AnthropicProvider(LLMProvider):
    """LLM backend backed by the Anthropic Messages API."""

    def __init__(self, model: str) -> None:
        self.model = model
        self._client = anthropic.Anthropic()

    @property
    def provider_name(self) -> str:
        return "anthropic"

    # ------------------------------------------------------------------
    # Send (with streaming)
    # ------------------------------------------------------------------

    def send_message(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int = 4096,
    ) -> LLMResponse:
        with self._client.messages.stream(
            model=self.model,
            system=system_prompt,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
        ) as stream:
            text_parts: list[str] = []
            for delta in stream.text_stream:
                text_parts.append(delta)
                print(delta, end="", flush=True)

            final = stream.get_final_message()

        tool_calls: list[ToolCall] = []
        for b in final.content:
            if b.type == "tool_use":
                tool_calls.append(ToolCall(
                    id=b.id,
                    name=b.name,
                    arguments=dict(b.input),
                ))

        return LLMResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            assistant_message={"role": "assistant", "content": final.content},
        )

    # ------------------------------------------------------------------
    # Message builders
    # ------------------------------------------------------------------

    def make_user_message(self, content: str) -> dict[str, Any]:
        return {
            "role": "user",
            "content": [{"type": "text", "text": content}],
        }

    def make_tool_result_messages(
        self,
        items: list[tuple[str, str, str]],
    ) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        for tool_id, _name, content in items:
            blocks.append({
                "type": "tool_result",
                "tool_use_id": tool_id,
                "content": content,
            })
        return [{"role": "user", "content": blocks}]

    def make_compaction_summary_message(self, summary: str) -> dict[str, Any]:
        return self.make_user_message(
            f"<conversation_summary>\n{summary}\n</conversation_summary>"
        )

    # ------------------------------------------------------------------
    # Compaction (non-streaming — no terminal output needed)
    # ------------------------------------------------------------------

    def compact(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
    ) -> str:
        response = self._client.messages.create(
            model=self.model,
            system=system_prompt,
            messages=messages,
            max_tokens=2048,
        )
        return "".join(b.text for b in response.content if b.type == "text")

    # ------------------------------------------------------------------
    # Tool schema
    # ------------------------------------------------------------------

    def tools_for_provider(self, registry: Any) -> list[dict[str, Any]]:
        return registry.to_anthropic()
