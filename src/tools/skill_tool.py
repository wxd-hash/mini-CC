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
            "当用户的任务匹配某个已注册的技能时，这是一个**强制要求**：",
            "在生成任何其他回复之前，必须先调用 Skill 工具。",
            "",
            "触发示例（用户说什么 → 立即调什么）：",
            "- 用户说\"帮我review代码\"→ 立即 Skill(name=\"review\")",
            "- 用户说\"提交一下\"→ 立即 Skill(name=\"commit\")",
            "- 用户说\"跑个测试\"→ 立即 Skill(name=\"test\")",
            "- 用户说\"简化这段\"→ 立即 Skill(name=\"simplify\")",
            "",
            "重要规则：",
            "- 如果看到对话中有 <command-name> 标签，技能已加载，直接遵循指令",
            "- 绝不提及某技能而不实际调用 Skill 工具",
            "- 不要对内置 CLI 命令（/help, /clear 等）使用此工具",
            "- Skill 调用不需要用户确认，始终自动允许",
            "",
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
        # Honour disable_model_invocation flag (matching Claude Code)
        if getattr(skill, 'disable_model_invocation', False):
            return ToolResult(
                content=f"技能 /{name} 不允许模型自动调用。请用户手动输入 /{name} 触发。",
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
