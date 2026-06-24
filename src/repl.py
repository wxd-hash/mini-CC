"""Terminal REPL — banner, prompt, input loop, slash-command routing.

Enhanced with cc-mini features: auto-compact, skill commands, history listing.
"""

from __future__ import annotations

import sys
from pathlib import Path

from src import terminal as term
from src.security.permission import PermissionChecker
from src.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------

_CAT = [
    r"    /\_/\    ",
    r"   ( o.o )   ",
    r"    > ^ <    ",
    r"   meow~     ",
]


def print_banner(provider, logger, workspace, permission) -> None:
    cat = list(_CAT)
    cat_w = max(len(c) for c in cat)

    hr_fixed = term.hr_fixed(50)
    info = [
        term.bold("Mini Claude Code"),
        hr_fixed,
        term.banner_line("Provider", f"{provider.provider_name}  ({provider.model})"),
        term.banner_line("Session", logger.path.name if hasattr(logger, 'path') else str(logger.session_id)[:16]),
        term.banner_line("Workspace", str(workspace.root)),
        term.banner_line("Mode", permission.mode.value),
        hr_fixed,
        term.info("/tools  /tool  /perm  /skills  /history  /clear  /reload  /exit"),
    ]

    while len(cat) < len(info):
        cat.insert(0, "") if len(cat) % 2 == 0 else cat.append("")
    while len(info) < len(cat):
        info.append("")

    print(term.hr())
    for c, i in zip(cat, info):
        pad = cat_w - len(c) + 3
        print(f"  {c}{' ' * pad}{i}")
    print(term.hr())


# ---------------------------------------------------------------------------
# REPL
# ---------------------------------------------------------------------------

def run_repl(
    registry: ToolRegistry,
    permission: PermissionChecker,
    engine: Any,
    log_dir: Path | None = None,
    workspace: str = "",
    session_store: Any = None,
) -> None:
    """Interactive REPL loop with slash-command routing."""
    from src.commands import handle_command, do_resume

    _mode_names = {"plan": "PLAN", "ask": "ASK", "auto": "AUTO"}

    def _prompt() -> str:
        m = permission.mode.value
        label = f"{_mode_names.get(m, m)} >"
        if m == "plan":
            return f"  {term._YELLOW}{_mode_names.get(m, m)}{term._RESET} {term._BOLD_GREEN}>{term._RESET}  "
        if m == "auto":
            return f"  {term._GREEN}{_mode_names.get(m, m)}{term._RESET} {term._BOLD_GREEN}>{term._RESET}  "
        return f"  {term._CYAN}{_mode_names.get(m, m)}{term._RESET} {term._BOLD_GREEN}>{term._RESET}  "

    if log_dir is None:
        log_dir = Path.cwd() / ".sessions"
    if not workspace:
        workspace = str(Path.cwd())

    first = True
    while True:
        if not first:
            print(term.hr(), flush=True)
        first = False
        try:
            line = term.readline(_prompt())
        except (EOFError, KeyboardInterrupt):
            print()
            break
        print()

        stripped = line.strip()
        if not stripped:
            continue

        # Slash commands
        if stripped.startswith("/"):
            parts = stripped[1:].split(maxsplit=1)
            cmd_name = parts[0].lower()
            cmd_args = parts[1] if len(parts) > 1 else ""

            if cmd_name in ("exit", "quit"):
                break

            if cmd_name == "resume":
                resume_arg = True if not cmd_args else cmd_args
                do_resume(engine, log_dir, workspace, resume_arg)
                continue

            if handle_command(
                cmd_name, cmd_args,
                engine=engine,
                registry=registry,
                permission=permission,
                log_dir=log_dir,
                workspace=workspace,
                session_store=session_store,
            ):
                continue

            print(term.info(f"Unknown command: /{cmd_name}. Type /help or see available commands."))
            continue

        # Normal query → submit to engine (NEVER exit on error)
        try:
            engine.run(stripped)
        except KeyboardInterrupt:
            print()
            continue
        except Exception as e:
            # Catch ALL errors — the REPL must NEVER die
            print()
            print(term.error(f"Engine error (recovered): {e}"))
            print(term.info("The agent is still alive. You can continue."))
            continue

    # Clean up the input box on exit (only reached via /exit, /quit, or Ctrl+C twice)
    print("\033[2K\033[1A\033[2K\033[1A\033[2K", end="")
    sys.stdout.flush()
