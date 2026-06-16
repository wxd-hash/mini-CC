"""Tool registry — stores tools and exports them for LLM tool-calling."""

from __future__ import annotations

from typing import Any

from src.tools.base import Tool


class ToolRegistry:
    """Holds registered tools and provides lookup / Anthropic schema export."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    # ------------------------------------------------------------------
    # Registration & lookup
    # ------------------------------------------------------------------

    def register(self, tool: Tool) -> None:
        """Register a tool instance.  Replaces any existing tool with the same name."""
        self._tools[tool.name] = tool

    def get_tool(self, name: str) -> Tool:
        """Look up a tool by name.  Raises KeyError if not found."""
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name}")
        return self._tools[name]

    def list_tools(self) -> list[tuple[str, str]]:
        """Return (name, description) for every registered tool."""
        return [(t.name, t.description) for t in self._tools.values()]

    # ------------------------------------------------------------------
    # Provider-native schemas
    # ------------------------------------------------------------------

    def to_anthropic(self) -> list[dict[str, Any]]:
        """Export tool definitions for the Anthropic Messages API."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in self._tools.values()
        ]

    def to_openai(self) -> list[dict[str, Any]]:
        """Export tool definitions for the OpenAI / DeepSeek API."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in self._tools.values()
        ]
