"""Abstract LLM provider interface and stream event types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterator


# ---------------------------------------------------------------------------
# Stream event types (defined here to avoid circular imports with src.agent)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TextDelta:
    """Incremental text chunk from the model stream."""
    text: str


@dataclass(frozen=True)
class ToolUseBlock:
    """A completed tool-use block — ready to execute."""
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class StreamEnd:
    """Final event from a streaming API call."""
    assistant_message: dict[str, Any]
    text: str = ""  # accumulated full text


# ---------------------------------------------------------------------------
# Legacy types
# ---------------------------------------------------------------------------


@dataclass
class ToolCall:
    """Normalized tool-call from any provider."""
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    """Normalized LLM response — provider-agnostic."""
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    assistant_message: dict[str, Any] | None = None


class LLMProvider(ABC):
    """Abstract backend for an LLM (Anthropic, OpenAI, DeepSeek, ...)."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        ...

    @abstractmethod
    def send_message(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Send a message and return the complete response (legacy API)."""

    @abstractmethod
    def send_message_stream(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int = 4096,
    ) -> Iterator[TextDelta | ToolUseBlock | StreamEnd]:
        """Send a message and yield events as they arrive.

        Yields TextDelta for incremental text, ToolUseBlock when a tool-call
        block completes (ready to execute), and a final StreamEnd carrying
        the assistant_message for history.
        """

    @abstractmethod
    def make_user_message(self, content: str) -> dict[str, Any]:
        """Create a provider-native user message for the history."""

    @abstractmethod
    def make_tool_result_messages(
        self,
        items: list[tuple[str, str, str]],
    ) -> list[dict[str, Any]]:
        """Create provider-native tool-result message(s).

        Each item is ``(tool_call_id, tool_name, result_content)``.
        """

    @abstractmethod
    def make_compaction_summary_message(self, summary: str) -> dict[str, Any]:
        """Create a provider-native message carrying a compaction summary."""

    @abstractmethod
    def compact(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
    ) -> str:
        """Ask the model to summarise old messages. Returns the summary text."""

    @abstractmethod
    def tools_for_provider(self, registry: Any) -> list[dict[str, Any]]:
        """Convert a ToolRegistry's tools to provider-native schema."""
