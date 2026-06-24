"""Application wiring — assemble config, provider, tools, engine, and launch REPL.

Matches cc-mini's bootstrap pattern while keeping the original project's
LLM provider abstraction and entry flow.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from src import terminal as term
from src.agent.loop import Engine
from src.commands import do_resume, handle_command
from src.config import (
    AppConfig,
    load_app_config,
    DEEPSEEK_API_BASE,
    MAX_TOOL_ROUNDS,
    resolve_model,
    default_max_tokens_for_model,
)
from src.llm.anthropic_provider import AnthropicProvider
from src.llm.openai_provider import OpenAIProvider
from src.repl import print_banner, run_repl
from src.security.permission import PermissionChecker, Mode
from src.session.logger import SessionLogger, SessionStore, cleanup_empty
from src.tools.file_tools import (
    ReadFile, WriteFile, ListFiles, SearchFiles, FileEditTool,
    self_test as sandbox_self_test,
)
from src.tools.git_tools import GitDiff
from src.tools.registry import ToolRegistry
from src.tools.shell_tool import RunShell
from src.workspace.sandbox import WorkspaceSandbox


def run(args) -> None:
    """Bootstrap the application from parsed CLI args and enter the REPL."""
    sandbox_self_test()

    # -- Load config (matches cc-mini's load_app_config) --------------------
    try:
        app_config = load_app_config(args)
    except ValueError as exc:
        print(term.error(f"Config error: {exc}"))
        return

    # -- Create provider (keep original pattern) ----------------------------
    provider = _make_provider(args, app_config)
    workspace = WorkspaceSandbox(args.workspace)

    # -- Permissions (matches cc-mini's PermissionChecker) ------------------
    permission = PermissionChecker(auto_approve=(args.mode == "auto"))

    # -- Session store (new cc-mini pattern) --------------------------------
    session_store = SessionStore(
        cwd=str(workspace.root),
        model=app_config.model,
        mode=args.mode,
        sessions_dir=args.log_dir,
    )

    # -- Legacy logger (keep for backward compat) ---------------------------
    logger = _make_logger(args.log_dir, str(workspace.root))

    # -- Tool registry ------------------------------------------------------
    registry = ToolRegistry()
    registry.register(ReadFile(workspace.root))
    registry.register(WriteFile(workspace.root))
    registry.register(FileEditTool(workspace.root))
    registry.register(ListFiles(workspace.root))
    registry.register(SearchFiles(workspace.root))
    registry.register(GitDiff(workspace.root))
    registry.register(RunShell(workspace.root))

    # -- Skills (new feature) -----------------------------------------------
    from src.features.skills_bundled import register_bundled_skills
    from src.features.skills import discover_skills, build_skills_prompt_section
    register_bundled_skills()
    discover_skills(str(workspace.root))

    # -- Memory (new feature) -----------------------------------------------
    from src.features.memory import ensure_memory_dir
    memory_dir = app_config.memory_dir
    ensure_memory_dir(memory_dir)

    # -- Plan mode manager (new feature) ------------------------------------
    from src.features.plan import PlanModeManager
    plan_manager = PlanModeManager()
    permission.set_plan_manager(plan_manager)

    # -- Cost tracker (new feature) -----------------------------------------
    from src.features.cost_tracker import CostTracker
    cost_tracker = CostTracker()

    # -- Build system prompt ------------------------------------------------
    from src.context import build_system_prompt
    system_prompt = build_system_prompt(
        cwd=str(workspace.root),
        model=app_config.model,
        memory_dir=memory_dir,
    )

    # Add skills section
    skills_section = build_skills_prompt_section()
    if skills_section:
        system_prompt += "\n\n" + skills_section

    # -- Build Engine (matches cc-mini's Engine init) -----------------------
    tools_list = [
        ReadFile(workspace.root),
        WriteFile(workspace.root),
        FileEditTool(workspace.root),
        ListFiles(workspace.root),
        SearchFiles(workspace.root),
        GitDiff(workspace.root),
        RunShell(workspace.root),
    ]

    engine = Engine(
        tools=tools_list,
        system_prompt=system_prompt,
        permission_checker=permission,
        provider=provider,
        model=app_config.model,
        max_tokens=app_config.max_tokens,
        session_store=session_store,
        cost_tracker=cost_tracker,
        tool_registry=registry,
        workspace_dir=workspace.root,
        logger=logger,
        memory_dir=memory_dir,
        provider_factory=lambda: _make_provider(args, app_config),
    )

    plan_manager.bind_engine(engine)
    plan_manager.set_permissions(permission)

    # -- Handle resume ------------------------------------------------------
    resumed = args.resume is not False
    if resumed:
        do_resume(engine, args.log_dir, str(workspace.root), args.resume)

    if not resumed:
        print_banner(provider, session_store, workspace, permission)

    # -- Enter REPL ---------------------------------------------------------
    run_repl(
        registry=registry,
        permission=permission,
        engine=engine,
        log_dir=args.log_dir,
        workspace=str(workspace.root),
        session_store=session_store,
    )

    # -- Cleanup ------------------------------------------------------------
    logger.close()
    session_store.close()
    cleanup_empty(logger.path)

    # Print cost summary
    if cost_tracker.total_cost_usd > 0:
        print(f"\n{term.info(cost_tracker.format_cost())}")


# ---------------------------------------------------------------------------
# Internal helpers (kept from original project)
# ---------------------------------------------------------------------------

def _make_provider(args, app_config: AppConfig):
    """Create LLM provider from args + resolved config."""
    if args.provider == "deepseek":
        model = app_config.model
        api_key = app_config.api_key or os.environ.get("DEEPSEEK_API_KEY")
        base_url = app_config.base_url or DEEPSEEK_API_BASE
        return OpenAIProvider(model=model, api_key=api_key, base_url=base_url)

    # Anthropic
    model = app_config.model
    if app_config.api_key:
        os.environ["ANTHROPIC_API_KEY"] = app_config.api_key
    return AnthropicProvider(model=model)


def _make_logger(log_dir: Path, workspace: str = "") -> SessionLogger:
    from src.session.logger import _workspace_dir_name
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ws_dir = log_dir / (_workspace_dir_name(workspace) if workspace else "_default")
    ws_dir.mkdir(parents=True, exist_ok=True)
    return SessionLogger(ws_dir / f"session-{ts}.jsonl", workspace=workspace)
