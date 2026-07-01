"""Terminal REPL — banner, prompt, input loop, slash-command routing."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from src import terminal as term
from src.agent.loop import AbortedError
from src.security.permission import PermissionChecker
from src.tools.registry import ToolRegistry


# Double-press timeout (matches claude-code's DOUBLE_PRESS_TIMEOUT_MS = 800)
DOUBLE_PRESS_SECONDS = 0.8


def print_banner(provider, session_store, workspace, permission) -> None:
    cat = [
        r"    /\_/\    ",
        r"   ( o.o )   ",
        r"    > ^ <    ",
        r"   meow~     ",
    ]
    cat_w = max(len(c) for c in cat)

    hr_fixed = term.hr_fixed(50)
    info = [
        term.bold("Mini Claude Code"),
        hr_fixed,
        term.banner_line("Provider", f"{provider.provider_name}  ({provider.model})"),
        term.banner_line("Session", getattr(session_store, 'session_id', '?')[:16]
                         if hasattr(session_store, 'session_id') else
                         getattr(session_store, 'path', '?').name[:16]
                         if hasattr(session_store, 'path') else '?'),
        term.banner_line("Workspace", str(workspace.root)),
        term.banner_line("Mode", permission.mode.value),
        hr_fixed,
        term.info("/tools  /perm  /skills  /init  /history  /clear  /reload  /exit"),
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


def run_repl(
    registry: ToolRegistry,
    permission: PermissionChecker,
    engine: Any,
    log_dir: Path | None = None,
    workspace: str = "",
    session_store: Any = None,
) -> None:
    """Interactive REPL. Double-press Ctrl+C to exit (matches claude-code).

    Flow:
      - Ctrl+C once during engine.run() → cancel turn, back to prompt
      - Ctrl+C once at empty prompt → "Press again to exit"
      - Ctrl+C twice quickly → exit
    """
    from src.commands import handle_command, do_resume

    _mode_names = {"plan": "PLAN", "ask": "ASK", "auto": "AUTO"}
    _mode_colors = {
        "plan": term._YELLOW,
        "ask": term._CYAN,
        "auto": term._GREEN,
    }

    def _prompt() -> str:
        m = permission.mode.value
        label = _mode_names.get(m, m)
        color = _mode_colors.get(m, "")
        return f"  {color}{label}{term._RESET} {term._BOLD_GREEN}>{term._RESET}  "

    if log_dir is None:
        log_dir = Path.cwd() / ".sessions"
    if not workspace:
        workspace = str(Path.cwd())

    first = True
    last_ctrlc = 0.0

    while True:
        try:
            if not first:
                print(term.hr(), flush=True)
            first = False

            # Read input (Ctrl+C here triggers double-press exit)
            try:
                line = term.readline(_prompt())
            except (EOFError, KeyboardInterrupt):
                now = time.monotonic()
                if now - last_ctrlc <= DOUBLE_PRESS_SECONDS:
                    print("\nGoodbye.")
                    sys.exit(0)
                last_ctrlc = now
                sys.stdout.write(f"\n{term._YELLOW}Press Ctrl+C again to exit{term._RESET}\n")
                sys.stdout.flush()
                first = True
                deadline = time.monotonic() + DOUBLE_PRESS_SECONDS
                while time.monotonic() < deadline:
                    try:
                        ch = term._getch()
                    except KeyboardInterrupt:
                        sys.stdout.write("Goodbye.\n")
                        sys.exit(0)
                    if ch == "\x03":
                        sys.stdout.write("Goodbye.\n")
                        sys.exit(0)
                    # Any other key pressed → cancel, erase warning
                    break
                # Erase warning line and the blank line after it
                sys.stdout.write("\033[A\033[K\033[A\033[K")
                sys.stdout.flush()
                last_ctrlc = 0.0
                continue

            last_ctrlc = 0.0
            stripped = line.strip()
            if not stripped:
                continue

            # Trace: user_input
            if getattr(engine, '_trace', None):
                engine._trace.user_input(stripped, is_command=stripped.startswith("/"))

            # Slash commands — handle /exit BEFORE handle_command
            if stripped.startswith("/"):
                parts = stripped[1:].split(maxsplit=1)
                cmd_name = parts[0].lower()
                cmd_args = parts[1] if len(parts) > 1 else ""

                if cmd_name in ("exit", "quit"):
                    sys.exit(0)

                if cmd_name == "resume":
                    do_resume(engine, log_dir, workspace,
                              True if not cmd_args else cmd_args)
                    continue

                if handle_command(
                    cmd_name, cmd_args,
                    engine=engine, registry=registry, permission=permission,
                    log_dir=log_dir, workspace=workspace, session_store=session_store,
                ):
                    continue

                print(term.info(f"Unknown command: /{cmd_name}"))
                continue

            # Normal query — visual separator between input and response
            print(term.hr())
            engine.run(stripped)
        except (KeyboardInterrupt, AbortedError):
            print()
            print(term.info("Turn cancelled."))
            continue
        except EOFError:
            sys.exit(0)
        except Exception:
            import traceback
            print()
            traceback.print_exc()
            print()
            print(term.error("Engine crashed — but I'm still alive. Try again."))
            continue

    # Clean up on exit
    print("\033[2K\033[1A\033[2K\033[1A\033[2K", end="")
    sys.stdout.flush()
