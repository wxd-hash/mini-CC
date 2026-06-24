"""Permission gate — matches cc-mini's PermissionChecker with mode awareness.

Modes: plan (read-only + explore), ask (prompt for writes), auto (allow all).
Integrates with PlanModeManager for sub-agent permission isolation.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from src import terminal as term
from src.tools.base import Tool


class Mode(Enum):
    PLAN = "plan"
    ASK = "ask"
    AUTO = "auto"


# Single-word dangerous commands — these always prompt for confirmation
_HIGH_RISK_WORDS = re.compile(
    r"\b(?:rm|del|rmdir|rd|sudo|curl|wget|ssh|scp|chmod|chown"
    r"|taskkill|tskill|kill|killall|pkill|format|mkfs)\b",
    re.IGNORECASE,
)
# Multi-word dangerous commands
_HIGH_RISK_PHRASES = [
    re.compile(r"\bgit\s+push\b", re.IGNORECASE),
    re.compile(r"\bpip\s+install\b", re.IGNORECASE),
    re.compile(r"\bnpm\s+install\b", re.IGNORECASE),
    re.compile(r"\bdocker\s+(rm|rmi|stop|kill)\b", re.IGNORECASE),
    re.compile(r"\bgit\s+reset\s+--hard\b", re.IGNORECASE),
    re.compile(r"\bshutdown\b", re.IGNORECASE),
    re.compile(r"\breboot\b", re.IGNORECASE),
    re.compile(r"\bwget\b.*\b-O\b", re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# Self-preservation: patterns that would kill this agent process
# ---------------------------------------------------------------------------

_SELF_DESTRUCT_PATTERNS = [
    # ── ONLY block mass-kill-by-name of Python ──
    # taskkill /F /IM python.exe  — kills ALL Python (including this agent)
    re.compile(r"taskkill\s+(/F\s+)?/IM\s+python", re.IGNORECASE),
    # tskill python / killall python / pkill python
    re.compile(r"tskill\s+python", re.IGNORECASE),
    re.compile(r"(killall|pkill)\s+.*python", re.IGNORECASE),
    # kill -9 -1  — kills all processes on the system
    re.compile(r"kill\s+-9\s+-1", re.IGNORECASE),
    # taskkill /F /IM *  — kills ALL processes
    re.compile(r"taskkill\s+/F\s+/IM\s+\*", re.IGNORECASE),
    # Fork bombs
    re.compile(r":\(\)\s*\{", re.IGNORECASE),
    re.compile(r"%0\|%0", re.IGNORECASE),
]
# NOTE: taskkill /PID <number> is intentionally NOT blocked — killing a
# specific process by PID is safe. The model can use this to restart a
# specific server without killing the agent.


def _is_self_destructive(command: str) -> bool:
    """Check if a command would kill this agent process. NEVER allowed."""
    return any(p.search(command) for p in _SELF_DESTRUCT_PATTERNS)


class PermissionChecker:
    """deny-first permission gate matching cc-mini's PermissionChecker.

    Returns "allow" | "deny" | "ask" from check().
    Plan mode integration via set_plan_manager().
    Dream mode isolation via enter_dream_mode() / exit_dream_mode().
    """

    def __init__(
        self,
        auto_approve: bool = False,
        sandbox_manager: Any = None,
    ) -> None:
        self.auto_approve = auto_approve
        self._sandbox_manager = sandbox_manager
        self._plan_manager: Any = None
        self._always_allow: set[str] = set()  # "don't ask again" for current turn
        self._dream_mode = False
        self._plan_mode = False
        self._mode = Mode.ASK

    # -- mode control ---------------------------------------------------------

    @property
    def mode(self) -> Mode:
        return self._mode

    @mode.setter
    def mode(self, value: Mode) -> None:
        self._mode = value

    # -- plan manager integration (matches cc-mini) --------------------------

    def set_plan_manager(self, plan_manager: Any) -> None:
        self._plan_manager = plan_manager

    # -- dream mode isolation (matches cc-mini) ------------------------------

    def enter_dream_mode(self, memory_dir: str) -> None:
        self._dream_mode = True

    def exit_dream_mode(self) -> None:
        self._dream_mode = False

    # -- main check API (matches cc-mini's check(tool, input) → str) ---------

    def check(self, tool: Tool | str, tool_input: dict[str, Any] | None = None) -> str:
        """Check permission for a tool call.

        Returns: "allow" | "deny" | "ask"
        """
        if tool_input is None:
            tool_input = {}

        tool_name = tool if isinstance(tool, str) else tool.name

        # ═══════════════════════════════════════════════════════════════
        # SELF-PRESERVATION: commands that kill this process are NEVER
        # allowed, not even with confirmation. This runs BEFORE auto_approve.
        # ═══════════════════════════════════════════════════════════════
        if tool_name == "run_shell":
            cmd = tool_input.get("command", "")
            if _is_self_destructive(cmd):
                self._deny_self_destruct(cmd)
                return "deny"

        # Auto-approve flag bypasses everything
        if self.auto_approve:
            return "allow"

        # Dream mode: allow reads, deny writes/shell
        if self._dream_mode:
            return self._check_dream(tool_name)

        # Plan mode: check delegate to plan manager first
        if self._plan_mode and self._plan_manager is not None:
            pm_result = self._plan_manager.check_permission(tool_name, tool_input)
            if pm_result is not None:
                return pm_result

        # Per-mode logic
        if self._mode == Mode.PLAN:
            return self._check_plan(tool_name)

        # run_shell high-risk commands ALWAYS prompt
        if tool_name == "run_shell" and self._is_high_risk(tool_input.get("command", "")):
            result = self._prompt(tool_name, tool_input)
            return "allow" if result else "deny"

        # "Don't ask again" grants (turn-scoped)
        if tool_name in self._always_allow:
            return "allow"

        if self._mode == Mode.ASK:
            return self._check_ask(tool_name, tool_input)

        if self._mode == Mode.AUTO:
            return self._check_auto(tool_name, tool_input)

        return "deny"

    # -- legacy compatibility -------------------------------------------------

    def reset_for_turn(self) -> None:
        """Clear turn-scoped auto-allow grants (legacy API)."""
        self._always_allow.clear()

    # ------------------------------------------------------------------
    # Mode helpers
    # ------------------------------------------------------------------

    def _check_dream(self, tool_name: str) -> str:
        if tool_name in ("read_file", "list_files", "search_files", "git_diff"):
            return "allow"
        return "deny"

    def _check_plan(self, tool_name: str) -> str:
        if tool_name in ("read_file", "list_files", "search_files", "git_diff"):
            return "allow"
        self._deny_msg(tool_name)
        return "deny"

    def _check_ask(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        if tool_name in ("read_file", "list_files", "search_files", "git_diff"):
            return "allow"
        result = self._prompt(tool_name, tool_input)
        return "allow" if result else "deny"

    def _check_auto(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        if tool_name in ("read_file", "list_files", "write_file", "search_files", "git_diff"):
            return "allow"
        if tool_name == "run_shell":
            if self._is_high_risk(tool_input.get("command", "")):
                result = self._prompt(tool_name, tool_input)
                return "allow" if result else "deny"
            return "allow"
        return "deny"

    # ------------------------------------------------------------------
    # Risk classifier
    # ------------------------------------------------------------------

    @staticmethod
    def _is_high_risk(command: str) -> bool:
        """True if *command* contains known dangerous patterns."""
        if _HIGH_RISK_WORDS.search(command):
            return True
        return any(p.search(command) for p in _HIGH_RISK_PHRASES)


    # ------------------------------------------------------------------
    # User prompt
    # ------------------------------------------------------------------

    def _prompt(self, tool_name: str, tool_input: dict[str, Any]) -> bool:
        summary = self._summarize(tool_name, tool_input)
        print()
        print(term.permission_prompt(f"Allow {summary}?"))

        options = ["Yes", "Yes, and don't ask again", "No"]
        try:
            idx = term.select_menu(options)
        except Exception:
            # Fallback: if terminal doesn't support raw input, use input()
            print("  [y] Yes  [a] Yes, always  [n] No  ", end="", flush=True)
            try:
                resp = input().strip().lower()
            except (EOFError, KeyboardInterrupt):
                return False
            if resp in ("a", "yes always", "always"):
                self._always_allow.add(tool_name)
                return True
            if resp in ("y", "yes", ""):
                return True
            self._deny_msg(tool_name)
            return False

        if idx == 1:
            self._always_allow.add(tool_name)
            return True
        if idx == 0:
            return True

        self._deny_msg(tool_name)
        return False

    def _deny_msg(self, tool_name: str) -> None:
        print(term.denied(f"Denied by permission system (mode={self._mode.value}): {tool_name}"))

    def _deny_self_destruct(self, command: str) -> None:
        """Notify user that a self-destructive command was blocked."""
        print()
        print(term.permission_prompt("BLOCKED: Self-destructive command detected!"))
        print(term.error(f"  Command: {command[:100]}"))
        print(term.error("  This would kill this agent process. Permanently denied."))

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    @staticmethod
    def _summarize(tool_name: str, tool_input: dict[str, Any]) -> str:
        if tool_name == "read_file":
            return f"read_file({tool_input.get('path', '?')})"
        if tool_name == "write_file":
            return f"write_file({tool_input.get('path', '?')})"
        if tool_name == "list_files":
            return f"list_files({tool_input.get('path', '.')})"
        if tool_name == "run_shell":
            cmd = tool_input.get("command", "?")
            return f"run_shell({cmd[:80]})"
        if tool_name == "search_files":
            return f"search_files({tool_input.get('query', '?')})"
        if tool_name == "git_diff":
            return "git_diff()"
        return f"{tool_name}(...)"
