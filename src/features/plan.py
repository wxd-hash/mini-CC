"""Plan mode manager — matches cc-mini's PlanModeManager.

Enables the agent to enter plan mode, where it can launch parallel
read-only Explore sub-agents to research before implementing.
"""

from __future__ import annotations

from typing import Any, Callable


class PlanModeManager:
    """Manages plan mode: sub-agent launching and permission orchestration.

    Matches cc-mini's PlanModeManager. When active, read-only Explore
    sub-agents can be launched to research the codebase before making changes.
    """

    def __init__(self) -> None:
        self._active = False
        self._engine: Any = None
        self._build_explore_engine: Callable[[], Any] | None = None
        self._permissions: Any = None
        self._worker_manager: Any = None

    # -- bindings (set during bootstrap) ------------------------------------

    def bind_engine(
        self,
        engine: Any,
        build_explore_engine: Callable[[], Any] | None = None,
    ) -> None:
        """Bind the main engine and optional explore-engine factory."""
        self._engine = engine
        self._build_explore_engine = build_explore_engine

    def set_permissions(self, permissions: Any) -> None:
        self._permissions = permissions

    # -- state ---------------------------------------------------------------

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def worker_manager(self) -> Any:
        return self._worker_manager

    def activate(self) -> None:
        self._active = True

    def deactivate(self) -> None:
        self._active = False

    def check_permission(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> str | None:
        """Check permission for a tool in plan mode context.

        Returns "allow" | "deny" | None (None = delegate to normal checker).
        """
        if not self._active:
            return None
        # In plan mode, reads are auto-allowed, writes are denied
        if tool_name in ("read_file", "list_files", "search_files", "git_diff"):
            return "allow"
        return "deny"
