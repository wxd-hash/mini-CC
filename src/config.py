"""Global configuration — overridable at startup via CLI args."""

from pathlib import Path

WORKSPACE_DIR: Path = Path.cwd() / "workspace"

# LLM
PROVIDER: str = "anthropic"              # "anthropic" or "deepseek"
MODEL_ANTHROPIC: str = "claude-sonnet-4-5"
MODEL_DEEPSEEK: str = "deepseek-chat"
DEEPSEEK_API_BASE: str = "https://api.deepseek.com/v1"

# Agent loop
MAX_TOOL_ROUNDS: int = 10

# Context compaction
MAX_MESSAGES_BEFORE_COMPACT: int = 30
KEEP_RECENT_MESSAGES: int = 10
