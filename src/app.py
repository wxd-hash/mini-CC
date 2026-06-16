"""Application wiring — assemble provider, tools, agent, and launch REPL."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from src import terminal as term
from src.agent.loop import MiniClaudeAgent
from src.commands import do_resume
from src.config import MODEL_ANTHROPIC, MODEL_DEEPSEEK, DEEPSEEK_API_BASE
from src.llm.anthropic_provider import AnthropicProvider
from src.llm.openai_provider import OpenAIProvider
from src.repl import print_banner, run_repl
from src.security.permission import PermissionManager, Mode
from src.session.logger import SessionLogger, cleanup_empty
from src.tools.file_tools import ReadFile, WriteFile, ListFiles, SearchFiles, self_test as sandbox_self_test
from src.tools.git_tools import GitDiff
from src.tools.registry import ToolRegistry
from src.tools.shell_tool import RunShell
from src.workspace.sandbox import WorkspaceSandbox


def run(args) -> None:
    """Bootstrap the application from parsed CLI args and enter the REPL."""
    sandbox_self_test()

    provider = _make_provider(args)
    workspace = WorkspaceSandbox(args.workspace)
    permission = PermissionManager(Mode(args.mode))
    logger = _make_logger(args.log_dir, str(workspace.root))

    registry = ToolRegistry()
    registry.register(ReadFile(workspace.root))
    registry.register(WriteFile(workspace.root))
    registry.register(ListFiles(workspace.root))
    registry.register(SearchFiles(workspace.root))
    registry.register(GitDiff(workspace.root))
    registry.register(RunShell(workspace.root))

    agent = MiniClaudeAgent(
        tool_registry=registry,
        permission=permission,
        logger=logger,
        workspace_dir=workspace.root,
        provider=provider,
    )

    resumed = args.resume is not False
    if resumed:
        do_resume(agent, args.log_dir, str(workspace.root), args.resume)

    if not resumed:
        print_banner(provider, logger, workspace, permission)

    run_repl(registry, permission, agent)

    logger.close()
    cleanup_empty(logger.path)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _make_provider(args) -> object:
    if args.provider == "deepseek":
        model = args.model or MODEL_DEEPSEEK
        api_key = args.api_key or os.environ.get("DEEPSEEK_API_KEY")
        base_url = args.api_base or DEEPSEEK_API_BASE
        return OpenAIProvider(model=model, api_key=api_key, base_url=base_url)

    model = args.model or MODEL_ANTHROPIC
    if args.api_key:
        os.environ["ANTHROPIC_API_KEY"] = args.api_key
    return AnthropicProvider(model=model)


def _make_logger(log_dir: Path, workspace: str = "") -> SessionLogger:
    from src.session.logger import _workspace_dir_name
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ws_dir = log_dir / (_workspace_dir_name(workspace) if workspace else "_default")
    ws_dir.mkdir(parents=True, exist_ok=True)
    return SessionLogger(ws_dir / f"session-{ts}.jsonl", workspace=workspace)
