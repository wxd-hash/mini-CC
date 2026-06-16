"""Abstract LLM provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


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
        ...

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
