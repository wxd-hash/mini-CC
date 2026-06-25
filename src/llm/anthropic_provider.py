"""Anthropic API provider with streaming support."""

from __future__ import annotations

import json
from typing import Any, Iterator

import anthropic

from src.llm.provider import LLMProvider, ToolCall, LLMResponse, TextDelta, ToolUseBlock, StreamEnd


class AnthropicProvider(LLMProvider):
    """LLM backend backed by the Anthropic Messages API."""

    def __init__(self, model: str) -> None:
        self.model = model
        self._client = anthropic.Anthropic()

    @property
    def provider_name(self) -> str:
        return "anthropic"

    # ------------------------------------------------------------------
    # Send (legacy — returns complete response)
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
    # Send (streaming — yields events as they arrive)
    # ------------------------------------------------------------------

    def send_message_stream(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int = 4096,
    ) -> Iterator[TextDelta | ToolUseBlock | StreamEnd]:
        """Stream model response. Yields TextDelta for incremental text,
        ToolUseBlock when each tool-call block completes, and StreamEnd
        when the full message is ready.
        """
        text_parts: list[str] = []
        tool_blocks: dict[int, dict[str, Any]] = {}  # index → {id, name, json_fragments}

        with self._client.messages.stream(
            model=self.model,
            system=system_prompt,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
        ) as stream:
            for event in stream:
                ev_type = getattr(event, "type", "")

                if ev_type == "content_block_start":
                    block = getattr(event, "content_block", None)
                    if block is not None and getattr(block, "type", "") == "tool_use":
                        idx = getattr(event, "index", -1)
                        tool_blocks[idx] = {
                            "id": getattr(block, "id", ""),
                            "name": getattr(block, "name", ""),
                            "json_fragments": [],
                        }

                elif ev_type == "content_block_delta":
                    delta = getattr(event, "delta", None)
                    if delta is None:
                        continue
                    delta_type = getattr(delta, "type", "")
                    if delta_type == "text_delta":
                        chunk = getattr(delta, "text", "")
                        text_parts.append(chunk)
                        yield TextDelta(text=chunk)
                    elif delta_type == "input_json_delta":
                        idx = getattr(event, "index", -1)
                        partial = getattr(delta, "partial_json", "")
                        if idx in tool_blocks:
                            tool_blocks[idx]["json_fragments"].append(partial)

                elif ev_type == "content_block_stop":
                    idx = getattr(event, "index", -1)
                    if idx in tool_blocks:
                        tb = tool_blocks[idx]
                        arg_str = "".join(tb["json_fragments"])
                        try:
                            args = json.loads(arg_str) if arg_str else {}
                        except json.JSONDecodeError:
                            args = {}
                        yield ToolUseBlock(id=tb["id"], name=tb["name"], arguments=args)

            final = stream.get_final_message()

        assistant_message: dict[str, Any] = {"role": "assistant", "content": final.content}
        yield StreamEnd(assistant_message=assistant_message, text="".join(text_parts))

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
