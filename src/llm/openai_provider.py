"""OpenAI-compatible provider (DeepSeek, etc.) with streaming support."""

from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from src.llm.provider import LLMProvider, ToolCall, LLMResponse


class OpenAIProvider(LLMProvider):
    """LLM backend backed by any OpenAI-compatible API."""

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.model = model
        self._client = OpenAI(
            api_key=api_key or "sk-placeholder",
            base_url=base_url,
        )

    @property
    def provider_name(self) -> str:
        return "openai"

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
        # Validate: strip orphaned tool messages
        clean = _validate_messages(messages)

        full_messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            *clean,
        ]

        response = self._client.chat.completions.create(
            model=self.model,
            messages=full_messages,
            tools=tools,
            max_tokens=max_tokens,
            stream=True,
        )

        text_parts: list[str] = []
        tool_calls_acc: dict[int, dict[str, Any]] = {}

        for chunk in response:
            delta = chunk.choices[0].delta
            if delta.content:
                text_parts.append(delta.content)
                print(delta.content, end="", flush=True)
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {
                            "id": tc_delta.id or "",
                            "name": tc_delta.function.name if tc_delta.function else "",
                            "arguments": "",
                        }
                    entry = tool_calls_acc[idx]
                    if tc_delta.id:
                        entry["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            entry["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            entry["arguments"] += tc_delta.function.arguments

        text = "".join(text_parts)

        tool_calls: list[ToolCall] = []
        for tc in sorted(tool_calls_acc.values(), key=lambda x: list(tool_calls_acc.keys())[list(tool_calls_acc.values()).index(x)]):
            try:
                args = json.loads(tc["arguments"])
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(ToolCall(id=tc["id"], name=tc["name"], arguments=args))

        # Build assistant_message for history
        assistant_msg: dict[str, Any] = {"role": "assistant"}
        if tool_calls:
            assistant_msg["content"] = None
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)},
                }
                for tc in tool_calls
            ]
        else:
            assistant_msg["content"] = text or None

        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            assistant_message=assistant_msg,
        )

    # ------------------------------------------------------------------
    # Message builders
    # ------------------------------------------------------------------

    def make_user_message(self, content: str) -> dict[str, Any]:
        return {"role": "user", "content": content}

    def make_tool_result_messages(
        self,
        items: list[tuple[str, str, str]],
    ) -> list[dict[str, Any]]:
        return [
            {"role": "tool", "tool_call_id": tid, "content": content}
            for tid, _name, content in items
        ]

    def make_compaction_summary_message(self, summary: str) -> dict[str, Any]:
        return self.make_user_message(
            f"<conversation_summary>\n{summary}\n</conversation_summary>"
        )

    # ------------------------------------------------------------------
    # Compaction (non-streaming)
    # ------------------------------------------------------------------

    def compact(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
    ) -> str:
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                *messages,
            ],
            max_tokens=2048,
        )
        return response.choices[0].message.content or ""

    # ------------------------------------------------------------------
    # Tool schema
    # ------------------------------------------------------------------

    def tools_for_provider(self, registry: Any) -> list[dict[str, Any]]:
        return registry.to_openai()


# ------------------------------------------------------------------
# Message validation (shared)
# ------------------------------------------------------------------

def _validate_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip orphaned tool messages that lack a preceding assistant with tool_calls."""
    last_tool_call_ids: set[str] = set()
    clean: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role", "")
        if role == "assistant" and m.get("tool_calls"):
            ids = {tc["id"] for tc in m["tool_calls"]}
            last_tool_call_ids = ids
            clean.append(m)
        elif role == "tool":
            if m.get("tool_call_id") in last_tool_call_ids:
                clean.append(m)
        else:
            last_tool_call_ids = set()
            clean.append(m)
    return clean
