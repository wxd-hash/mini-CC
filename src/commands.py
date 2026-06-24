"""Slash command handlers — legacy commands + new cc-mini-style skills.

Commands:
  /perm <plan|ask|auto|status>  — permission mode
  /tools                        — list tools
  /tool <name> <json_args>      — manual tool invocation
  /clear                        — clear conversation
  /reload                       — rebuild system prompt
  /compact                      — compact conversation
  /resume [session]             — resume a previous session
  /history                      — list saved sessions
  /skills                       — list available skills
  /plan                         — enter plan mode
  /review, /commit, /test, /simplify — bundled skills
  /exit, /quit                  — exit
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src import terminal as term
from src.security.permission import PermissionChecker, Mode
from src.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Command context (matches cc-mini's CommandContext)
# ---------------------------------------------------------------------------

class CommandContext:
    """Holds all dependencies needed by slash command handlers."""

    def __init__(
        self,
        session_store: Any = None,
        permissions: PermissionChecker | None = None,
    ) -> None:
        self.session_store = session_store
        self.permissions = permissions


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------

def do_resume(
    engine: Any,
    log_dir: Path,
    workspace: str,
    resume_arg: Any,
) -> None:
    """Run the resume flow: find session, show picker, load + print history."""
    from src.session.logger import list_sessions, load_session_messages, _workspace_dir_name

    session_path: Path | None = None

    if resume_arg is True:
        sessions = list_sessions(log_dir, workspace)
        if not sessions:
            print(term.info("No previous sessions for this workspace."))
            print(term.info("Starting fresh."))
            print()
            return

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
    engine.resume(messages)
    print(term.success(f"Resumed from {session_path.name} ({len(messages)} messages)"))
    print()


def _print_history(messages: list[dict[str, Any]]) -> None:
    print(term.hr())
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "user":
            print(f"\n{term.prompt()}{content}")
        elif isinstance(content, str) and (
            content.startswith("[tool]") or content.startswith("[called")
        ):
            for line in content.split("\n"):
                print(f"  {line}")
        elif isinstance(content, str) and (
            content.startswith("[permission_denied]") or content.startswith("[error]")
        ):
            print(term.error(content))
        else:
            print(f"\n{term.assistant_text(content)}")
    print(f"\n{term.hr()}")


# ---------------------------------------------------------------------------
# Slash command dispatchers
# ---------------------------------------------------------------------------

def handle_perm(permission: PermissionChecker, rest: str) -> None:
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


def handle_tool(registry: ToolRegistry, permission: PermissionChecker, rest: str) -> None:
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

    perm = permission.check(tool, args)
    if perm == "deny":
        return

    try:
        result = tool.execute(**args)
        content = result.content if hasattr(result, 'content') else str(result)
        is_err = result.is_error if hasattr(result, 'is_error') else False
        if is_err:
            print(term.tool_error(content))
        else:
            print(term.tool_done(content))
    except Exception as exc:
        print(term.error(f"Tool error: {exc}"))


# ---------------------------------------------------------------------------
# New cc-mini-style commands
# ---------------------------------------------------------------------------

def handle_skills() -> None:
    """List all available skills."""
    from src.features.skills import list_skills
    skills = list_skills()
    if not skills:
        print(term.info("No skills registered."))
        return
    print(term.hr())
    for s in skills:
        print(term.banner_line(f"/{s.name}", s.description))
    print(term.hr())


def handle_skill_command(skill_name: str, args: str, engine: Any) -> None:
    """Run a skill by name, submitting its prompt to the engine."""
    from src.features.skills import get_skill
    skill = get_skill(skill_name)
    if skill is None:
        print(term.error(f"Unknown skill: /{skill_name}"))
        print(term.info("Type /skills to see available skills."))
        return

    prompt = skill.run(args)
    if prompt is None:
        print(term.info(f"Skill /{skill_name} produced no prompt."))
        return

    print(term.info(f"Running skill: /{skill_name}"))
    engine.run(prompt)


def handle_history(log_dir: Path, workspace: str) -> None:
    """List saved sessions."""
    from src.session.logger import list_sessions
    sessions = list_sessions(log_dir, workspace)
    if not sessions:
        print(term.info("No saved sessions for this workspace."))
        return
    print(term.hr())
    for i, (path, name, ts) in enumerate(sessions, 1):
        print(term.banner_line(f"  [{i}]", f"{name[:50]}  {ts}"))
    print(term.hr())
    print(term.info("Use /resume <number> to resume a session."))


# ---------------------------------------------------------------------------
# Command dispatcher
# ---------------------------------------------------------------------------

def handle_command(
    cmd_name: str,
    cmd_args: str,
    engine: Any,
    registry: ToolRegistry,
    permission: PermissionChecker,
    log_dir: Path,
    workspace: str,
    session_store: Any = None,
) -> bool:
    """Dispatch a slash command. Returns True if handled, False if not a command."""
    cmd = cmd_name.lower()

    if cmd in ("exit", "quit"):
        return True  # caller handles exit

    if cmd == "perm":
        handle_perm(permission, cmd_args)
        return True

    if cmd == "tools":
        handle_tools(registry)
        return True

    if cmd == "tool":
        handle_tool(registry, permission, cmd_args)
        return True

    if cmd == "clear":
        if engine:
            engine.clear()
        return True

    if cmd == "reload":
        if engine:
            engine.reload()
        return True

    if cmd == "compact":
        if engine:
            engine._maybe_compact()
        return True

    if cmd == "skills":
        handle_skills()
        return True

    if cmd == "history":
        handle_history(log_dir, workspace)
        return True

    if cmd in ("review", "commit", "test", "simplify"):
        handle_skill_command(cmd, cmd_args, engine)
        return True

    if cmd == "plan":
        print(term.info("Plan mode: describe what you want to plan, e.g. /plan add authentication"))
        if cmd_args.strip():
            engine.run(f"Plan: {cmd_args.strip()}")
        return True

    return False
