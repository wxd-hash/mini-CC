"""SkillTool — lets the model invoke Skills as tool calls.

Matches Claude Code's pattern: skills are listed in the system prompt,
and the model calls Skill(name="review") to invoke one autonomously.
"""

from __future__ import annotations

from typing import Any

from src.tools.base import Tool, ToolResult


class SkillTool(Tool):
    """Tool that invokes a named skill, returning its prompt body.

    The model sees available skills in the system prompt and calls this
    tool when a skill matches the user's request. Users can also trigger
    skills manually via slash commands (/review, /commit, etc.).
    """

    @property
    def name(self) -> str:
        return "Skill"

    @property
    def description(self) -> str:
        from src.features.skills import list_skills
        skills = list_skills()
        if not skills:
            return "没有可用技能。"
        lines = [
            "当用户的任务匹配某个内置技能时调用此工具。",
            "例如：用户说'帮我review代码'→ 调 Skill(name=\"review\")",
            "用户说'提交一下'→ 调 Skill(name=\"commit\")",
            "用户说'跑个测试'→ 调 Skill(name=\"test\")",
            "可用技能：",
        ]
        for s in skills:
            lines.append(f"- {s.name}: {s.description}")
        return "\n".join(lines)

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "要调用的技能名称，如 review、commit、test、simplify。只能使用系统提示词中列出的技能。",
                },
                "args": {
                    "type": "string",
                    "description": "传给技能的可选参数，比如要审查的具体文件路径。",
                },
            },
            "required": ["name"],
        }

    @property
    def maxResultSizeChars(self) -> int | None:
        return 8000

    def is_read_only(self) -> bool:
        return True

    def get_activity_description(self, **kwargs: Any) -> str | None:
        name = kwargs.get("name", "skill")
        return f"调用技能 /{name}"

    def execute(self, name: str = "", args: str = "", **kwargs: Any) -> ToolResult:
        from src.features.skills import get_skill
        skill = get_skill(name)
        if skill is None:
            available = self._available_names()
            return ToolResult(
                content=f"未知技能: /{name}。可用技能: {', '.join(available)}",
                is_error=True,
            )
        prompt = skill.get_prompt(args)
        if not prompt:
            return ToolResult(
                content=f"技能 /{name} 未产生有效提示词。",
                is_error=True,
            )
        return ToolResult(content=prompt)

    @staticmethod
    def _available_names() -> list[str]:
        from src.features.skills import list_skills
        return [s.name for s in list_skills()]
