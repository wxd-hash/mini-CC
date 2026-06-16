"""Mini Claude Code — entry point."""
import argparse
import json
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

from src import terminal as term
from src.agent.loop import MiniClaudeAgent
from src.config import (
    PROVIDER,
    MODEL_ANTHROPIC,
    MODEL_DEEPSEEK,
    DEEPSEEK_API_BASE,
)
from src.llm.anthropic_provider import AnthropicProvider
from src.llm.openai_provider import OpenAIProvider
from src.tools.file_tools import ReadFile, WriteFile, ListFiles, SearchFiles, self_test as sandbox_self_test
from src.tools.registry import ToolRegistry
from src.tools.shell_tool import RunShell
from src.tools.git_tools import GitDiff
from src.workspace.sandbox import WorkspaceSandbox
from src.security.permission import PermissionManager, Mode
from src.session.logger import SessionLogger, list_sessions, load_session_messages, cleanup_empty


def main() -> None:
    parser = argparse.ArgumentParser(description="Mini Claude Code")
    parser.add_argument(
        "--workspace", type=Path, default=Path.cwd() / "workspace",
    )
    parser.add_argument(
        "--log-dir", type=Path, default=Path.cwd() / ".sessions",
    )
    parser.add_argument(
        "--mode",
        type=str,
        choices=["plan", "ask", "auto"],
        default="ask",
        help="Permission mode (default: ask)",
    )
    parser.add_argument(
        "--provider",
        type=str,
        choices=["anthropic", "deepseek"],
        default=PROVIDER,
        help=f"LLM provider (default: {PROVIDER})",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Model name override (provider-specific default if omitted)",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=None,
        help="API key (env ANTHROPIC_API_KEY or DEEPSEEK_API_KEY by default)",
    )
    parser.add_argument(
        "--api-base",
        type=str,
        default=None,
        help="API base URL override (DeepSeek only)",
    )
    parser.add_argument(
        "--resume",
        nargs="?",
        const=True,
        default=False,
        help="Resume from latest session, or specify a path to a .jsonl file",
    )
    args = parser.parse_args()

    # --- Sanity check ---
    sandbox_self_test()

    # --- LLM provider ---
    provider = _make_provider(args)

    # --- Setup ---
    workspace = WorkspaceSandbox(args.workspace)
    permission = PermissionManager(Mode(args.mode))
    logger = _make_logger(args.log_dir, str(workspace.root))

    # --- Tool registry ---
    registry = ToolRegistry()
    registry.register(ReadFile(workspace.root))
    registry.register(WriteFile(workspace.root))
    registry.register(ListFiles(workspace.root))
    registry.register(SearchFiles(workspace.root))
    registry.register(GitDiff(workspace.root))
    registry.register(RunShell(workspace.root))

    # --- Agent ---
    agent = MiniClaudeAgent(
        tool_registry=registry,
        permission=permission,
        logger=logger,
        workspace_dir=workspace.root,
        provider=provider,
    )

    # --- Resume ---
    resumed = args.resume is not False
    if resumed:
        _do_resume(agent, args, str(workspace.root))

    if not resumed:
        _print_banner(provider, logger, workspace, permission)

    _repl(registry, permission, agent)

    logger.close()
    cleanup_empty(logger.path)


# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------

def _make_provider(args: argparse.Namespace):
    if args.provider == "deepseek":
        model = args.model or MODEL_DEEPSEEK
        api_key = args.api_key or os.environ.get("DEEPSEEK_API_KEY")
        base_url = args.api_base or DEEPSEEK_API_BASE
        return OpenAIProvider(model=model, api_key=api_key, base_url=base_url)

    # anthropic (default)
    model = args.model or MODEL_ANTHROPIC
    if args.api_key:
        os.environ["ANTHROPIC_API_KEY"] = args.api_key
    return AnthropicProvider(model=model)


# ---------------------------------------------------------------------------
# Resume logic
# ---------------------------------------------------------------------------

