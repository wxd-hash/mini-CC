"""Terminal REPL — banner, prompt, input loop, slash-command routing."""

from __future__ import annotations

import sys

from src import terminal as term
from src.agent.loop import MiniClaudeAgent
from src.commands import handle_perm, handle_tools, handle_tool
from src.security.permission import PermissionManager
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
        term.banner_line("Session", logger.path.name),
        term.banner_line("Workspace", str(workspace.root)),
        term.banner_line("Mode", permission.mode.value),
        hr_fixed,
        term.info("/tools  /tool  /perm  /clear  /reload  /exit"),
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
    permission: PermissionManager,
    agent: MiniClaudeAgent,
) -> None:
    _mode_names = {"plan": "PLAN", "ask": "ASK", "auto": "AUTO"}

    def _prompt() -> str:
        m = permission.mode.value
        label = f"{_mode_names.get(m, m)} >"
        if m == "plan":
            return f"  {term._YELLOW}{_mode_names.get(m, m)}{term._RESET} {term._BOLD_GREEN}>{term._RESET}  "
        if m == "auto":
            return f"  {term._GREEN}{_mode_names.get(m, m)}{term._RESET} {term._BOLD_GREEN}>{term._RESET}  "
        return f"  {term._CYAN}{_mode_names.get(m, m)}{term._RESET} {term._BOLD_GREEN}>{term._RESET}  "

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

        if stripped == "/exit":
            break

        if stripped == "/tools":
            handle_tools(registry)
            continue

        if stripped.startswith("/perm "):
            handle_perm(permission, stripped[6:])
            continue

        if stripped.startswith("/tool "):
            handle_tool(registry, permission, stripped[6:])
            continue

        if stripped == "/clear":
            agent.clear()
            continue

        if stripped == "/reload":
            agent.reload()
            continue

        try:
            agent.run(stripped)
        except KeyboardInterrupt:
            print()
            continue

    # Clean up the input box on exit
    print("\033[2K\033[1A\033[2K\033[1A\033[2K", end="")
    sys.stdout.flush()
