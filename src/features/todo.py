"""TodoManager — simple in-memory task list tracking."""

from __future__ import annotations

from typing import Any


class TodoManager:
    """Manages a task list for the current session.

    Tasks are stored in memory and displayed to the user.
    Matches claude-code's TodoWrite/TodoUpdate pattern.
    """

    _tasks: list[dict[str, Any]] = []
    _instance: TodoManager | None = None

    def __new__(cls) -> TodoManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._tasks = []
        return cls._instance

    @classmethod
    def set_tasks(cls, tasks: list[dict[str, Any]]) -> None:
        cls._tasks = list(tasks)

    @classmethod
    def get_tasks(cls) -> list[dict[str, Any]]:
        return list(cls._tasks)

    @classmethod
    def get_display(cls) -> str | None:
        if not cls._tasks:
            return None
        lines = ["  Tasks:"]
        for i, t in enumerate(cls._tasks, 1):
            icon = {"pending": "○", "in_progress": "●", "completed": "✓"}.get(
                t.get("status", "pending"), "?"
            )
            lines.append(f"    {icon} [{i}] {t.get('subject', '?')}")
        return "\n".join(lines)
