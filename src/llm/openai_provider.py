"""OpenAI-compatible provider (DeepSeek, OpenAI, local models, ...)."""

from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from src.llm.provider import LLMProvider, ToolCall, LLMResponse


class OpenAIProvider(LLMProvider):
    """LLM backend backed by any OpenAI-compatible API (DeepSeek, etc.)."""

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
    # Send
    # ------------------------------------------------------------------

    def send_message(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int = 4096,
    ) -> LLMResponse:
        # Validate: every 'tool' message must follow an assistant with tool_calls.
        # Strip orphaned tool messages (can happen after compaction).
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
                # else: orphaned — skip it
            else:
                last_tool_call_ids = set()
                clean.append(m)

        full_messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            *clean,
        ]

        response = self._client.chat.completions.create(
            model=self.model,
            messages=full_messages,
            tools=tools,
            max_tokens=max_tokens,
        )
        choice = response.choices[0]
        msg = choice.message

        text = msg.content or ""

        tool_calls: list[ToolCall] = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=args,
                ))

        # Build assistant_message for history.  When tool_calls are present
        # some providers (DeepSeek) require content to be null, not a string.
        assistant_msg: dict[str, Any] = {"role": "assistant"}
        if msg.tool_calls:
            assistant_msg["content"] = None
        else:
            assistant_msg["content"] = text or None

        if msg.tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ]

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
        """OpenAI: each tool result is its own ``role: tool`` message."""
        return [
            {"role": "tool", "tool_call_id": tid, "content": content}
            for tid, _name, content in items
        ]

    def make_compaction_summary_message(self, summary: str) -> dict[str, Any]:
        return self.make_user_message(
            f"<conversation_summary>\n{summary}\n</conversation_summary>"
        )

    # ------------------------------------------------------------------
    # Compaction
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
