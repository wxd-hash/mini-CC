"""Context compaction service — matches cc-mini's CompactService."""

from __future__ import annotations

from typing import Any

# Rough heuristic: ~4 chars per token for English/Code
_CHARS_PER_TOKEN = 4


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """Estimate token count from message content length."""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    text = block.get("text", "") or str(block.get("content", ""))
                    total += len(text)
    return total // _CHARS_PER_TOKEN


def should_compact(
    messages: list[dict[str, Any]],
    model: str = "",
    last_input_tokens: int = 0,
    threshold: int = 30,
) -> bool:
    """Return True if the conversation should be compacted.

    Uses message count threshold (matching original project behavior).
    """
    return len(messages) > threshold


class CompactService:
    """LLM-driven conversation compaction — matches cc-mini's CompactService.

    Summarizes old messages and replaces them with a compact summary block.
    """

    def __init__(self, client: Any, model: str = "", effort: str | None = None) -> None:
        self._client = client
        self._model = model

    def compact(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str,
        keep_recent: int = 10,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Summarize old messages via LLM.

        Returns (new_messages, summary_text).
        On failure, returns original messages with old content truncated.
        """
        from src.context import micro_compact

        if len(messages) <= keep_recent:
            return messages, None

        # Step 1: In-memory truncation of old tool results
        truncated = micro_compact(messages, keep_recent=keep_recent)

        # Step 2: Try LLM summarization
        try:
            from src.context import compact_messages
            from src.llm.provider import LLMProvider

            if hasattr(self._client, '_provider_ref'):
                provider = self._client._provider_ref
                compacted = compact_messages(
                    provider=provider,
                    system_prompt=system_prompt,
                    messages=truncated,
                    keep_recent=keep_recent,
                )
                if compacted:
                    return compacted, "Compacted via LLM summary"
            return truncated, None
        except Exception:
            return truncated, None