def _do_resume(agent: MiniClaudeAgent, args: argparse.Namespace, workspace: str) -> None:
    session_path: Path | None = None

    if args.resume is True:
        sessions = list_sessions(args.log_dir, workspace)
        if not sessions:
            print(term.info(f"No previous sessions for this workspace."))
            print(term.info("Starting fresh."))
            print()
            return

        if len(sessions) == 1:
            session_path = sessions[0][0]
        else:
            print()
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
        session_path = Path(args.resume)
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

    # Print history to terminal
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

    agent.resume(messages)
    print(term.success(f"Resumed from {session_path.name} ({len(messages)} messages)"))
    print()


# ---------------------------------------------------------------------------
# Startup banner
# ---------------------------------------------------------------------------

_CAT = [
    r"    /\_/\    ",
    r"   ( o.o )   ",
    r"    > ^ <    ",
    r"   meow~     ",
]


def _print_banner(provider, logger, workspace, permission) -> None:
    cat = list(_CAT)
    cat_w = max(len(c) for c in cat)  # visible width of cat art

    # Build info lines (without full-width hr — use fixed width instead)
    hr_fixed = term.hr_fixed(50)
    info = [
        term.bold("Mini Claude Code"),
        hr_fixed,
        term.banner_line("Provider", f"{provider.provider_name}  ({provider.model})"),
        term.banner_line("Session", str(logger.path)),
        term.banner_line("Workspace", str(workspace.root)),
        term.banner_line("Mode", permission.mode.value),
        hr_fixed,
        term.info("/tools  /tool  /perm  /clear  /exit"),
    ]

    # Center cat vertically against info
    while len(cat) < len(info):
        cat.insert(0, "") if len(cat) % 2 == 0 else cat.append("")
    while len(info) < len(cat):
        info.append("")

    # Print banner: top hr, cat+info, bottom hr
    print(term.hr())
    for c, i in zip(cat, info):
        pad = cat_w - len(c) + 3
        print(f"  {c}{' ' * pad}{i}")
    print(term.hr())


# ---------------------------------------------------------------------------
# Interactive REPL
# ---------------------------------------------------------------------------

def _repl(
    registry: ToolRegistry,
    permission: PermissionManager,
    agent: MiniClaudeAgent,
) -> None:
    _mode_names = {"plan": "PLAN", "ask": "ASK", "auto": "AUTO"}
    _mode_colors = {"plan": "\033[33m", "ask": "\033[36m", "auto": "\033[32m"}
    _reset = "\033[0m"

    def _prompt() -> str:
        m = permission.mode.value
        return f"  {_mode_colors.get(m, '')}{_mode_names.get(m, m)} \033[1;37m>\033[0m  "

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
            print(term.hr())
            for name, desc in registry.list_tools():
                print(term.banner_line(name, desc))
            print(term.hr())
            continue

        if stripped.startswith("/perm "):
            _handle_perm(permission, stripped[6:])
            continue

        if stripped.startswith("/tool "):
            _dispatch_tool(registry, permission, stripped[6:])
            continue

        if stripped == "/clear":
            agent.clear()
            continue

        agent.run(stripped)

    # Clean up the input box on exit
    print("\033[2K\033[1A\033[2K\033[1A\033[2K", end="")
    sys.stdout.flush()


def _handle_perm(permission: PermissionManager, arg: str) -> None:
    arg = arg.strip()
    if arg == "status":
        print(term.info(f"Permission mode: {permission.mode.value}"))
        return
    if arg in ("plan", "ask", "auto"):
        permission.mode = Mode(arg)
        print(term.success(f"Permission mode -> {arg}"))
        return
    print(term.info("Usage: /perm <plan|ask|auto|status>"))


# ---------------------------------------------------------------------------
# Tool dispatch (manual /tool debugging)
# ---------------------------------------------------------------------------

def _dispatch_tool(
    registry: ToolRegistry,
    permission: PermissionManager,
    rest: str,
) -> None:
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_logger(log_dir: Path, workspace: str = "") -> SessionLogger:
    from src.session.logger import _workspace_dir_name
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ws_dir = log_dir / (_workspace_dir_name(workspace) if workspace else "_default")
    ws_dir.mkdir(parents=True, exist_ok=True)
    path = ws_dir / f"session-{ts}.jsonl"
    return SessionLogger(path, workspace=workspace)


if __name__ == "__main__":
    main()
