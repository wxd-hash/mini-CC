"""AskUserQuestion tool — model asks user for clarification.

Matches claude-code's AskUserQuestion: presents a question with labeled options,
user selects one. Used when the model needs direction the codebase can't provide.
"""

from __future__ import annotations

import sys
from typing import Any

from src.tools.base import Tool, ToolResult
from src import terminal as term


class AskUserQuestionTool(Tool):
    """Tool for asking the user a multiple-choice question."""

    def __init__(self) -> None:
        pass

    @property
    def name(self) -> str:
        return "ask_user"

    @property
    def description(self) -> str:
        return (
            "当需要用户做出选择或确认时，向用户提问。"
            "提供 2-4 个选项，用户选择一个。"
            "不同问 README 里有答案的、代码里能推断的、或者项目能自行判断的。"
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "要问的问题",
                },
                "header": {
                    "type": "string",
                    "description": "简短标签（最多 12 字），显示在选择器顶部",
                },
                "options": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string", "description": "选项的简短标签"},
                            "description": {"type": "string", "description": "选项的含义说明"},
                        },
                        "required": ["label", "description"],
                    },
                    "description": "2-4 个选项",
                    "minItems": 2,
                    "maxItems": 4,
                },
            },
            "required": ["question", "header", "options"],
        }

    def is_read_only(self) -> bool:
        return True

    def get_activity_description(self, **kwargs: Any) -> str | None:
        header = kwargs.get("header", "")
        return f"Asking: {header}" if header else "Asking user"

    def execute(self, **kwargs: Any) -> ToolResult:
        question = kwargs.get("question", "")
        header = kwargs.get("header", "")
        options = kwargs.get("options", [])

        if not options or len(options) < 2:
            return ToolResult(content="Error: at least 2 options required", is_error=True)

        print()
        print(term.bold(f"  {header}"))
        print(f"  {term._DIM}{question}{term._RESET}")
        print()

        labels = [opt.get("label", "") for opt in options]
        idx = term.select_menu(labels)
        if idx < 0:
            return ToolResult(content="User cancelled the question.", is_error=False)

        chosen = options[idx]
        return ToolResult(content=f"User selected: '{chosen.get('label', '')}' — {chosen.get('description', '')}")
