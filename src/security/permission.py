"""Permission gate — deny-first access control for tool execution."""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from src import terminal as term


class Mode(Enum):
    PLAN = "plan"
    ASK = "ask"
    AUTO = "auto"


# Single-word dangerous commands
_HIGH_RISK_WORDS = re.compile(
    r"\b(?:rm|del|rmdir|rd|sudo|curl|wget|ssh|scp|chmod|chown)\b"
)
# Multi-word dangerous commands
_HIGH_RISK_PHRASES = [
    re.compile(r"\bgit\s+push\b"),
    re.compile(r"\bpip\s+install\b"),
    re.compile(r"\bnpm\s+install\b"),
]


class PermissionManager:
    """deny-first permission gate: plan > ask > auto.

    Modes
    -----
    - **plan**: read_file / list_files allowed; write / shell denied.
    - **ask**: read_file / list_files auto; write / shell require y/n prompt.
    - **auto**: all allowed except high-risk shell commands (still prompt).

    "Don't ask again" grants are per-**turn** (one user message + its tool chain).
    They reset at the start of each new user input.
    """

    def __init__(self, mode: Mode = Mode.ASK) -> None:
        self.mode = mode
        self._always_allow: set[str] = set()  # "don't ask again" for current turn

    def reset_for_turn(self) -> None:
        """Clear turn-scoped auto-allow grants. Called at the start of each user turn."""
        self._always_allow.clear()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(self, tool_name: str, args: dict[str, Any]) -> bool:
        """Return ``True`` if the tool call is permitted, ``False`` if denied."""

        # PLAN mode denies write/run_shell unconditionally — no exceptions
        if self.mode == Mode.PLAN:
            return self._check_plan(tool_name)

        # run_shell high-risk commands ALWAYS prompt, even with "don't ask again"
        if tool_name == "run_shell" and self._is_high_risk(args.get("command", "")):
            return self._prompt(tool_name, args)

        # "Don't ask again" — applies to all tools for non-high-risk cases
        if tool_name in self._always_allow:
            return True

        if self.mode == Mode.ASK:
            return self._check_ask(tool_name, args)

        if self.mode == Mode.AUTO:
            return self._check_auto(tool_name, args)

        return False

    # ------------------------------------------------------------------
    # Mode helpers
    # ------------------------------------------------------------------

    def _check_plan(self, tool_name: str) -> bool:
        if tool_name in ("read_file", "list_files", "search_files", "git_diff"):
            return True
        self._deny(tool_name)
        return False

    def _check_ask(self, tool_name: str, args: dict[str, Any]) -> bool:
        if tool_name in ("read_file", "list_files", "search_files", "git_diff"):
            return True
        return self._prompt(tool_name, args)

    def _check_auto(self, tool_name: str, args: dict[str, Any]) -> bool:
        if tool_name in ("read_file", "list_files", "write_file", "search_files", "git_diff"):
            return True
        if tool_name == "run_shell":
            if self._is_high_risk(args.get("command", "")):
                return self._prompt(tool_name, args)
            return True
        return False

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

    def _prompt(self, tool_name: str, args: dict[str, Any]) -> bool:
        summary = self._summarize(tool_name, args)
        print()
        print(term.permission_prompt(f"Allow {summary}?"))

        options = ["Yes", "Yes, and don't ask again", "No"]
        idx = term.select_menu(options)

        if idx == 1:
            self._always_allow.add(tool_name)
            return True
        if idx == 0:
            return True

        self._deny(tool_name)
        return False

    def _deny(self, tool_name: str) -> None:
        print(term.denied(f"Denied by permission system (mode={self.mode.value}): {tool_name}"))

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    @staticmethod
    def _summarize(tool_name: str, args: dict[str, Any]) -> str:
        if tool_name == "read_file":
            return f"read_file({args.get('path', '?')})"
        if tool_name == "write_file":
            return f"write_file({args.get('path', '?')})"
        if tool_name == "list_files":
            return f"list_files({args.get('path', '.')})"
        if tool_name == "run_shell":
            cmd = args.get("command", "?")
            return f"run_shell({cmd[:80]})"
        return f"{tool_name}(...)"
