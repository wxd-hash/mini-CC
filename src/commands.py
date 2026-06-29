"""Slash command handlers — legacy commands + new cc-mini-style skills.

Commands:
  /perm <plan|ask|auto|status>  — permission mode
  /tools                        — list tools
  /tool <name> <json_args>      — manual tool invocation
  /init                         — create/update CLAUDE.md
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
import sys
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
# Session picker helpers
# ---------------------------------------------------------------------------

def _relative_time(ts_str: str) -> str:
    """Convert timestamp like '06-24 14:30' to relative time (matching claude-code)."""
    if not ts_str:
        return ""
    try:
        from datetime import datetime, timedelta
        now = datetime.now()
        parts = ts_str.split()
        date_part = parts[0]
        time_part = parts[1] if len(parts) > 1 else "00:00"
        month, day = map(int, date_part.split("-"))
        hour, minute = map(int, time_part.split(":"))
        dt = datetime(now.year, month, day, hour, minute)
        if dt > now:
            dt = dt.replace(year=now.year - 1)
        delta = now - dt
        if delta < timedelta(hours=1):
            mins = max(1, int(delta.total_seconds() / 60))
            return f"{mins}m ago"
        elif delta < timedelta(hours=24):
            return f"{int(delta.total_seconds() / 3600)}h ago"
        elif delta < timedelta(days=7):
            return f"{delta.days}d ago"
        else:
            return ts_str
    except Exception:
        return ts_str


def _print_session_picker(sessions: list) -> None:
    """Print a numbered session list (matches claude-code's resume picker)."""
    try:
        import shutil
        width = min(shutil.get_terminal_size().columns, 120) - 8
    except Exception:
        width = 80

    print()
    print(term.bold("Recent sessions:"))
    print(term.hr_fixed(width))
    for i, (path, name, ts) in enumerate(sessions, 1):
        rel = _relative_time(ts)
        display = name[:width - 25] if len(name) > width - 25 else name
        print(f"  {term._GREEN}{i:>2}{term._RESET}. {display:<{width - 22}} {term._DIM}{rel}{term._RESET}")
    print(term.hr_fixed(width))
    print(f"  {term._DIM}Enter = start fresh,  1-{len(sessions)} = resume,  q = cancel{term._RESET}")


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------

def _handle_init(engine: Any, cmd_args: str) -> bool:
    """Handle /init command — launch model to analyze codebase and create CLAUDE.md.

    Matches claude-code's OLD_INIT_PROMPT exactly: sends a specialized prompt
    telling the model to survey the project and write CLAUDE.md.
    """
    from pathlib import Path

    ws = getattr(engine, '_workspace_dir', None) or Path.cwd()
    ws = Path(ws)
    claude_md = ws / "CLAUDE.md"
    exists = claude_md.exists()
    action = "suggest improvements to" if exists else "create"

    prompt = (
        f"Please analyze this codebase and {action} a CLAUDE.md file, "
        f"which will be given to future instances to operate in this repository.\n\n"
        f"What to add:\n"
        f"1. Commands that will be commonly used, such as how to build, lint, "
        f"and run tests. Include the necessary commands to develop in this "
        f"codebase, such as how to run a single test.\n"
        f"2. High-level code architecture and structure so that future instances "
        f"can be productive more quickly. Focus on the 'big picture' architecture "
        f"that requires reading multiple files to understand.\n\n"
        f"Usage notes:\n"
        f"- When you make the initial CLAUDE.md, do not repeat yourself and do not "
        f"include obvious instructions like 'Provide helpful error messages to users', "
        f"'Write unit tests for all new utilities', 'Never include sensitive "
        f"information in code or commits'.\n"
        f"- Avoid listing every component or file structure that can be easily discovered.\n"
        f"- Don't include generic development practices.\n"
        f"- If there is a README.md, make sure to include the important parts.\n"
        f"- Do not make up information such as 'Common Development Tasks', "
        f"'Tips for Development', 'Support and Documentation' unless this is "
        f"expressly included in other files that you read.\n"
        f"- Be sure to prefix the file with the following text:\n\n"
        f"```\n"
        f"# CLAUDE.md\n\n"
        f"This file provides guidance to Claude Code (claude.ai/code) when "
        f"working with code in this repository.\n"
        f"```\n\n"
        f"用中文写 CLAUDE.md 的内容，但保留上面的英文前缀。"
    )

    print(term.hr())
    print(term.info(f"正在分析项目并{action.replace('suggest improvements to', '更新').replace('create', '创建')} CLAUDE.md..."))
    print(term.hr())
    engine.run(prompt)
    return True


def do_resume(
    engine: Any,
    log_dir: Path,
    workspace: str,
    resume_arg: Any,
) -> None:
    """Run the resume flow: find session, show picker, load + print history."""
    import sys as _sys
    from src.session.logger import list_sessions, load_session_messages, _workspace_dir_name

    try:
        session_path: Path | None = None

        if resume_arg is True:
            sessions = list_sessions(log_dir, workspace)
            if not sessions:
                print(term.info("No previous sessions for this workspace."))
                print(term.info("Starting fresh."))
                print()
                return

            choice_input = ""
            while not choice_input:
                _print_session_picker(sessions)
                try:
                    choice_input = input(f"\n  {term._BOLD_GREEN}Resume [1-{len(sessions)}] or Enter for fresh >{term._RESET} ").strip()
                except (EOFError, KeyboardInterrupt):
                    # Double-press Ctrl+C to exit, any key to re-show picker
                    import time as _time
                    _sys.stdout.write(f"\n{term._YELLOW}Press Ctrl+C again to exit, any key to continue{term._RESET}\n")
                    _sys.stdout.flush()
                    deadline = _time.monotonic() + 0.8
                    while _time.monotonic() < deadline:
                        try:
                            ch = term._getch()
                        except KeyboardInterrupt:
                            _sys.stdout.write("Goodbye.\n")
                            _sys.exit(0)
                        if ch == "\x03":
                            _sys.stdout.write("Goodbye.\n")
                            _sys.exit(0)
                        break
                    _sys.stdout.write("\033[A\033[K\033[A\033[K")
                    _sys.stdout.flush()
                    continue  # re-display picker

            if not choice_input or choice_input.lower() in ("q", "n", "fresh"):
                print(term.info("Starting fresh."))
                print()
                return

            try:
                idx = int(choice_input) - 1
                if 0 <= idx < len(sessions):
                    session_path = sessions[idx][0]
                else:
                    print(term.error(f"Invalid number: {choice_input}"))
                    print()
                    return
            except ValueError:
                needle = choice_input.lower()
                matches = [s for s in sessions if s[2].lower().startswith(needle)]
                if matches:
                    session_path = matches[0][0]
                else:
                    print(term.error(f"Session not found: {choice_input}"))
                    print()
                    return
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

    except KeyboardInterrupt:
        print()
        _sys.exit(0)


def _print_history(messages: list[dict[str, Any]]) -> None:
    """Print loaded session messages with exact live-session terminal styles.

    Uses _type field set by load_session_messages:
      user_input      →  > green prompt
      assistant_text  →  plain text
      tool_call       →  ↳ cyan tool name (dim)
      tool_result     →  ↳ ✓ green / ↳ ✗ red
      permission_denied / error → red
    """
    print(term.hr())
    for msg in messages:
        msg_type = msg.get("_type", "")
        content = msg.get("content", "")

        if msg_type == "user_input":
            sys.stdout.write(f"\n\n  {term._BOLD_GREEN}▶ {content}{term._RESET}\n")
            sys.stdout.flush()

        elif msg_type == "assistant_text":
            from src.agent.loop import render_markdown
            print(f"\n{render_markdown(content)}")

        elif msg_type == "tool_call":
            print(f"  {term._DIM}↳ {term._CYAN}{content}{term._RESET}")

        elif msg_type == "tool_result":
            result = content
            is_err = msg.get("is_error", False)
            first_line = result.split("\n")[0][:200]
            if is_err:
                print(f"    {term._RED}✗{term._RESET} {term._RED}{first_line}{term._RESET}")
            else:
                print(f"    {term._GREEN}✓{term._RESET} {term._DIM}{first_line}{term._RESET}")

        elif msg_type in ("permission_denied", "error"):
            print(f"  {term._RED}{msg_type}: {content}{term._RESET}")

        else:
            # Fallback for old-format messages without _type
            role = msg.get("role", "")
            if role == "user":
                print(f"\n  {term._BOLD_GREEN}>{term._RESET} {str(content)[:500]}")
            else:
                print(f"\n{str(content)[:500]}")

    print(term.hr())
    print()


# ---------------------------------------------------------------------------
# Slash command dispatchers
# ---------------------------------------------------------------------------

def handle_perm(permission: PermissionChecker, rest: str, engine: Any = None) -> None:
    arg = rest.strip()
    if arg == "status":
        print(term.info(f"Permission mode: {permission.mode.value}"))
    elif arg in ("plan", "ask", "auto"):
        permission.mode = Mode(arg)
        print(term.success(f"Permission mode -> {arg}"))
        if engine:
            engine.reload()  # rebuild system prompt with plan/ask/auto instructions
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

    prompt = skill.get_prompt(args)
    if not prompt:
        print(term.info(f"Skill /{skill_name} has no prompt content."))
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

    if cmd == "init":
        return _handle_init(engine, cmd_args)

    if cmd == "perm":
        handle_perm(permission, cmd_args, engine)
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
