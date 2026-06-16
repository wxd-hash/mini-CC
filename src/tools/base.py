"""Abstract base class for all tools."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Tool(ABC):
    """Abstract tool that the agent can invoke.

    Subclasses must provide name, description, input_schema properties
    and implement run(args).
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

    @abstractmethod
    def run(self, args: dict[str, Any]) -> str:
        """Execute the tool with the given arguments, return a string result."""
        ...
