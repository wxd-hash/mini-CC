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


# Commands that ALWAYS prompt — irreversible damage risks only.
# Claude Code handles most safety via AI classifier; mini-cc keeps a minimal
# hardcoded list. Development-common commands (curl, ssh, chmod, pip install,
# git push, etc.) are NOT blocked — they're routine operations.
_HIGH_RISK_WORDS = re.compile(
    r"\b(?:sudo|shutdown|reboot|format|mkfs)\b",
    re.IGNORECASE,
)

# Destructive remove — only prompt when force/recursive flags are
# directly attached to the command (not elsewhere in the string)
_DESTRUCTIVE_RM = re.compile(
    r"\b(?:rm|del|rmdir|rd)\b\s+(?:-[^\s]*[rf]|/[sSqQ])",
    re.IGNORECASE,
)

# Command-injection patterns — curl/wget piping to shell
_PIPE_TO_SHELL = re.compile(
    r"(?:curl|wget)\b.*\|\s*(?:bash|sh|zsh|python|perl|ruby)",
    re.IGNORECASE,
)

# Request persistent system changes
_SYSTEM_INSTALL = re.compile(
    r"\b(?:apt|yum|brew|choco|scoop)\s+(?:install|remove|purge|uninstall)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Self-preservation: patterns that would kill this agent process
# ---------------------------------------------------------------------------

_SELF_DESTRUCT_PATTERNS = [
    # ── Mass-kill-by-name of Python ──
    re.compile(r"taskkill\s+(/F\s+)?/IM\s+python", re.IGNORECASE),
    re.compile(r"tskill\s+python", re.IGNORECASE),
    re.compile(r"(killall|pkill)\s+.*python", re.IGNORECASE),
    # ── Kill own PID (the agent itself) ──
    # Built at runtime in _is_self_destructive()
    # ── System-wide kills ──
    re.compile(r"kill\s+-9\s+-1", re.IGNORECASE),
    re.compile(r"taskkill\s+/F\s+/IM\s+\*", re.IGNORECASE),
    # ── Fork bombs ──
    re.compile(r":\(\)\s*\{", re.IGNORECASE),
    re.compile(r"%0\|%0", re.IGNORECASE),
]


# Files/directories that trigger a prompt when written to in auto mode
_DANGEROUS_FILES = frozenset({
    ".gitconfig", ".gitmodules",
    ".bashrc", ".bash_profile", ".zshrc", ".zprofile", ".profile",
    ".ripgreprc", ".mcp.json", ".claude.json",
})
_DANGEROUS_DIRS = frozenset({
    ".git", ".vscode", ".idea", ".claude",
    "/etc", "/usr", "/boot", "/opt", "/var",
})


def _is_dangerous_path(path: str) -> bool:
    """Check if writing to *path* is dangerous (system config, git internals).

    Matches Claude Code's checkPathSafetyForAutoEdit.
    """
    import os
    p = path.replace("\\", "/").lower()
    name = os.path.basename(p)
    if name in _DANGEROUS_FILES:
        return True
    for d in _DANGEROUS_DIRS:
        if d in p.split("/"):
            return True
    # Windows-specific
    if "c:\\windows" in p or "c:\\program files" in p:
        return True
    return False


def _sanitize_self_kill(command: str) -> str:
    """Remove any sub-command that would kill this agent's own PID.

    Returns the sanitized command, or empty string if nothing remains.
    """
    import os
    own_pid = str(os.getpid())
    # Remove taskkill /PID <own_pid> ... (up to next & or end)
    command = re.sub(
        rf"taskkill\s+(/F\s+)?/PID\s+{own_pid}\b[^&]*\s*&?\s*",
        "", command, flags=re.IGNORECASE,
    )
    # Remove kill <own_pid>
    command = re.sub(
        rf"(?:^|\s)kill\s+(?:-9\s+)?{own_pid}\b[^;&]*\s*;?\s*",
        " ", command, flags=re.IGNORECASE,
    )
    # Remove tskill <own_pid>
    command = re.sub(
        rf"tskill\s+{own_pid}\b[^&]*\s*&?\s*",
        "", command, flags=re.IGNORECASE,
    )
    return command.strip().rstrip("&;").strip()


def _is_self_destructive(command: str) -> bool:
    """Check if a command would kill this agent process. NEVER allowed.

    Matches claude-code's approach: block killing the agent's own PID,
    but allow killing other specific PIDs (for server restart, etc).
    """
    # Check static patterns (mass kill, fork bombs, etc)
    if any(p.search(command) for p in _SELF_DESTRUCT_PATTERNS):
        return True
    # Check if command targets the agent's OWN PID
    import os, re
    own_pid = str(os.getpid())
    pid_patterns = [
        re.compile(rf"taskkill\s+(/F\s+)?/PID\s+{own_pid}\b", re.IGNORECASE),
        re.compile(rf"(?:^|\s)kill\s+(?:-9\s+)?{own_pid}\b", re.IGNORECASE),
        re.compile(rf"tskill\s+{own_pid}\b", re.IGNORECASE),
    ]
    return any(p.search(command) for p in pid_patterns)


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
        # SELF-PRESERVATION — mass-kill / fork bombs are NEVER allowed.
        # Agent's own PID in compound commands is sanitized (filtered out)
        # rather than blocking the whole command.
        # ═══════════════════════════════════════════════════════════════
        if tool_name == "run_shell":
            cmd = tool_input.get("command", "")
            # Mass-kill patterns — always block
            if _is_self_destructive(cmd):
                self._deny_self_destruct(cmd)
                return "deny"
            # Own PID in compound command — sanitize, don't block
            sanitized = _sanitize_self_kill(cmd)
            if sanitized != cmd:
                tool_input["command"] = sanitized
                if not sanitized:
                    return "deny"  # nothing left after sanitization

        # CLAUDE.md maintenance — always auto-allow, same as memory
        if tool_name in ("write_file", "edit_file"):
            path = tool_input.get("path", "")
            if path.endswith("CLAUDE.md") or path.endswith("CLAUDE.local.md"):
                return "allow"

        # Auto-approve flag bypasses everything
        if self.auto_approve:
            return "allow"

        # Dream mode: allow reads, deny writes/shell
        if self._dream_mode:
            return self._check_dream(tool_name)

        # Auto-mode: check file path safety for writes
        if self._mode == Mode.AUTO and tool_name in ("write_file", "edit_file"):
            path = tool_input.get("path", "")
            if _is_dangerous_path(path):
                result = self._prompt(tool_name, tool_input, allow_always=False)
                return "allow" if result else "deny"

        # Plan mode: check delegate to plan manager first
        if self._plan_mode and self._plan_manager is not None:
            pm_result = self._plan_manager.check_permission(tool_name, tool_input)
            if pm_result is not None:
                return pm_result

        # Per-mode logic
        if self._mode == Mode.PLAN:
            return self._check_plan(tool_name)

        # run_shell high-risk commands ALWAYS prompt — no "don't ask again"
        if tool_name == "run_shell" and self._is_high_risk(tool_input.get("command", "")):
            result = self._prompt(tool_name, tool_input, allow_always=False)
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

    # Internal tools that are always allowed (matching claude-code)
    _INTERNAL_TOOLS = {"ask_user", "todo_write", "todo_update"}

    def _check_ask(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        if tool_name in self._INTERNAL_TOOLS:
            return "allow"
        if tool_name in ("read_file", "list_files", "search_files", "git_diff", "web_fetch"):
            return "allow"
        result = self._prompt(tool_name, tool_input)
        return "allow" if result else "deny"

    def _check_auto(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        if tool_name in self._INTERNAL_TOOLS:
            return "allow"
        if tool_name in ("read_file", "list_files", "write_file", "edit_file", "search_files", "git_diff", "web_fetch"):
            return "allow"
        if tool_name == "run_shell":
            if self._is_high_risk(tool_input.get("command", "")):
                result = self._prompt(tool_name, tool_input, allow_always=False)
                return "allow" if result else "deny"
            return "allow"
        return "deny"

    # ------------------------------------------------------------------
    # Risk classifier
    # ------------------------------------------------------------------

    @staticmethod
    def _is_high_risk(command: str) -> bool:
        """True if *command* contains known dangerous patterns.

        Matches Claude Code's approach: only prompt for truly irreversible
        operations, not routine development commands.
        """
        if _HIGH_RISK_WORDS.search(command):
            return True
        if _DESTRUCTIVE_RM.search(command):
            return True
        if _PIPE_TO_SHELL.search(command):
            return True
        if _SYSTEM_INSTALL.search(command):
            return True
        return False


    # ------------------------------------------------------------------
    # User prompt
    # ------------------------------------------------------------------

    def _prompt(
        self, tool_name: str, tool_input: dict[str, Any],
        allow_always: bool = True,
    ) -> bool:
        summary = self._summarize(tool_name, tool_input)
        title = {
            "run_shell": "Shell 命令",
            "write_file": "写入文件",
            "edit_file": "编辑文件",
        }.get(tool_name, tool_name)

        if allow_always:
            options = ["是", "是，不再询问", "否"]
            fallback_hint = "  [y] Yes  [a] Yes, always  [n] No  "
        else:
            options = ["是", "否"]
            fallback_hint = "  [y] Yes  [n] No  "

        try:
            idx = term.select_menu(
                options,
                title=f"{title}: {summary}",
                footer="Esc 拒绝 · Enter 确认",
            )
        except Exception:
            print(f"  {term._YELLOW}[auth]{term._RESET} Allow {summary}?")
            print(fallback_hint, end="", flush=True)
            try:
                resp = input().strip().lower()
            except (EOFError, KeyboardInterrupt):
                return False
            if allow_always and resp in ("a", "yes always", "always"):
                self._always_allow.add(tool_name)
                return True
            if resp in ("y", "yes", ""):
                return True
            self._deny_msg(tool_name)
            return False

        if allow_always and idx == 1:
            self._always_allow.add(tool_name)
            return True
        if idx == 0:
            return True

        self._deny_msg(tool_name)
        return False

    def _deny_msg(self, tool_name: str) -> None:
        print(term.denied(f"Denied by permission system (mode={self._mode.value}): {tool_name}"))

    def _deny_self_destruct(self, command: str) -> None:
        """Notify user that a self-destructive command was blocked.

        Shows the agent's own PID so the model can target specific PIDs
        instead of mass-killing all Python processes.
        """
        import os
        own_pid = os.getpid()
        print()
        print(term.permission_prompt("BLOCKED: 会杀死 agent 自身！"))
        print(term.error(f"  {command[:120]}"))
        print(term.info(
            f"  agent PID = {own_pid}，用 taskkill /PID <目标PID> 杀特定进程，"
            f"不要用 /IM python 全部杀"
        ))

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
