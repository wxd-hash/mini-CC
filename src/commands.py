"""Slash command handlers and resume logic."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src import terminal as term
from src.agent.loop import MiniClaudeAgent
from src.security.permission import PermissionManager, Mode
from src.session.logger import list_sessions, load_session_messages
from src.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------

def do_resume(agent: MiniClaudeAgent, log_dir: Path, workspace: str, resume_arg: Any) -> None:
    """Run the resume flow: find session, show picker, load + print history."""
    session_path: Path | None = None

    if resume_arg is True:
        sessions = list_sessions(log_dir, workspace)
        if not sessions:
            print(term.info("No previous sessions for this workspace."))
            print(term.info("Starting fresh."))
            print()
            return

        if len(sessions) == 1:
            session_path = sessions[0][0]
        else:
            print(term.bold("Select a session to resume:"))
            print(term.hr())
            options = [f"{name:<50} {ts}" for _, name, ts in sessions]
            options.append("(start fresh)")
            idx = term.select_menu(options)
            if idx < 0 or idx == len(sessions):
                print(term.info("Starting fresh."))
                print()
                return
            session_path = sessions[idx][0]
    else:
        session_path = Path(resume_arg)
        if not session_path.is_file():
            print(term.info(f"Session file not found: {session_path}"))
            print(term.info("Starting fresh."))
            print()
            return

    try:
        messages = load_session_messages(session_path)
    except Exception as exc:
        print(term.error(f"Failed to load session: {exc}"))
        print(term.info("Starting fresh."))
        print()
        return

    _print_history(messages)
    agent.resume(messages)
    print(term.success(f"Resumed from {session_path.name} ({len(messages)} messages)"))
    print()


def _print_history(messages: list[dict[str, Any]]) -> None:
    print(term.hr())
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "user":
            print(f"\n{term.prompt()}{content}")
        elif content.startswith("[tool]") or content.startswith("[called"):
            for line in content.split("\n"):
                print(term.tool_result(line))
        elif content.startswith("[permission_denied]") or content.startswith("[error]"):
            print(term.error(content))
        else:
            print(f"\n{term.assistant_text(content)}")
    print(f"\n{term.hr()}")


# ---------------------------------------------------------------------------
# Slash command dispatchers
# ---------------------------------------------------------------------------

def handle_perm(permission: PermissionManager, rest: str) -> None:
    arg = rest.strip()
    if arg == "status":
        print(term.info(f"Permission mode: {permission.mode.value}"))
    elif arg in ("plan", "ask", "auto"):
        permission.mode = Mode(arg)
        print(term.success(f"Permission mode -> {arg}"))
    else:
        print(term.info("Usage: /perm <plan|ask|auto|status>"))


def handle_tools(registry: ToolRegistry) -> None:
    print(term.hr())
    for name, desc in registry.list_tools():
        print(term.banner_line(name, desc))
    print(term.hr())


def handle_tool(registry: ToolRegistry, permission: PermissionManager, rest: str) -> None:
    parts = rest.strip().split(maxsplit=1)
    if len(parts) != 2:
        print(term.info('Usage: /tool <name> <json_args>'))
        print(term.info('Example: /tool read_file {"path":"a.txt"}'))
        return

    name, raw_args = parts
    try:
        args = json.loads(raw_args)
    except json.JSONDecodeError as e:
        print(term.error(f"Invalid JSON: {e}"))
        return

    if not isinstance(args, dict):
        print(term.error("Args must be a JSON object"))
        return

    try:
        tool = registry.get_tool(name)
    except KeyError:
        print(term.error(f"Unknown tool: {name!r}"))
        return

    if not permission.check(name, args):
        return

    try:
        result = tool.run(args)
        print(term.tool_result(result))
    except Exception as exc:
        print(term.error(f"Tool error: {exc}"))
