"""TodoWrite and TodoUpdate tools — task tracking for complex work.

Matches claude-code's TodoWrite/TodoUpdate: lets the model break down large
tasks into tracked subtasks. Users can see progress.
"""

from __future__ import annotations

from typing import Any

from src.tools.base import Tool, ToolResult


class TodoWriteTool(Tool):
    """Create or replace the task list."""

    def __init__(self) -> None:
        pass

    @property
    def name(self) -> str:
        return "todo_write"

    @property
    def description(self) -> str:
        return (
            "创建或替换当前的任务列表。用于将复杂任务拆分为可追踪的子任务。"
            "每个任务有 subject（标题）、description（描述）、status（pending/in_progress/completed）。"
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "subject": {"type": "string", "description": "任务标题"},
                            "description": {"type": "string", "description": "任务描述"},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"],
                                "description": "任务状态",
                            },
                        },
                        "required": ["subject", "description", "status"],
                    },
                    "description": "任务列表",
                },
            },
            "required": ["tasks"],
        }

    def is_read_only(self) -> bool:
        return False

    def get_activity_description(self, **kwargs: Any) -> str | None:
        tasks = kwargs.get("tasks", [])
        return f"Writing {len(tasks)} tasks"

    def execute(self, **kwargs: Any) -> ToolResult:
        from src.features.todo import TodoManager
        tasks = kwargs.get("tasks", [])
        if not tasks:
            return ToolResult(content="Error: tasks list is empty", is_error=True)

        TodoManager.set_tasks(tasks)
        counts = {}
        for t in tasks:
            s = t.get("status", "pending")
            counts[s] = counts.get(s, 0) + 1
        summary = ", ".join(f"{v} {k}" for k, v in counts.items())
        lines = [f"Tasks updated ({summary}):"]
        for i, t in enumerate(tasks, 1):
            status_icon = {"pending": "○", "in_progress": "●", "completed": "✓"}.get(
                t.get("status", "pending"), "?"
            )
            lines.append(f"  {status_icon} [{i}] {t.get('subject', '?')}")
        return ToolResult(content="\n".join(lines))


class TodoUpdateTool(Tool):
    """Update individual task statuses."""

    def __init__(self) -> None:
        pass

    @property
    def name(self) -> str:
        return "todo_update"

    @property
    def description(self) -> str:
        return "更新任务列表中指定任务的状态。用于标记任务进度。"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "updates": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "index": {
                                "type": "integer",
                                "description": "任务编号（1-based，从 todo_write 输出中获取）",
                            },
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"],
                                "description": "新状态",
                            },
                        },
                        "required": ["index", "status"],
                    },
                    "description": "要更新的任务列表",
                },
            },
            "required": ["updates"],
        }

    def is_read_only(self) -> bool:
        return False

    def get_activity_description(self, **kwargs: Any) -> str | None:
        updates = kwargs.get("updates", [])
        return f"Updating {len(updates)} tasks"

    def execute(self, **kwargs: Any) -> ToolResult:
        from src.features.todo import TodoManager
        updates = kwargs.get("updates", [])
        if not updates:
            return ToolResult(content="Error: updates list is empty", is_error=True)

        tasks = TodoManager.get_tasks()
        if not tasks:
            return ToolResult(content="Error: no tasks to update. Use todo_write first.", is_error=True)

        lines = ["Tasks updated:"]
        for upd in updates:
            idx = upd.get("index", 0) - 1
            new_status = upd.get("status", "pending")
            if 0 <= idx < len(tasks):
                old = tasks[idx].get("status", "?")
                tasks[idx]["status"] = new_status
                status_icon = {"pending": "○", "in_progress": "●", "completed": "✓"}.get(new_status, "?")
                lines.append(f"  [{idx + 1}] {tasks[idx]['subject']}: {old} → {status_icon} {new_status}")
            else:
                lines.append(f"  Error: index {upd['index']} out of range (1-{len(tasks)})")

        TodoManager.set_tasks(tasks)
        return ToolResult(content="\n".join(lines))
