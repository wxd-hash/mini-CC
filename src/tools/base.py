"""Tool base protocol — matches cc-mini's Tool + ToolResult pattern."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class ToolResult:
    """Result from a tool execution.

    Matches cc-mini's ToolResult: content is a string sent back to the LLM;
    is_error flags failures (shown to user but not treated as a fatal stop).
    """

    content: str
    is_error: bool = False


class Tool(ABC):
    """Abstract tool that the agent can invoke.

    Subclasses provide: name, description, input_schema, execute(),
    is_read_only(), get_activity_description(), and to_api_schema().
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique tool name, e.g. 'read_file'."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description of what the tool does."""
        ...

    @property
    @abstractmethod
    def input_schema(self) -> dict[str, Any]:
        """JSON Schema for the tool's arguments.

        Must include 'type': 'object', 'properties', and 'required' keys.
        """
        ...

    # -- new methods matching cc-mini Tool protocol ---------------------------

    @property
    def maxResultSizeChars(self) -> int | None:
        """Maximum characters for this tool's result in the message history.

        None = no limit (result always stays inline). Results exceeding this
        are moved to a temp file and replaced with a preview + path reference.
        Default: None (no limit). Matches claude-code's maxResultSizeChars.
        """
        return None

    def is_read_only(self) -> bool:
        """Return True if this tool never mutates state.

        Read-only tools can be executed in parallel batches.
        Default: False (conservative).
        """
        return False

    def get_activity_description(self, **kwargs: Any) -> str | None:
        """Return a human-readable one-liner describing what's about to happen.

        Shown in the terminal as a spinner label while the tool runs.
        Default: name + first meaningful value from kwargs.
        """
        parts = [self.name]
        for key, val in kwargs.items():
            if isinstance(val, str) and len(val) < 80:
                parts.append(val)
                break
        return " ".join(parts)

    def to_api_schema(self) -> dict[str, Any]:
        """Return a provider-neutral tool declaration dict.

        Has keys: name, description, input_schema.
        Providers convert this to their native format.
        """
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    @abstractmethod
    def execute(self, **kwargs: Any) -> ToolResult:
        """Execute the tool with keyword arguments, return a ToolResult."""
        ...

    # -- legacy bridge: run() delegates to execute() --------------------------

    def run(self, args: dict[str, Any]) -> str:
        """Legacy compatibility — calls execute(**args) and returns content."""
        result = self.execute(**args)
        return result.content
