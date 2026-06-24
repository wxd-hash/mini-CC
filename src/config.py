"""Configuration system — matches cc-mini's multi-source (CLI > env > TOML) pattern.

Uses dataclass AppConfig for typed config, TOML file loading, env var
support, and model alias resolution.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Provider defaults (kept from original project)
# ---------------------------------------------------------------------------

DEFAULT_PROVIDER = "deepseek"
PROVIDER = DEFAULT_PROVIDER  # legacy compat

# Anthropic models
MODEL_ANTHROPIC = "claude-sonnet-4-5"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"

# OpenAI-compatible (DeepSeek)
MODEL_DEEPSEEK = "deepseek-chat"
DEEPSEEK_API_BASE = "https://api.deepseek.com/v1"
DEFAULT_OPENAI_MODEL = "gpt-5.1-codex"

# Agent loop
MAX_TOOL_ROUNDS: int = 60  # warn at 60, force-stop at 300
MAX_MESSAGES_BEFORE_COMPACT: int = 50  # legacy, now uses token-based heuristic
KEEP_RECENT_MESSAGES: int = 20

# Project memory (old paths kept for compat)
MEMORY_MAX_CHARS: int = 6000
WORKSPACE_DIR: Path = Path.cwd()

# ---------------------------------------------------------------------------
# Model aliases (matches cc-mini)
# ---------------------------------------------------------------------------

_MODEL_ALIASES: dict[str, str] = {
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-6",
    "haiku": "claude-haiku-4-5-20251001",
    "best": "claude-opus-4-6",
    "sonnet35": "claude-3-5-sonnet-20241022",
    "sonnet37": "claude-3-7-sonnet-20250219",
    "haiku35": "claude-3-5-haiku-20241022",
    "sonnet45": "claude-sonnet-4-5-20250929",
    "opus45": "claude-opus-4-5-20251101",
    "claude-opus-4.6": "claude-opus-4-6",
    "claude-opus-4.5": "claude-opus-4-5",
    "claude-opus-4.1": "claude-opus-4-1",
    "claude-opus-4": "claude-opus-4",
    "claude-sonnet-4.6": "claude-sonnet-4-6",
    "claude-sonnet-4.5": "claude-sonnet-4-5",
    "claude-sonnet-4": "claude-sonnet-4",
    "claude-3.7-sonnet": "claude-3-7-sonnet",
    "claude-3.5-sonnet": "claude-3-5-sonnet",
    "claude-3.5-haiku": "claude-3-5-haiku",
    "claude-3-haiku": "claude-3-haiku",
}

# First prefix match wins for max_tokens lookup
_MODEL_MAX_TOKENS: tuple[tuple[str, int], ...] = (
    ("claude-opus-4-6", 64000),
    ("claude-sonnet-4-6", 32000),
    ("claude-opus-4-5", 32000),
    ("claude-sonnet-4-5", 32000),
    ("claude-sonnet-4", 32000),
    ("claude-haiku-4", 32000),
    ("claude-opus-4-1", 32000),
    ("claude-opus-4", 32000),
    ("claude-3-7-sonnet", 32000),
    ("claude-3-5-sonnet", 8192),
    ("claude-3-5-haiku", 8192),
    ("claude-3-haiku", 4096),
)

_OPENAI_MAX_TOKENS: tuple[tuple[str, int], ...] = (
    ("gpt-5", 8192),
    ("gpt-4.1", 16384),
    ("gpt-4o", 16384),
    ("o1", 32768),
    ("o3", 32768),
    ("o4", 32768),
)

# ---------------------------------------------------------------------------
# Env var names
# ---------------------------------------------------------------------------

_ENV_MODEL = "MINICLAUDE_MODEL"
_ENV_MAX_TOKENS = "MINICLAUDE_MAX_TOKENS"
_ENV_PROVIDER = "MINICLAUDE_PROVIDER"
_ENV_MEMORY_DIR = "MINICLAUDE_MEMORY_DIR"

# ---------------------------------------------------------------------------
# Config file paths
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG_PATHS = (
    Path.home() / ".config" / "mini-claude" / "config.toml",
    Path.cwd() / ".mini-claude.toml",
)


# ---------------------------------------------------------------------------
# AppConfig
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AppConfig:
    """All resolved configuration in one immutable object — matches cc-mini."""
    provider: str
    api_key: str | None
    base_url: str | None
    model: str
    max_tokens: int
    max_rounds: int = MAX_TOOL_ROUNDS
    memory_dir: Path = Path.home() / ".config" / "mini-claude" / "memory"
    dream_interval_hours: float = 24.0
    dream_min_sessions: int = 5
    auto_dream: bool = True
    config_paths: tuple[Path, ...] = ()


# ---------------------------------------------------------------------------
# Model resolution
# ---------------------------------------------------------------------------

def resolve_model(model: str | None, provider: str = DEFAULT_PROVIDER) -> str:
    """Resolve model aliases to canonical names. Returns default if None."""
    provider = provider.lower()
    if not model:
        return default_model_for_provider(provider)
    normalized = model.strip()
    if provider != "anthropic":
        return normalized
    return _MODEL_ALIASES.get(normalized, normalized)


def default_model_for_provider(provider: str) -> str:
    """Return the default model name for a given provider."""
    provider = provider.lower()
    if provider == "anthropic":
        return DEFAULT_ANTHROPIC_MODEL
    if provider == "openai":
        return DEFAULT_OPENAI_MODEL
    # deepseek
    return MODEL_DEEPSEEK


def default_max_tokens_for_model(
    model: str | None,
    provider: str = DEFAULT_PROVIDER,
) -> int:
    """Look up the default max_tokens for a model by prefix match."""
    provider = provider.lower()
    resolved = resolve_model(model, provider=provider)
    if provider in ("openai", "deepseek"):
        for prefix, limit in _OPENAI_MAX_TOKENS:
            if resolved.startswith(prefix):
                return limit
        return 8192

    for prefix, limit in _MODEL_MAX_TOKENS:
        if resolved.startswith(prefix):
            return limit
    return 32000


# ---------------------------------------------------------------------------
# Main config loader
# ---------------------------------------------------------------------------

def load_app_config(args: Any) -> AppConfig:
    """Load configuration from CLI args, env vars, and TOML files.

    Priority: CLI args > env vars > TOML file > defaults.
    """
    file_values, config_paths = _load_file_values(
        getattr(args, "config", None)
    )
    env_values = _load_env_values()

    # Resolve provider
    raw_provider = (
        getattr(args, "provider", None)
        or env_values.get("provider")
        or file_values.get("provider")
        or DEFAULT_PROVIDER
    )
    provider = raw_provider.lower()

    # Gather per-provider values
    selected_provider_values = file_values.get("providers", {}).get(provider, {})
    selected_env_values = _provider_env_values(env_values, provider)

    def _file_value(key: str) -> Any:
        val = file_values.get(key)
        if val is not None:
            return val
        return selected_provider_values.get(key)

    # Model
    raw_model = (
        getattr(args, "model", None)
        or env_values.get("model")
        or _file_value("model")
    )
    model = resolve_model(raw_model, provider=provider)

    # Max tokens
    raw_max_tokens = (
        getattr(args, "max_tokens", None)
        or env_values.get("max_tokens")
        or _file_value("max_tokens")
    )
    max_tokens = _parse_max_tokens(
        raw_max_tokens,
        default=default_max_tokens_for_model(model, provider=provider),
    )

    # API key / base url
    api_key = (
        getattr(args, "api_key", None)
        or selected_env_values.get("api_key")
        or _file_value("api_key")
    )
    base_url = (
        getattr(args, "api_base", None)
        or selected_env_values.get("base_url")
        or _file_value("base_url")
    )

    # Max rounds
    max_rounds = getattr(args, "max_rounds", None) or MAX_TOOL_ROUNDS

    # Memory dir
    raw_memory_dir = (
        getattr(args, "memory_dir", None)
        or env_values.get("memory_dir")
        or _file_value("memory_dir")
    )
    memory_dir = Path(raw_memory_dir).expanduser() if raw_memory_dir else Path.home() / ".config" / "mini-claude" / "memory"

    return AppConfig(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        model=model,
        max_tokens=max_tokens,
        max_rounds=int(max_rounds) if max_rounds else MAX_TOOL_ROUNDS,
        memory_dir=memory_dir,
        config_paths=config_paths,
    )


# ---------------------------------------------------------------------------
# Internal: file loading
# ---------------------------------------------------------------------------

def _load_file_values(explicit_path: str | None) -> tuple[dict[str, Any], tuple[Path, ...]]:
    values: dict[str, Any] = {"providers": {}}
    loaded_paths: list[Path] = []

    if explicit_path:
        path = Path(explicit_path).expanduser()
        if not path.exists():
            raise ValueError(f"Config file not found: {path}")
        _merge_file_values(values, _read_config_file(path))
        loaded_paths.append(path)
        return values, tuple(loaded_paths)

    for path in _DEFAULT_CONFIG_PATHS:
        if not path.exists():
            continue
        _merge_file_values(values, _read_config_file(path))
        loaded_paths.append(path)

    return values, tuple(loaded_paths)


def _read_config_file(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except Exception as exc:
        raise ValueError(f"Invalid TOML in config file {path}: {exc}") from exc

    result: dict[str, Any] = {"providers": {}}
    for provider in ("anthropic", "openai", "deepseek"):
        section = data.get(provider, {})
        if isinstance(section, dict):
            result["providers"][provider] = dict(section)

    for key in (
        "provider", "api_key", "base_url", "model", "max_tokens",
        "max_rounds", "memory_dir",
    ):
        if key in data:
            result[key] = data[key]

    return result


def _merge_file_values(target: dict[str, Any], incoming: dict[str, Any]) -> None:
    for key, val in incoming.items():
        if key == "providers":
            target.setdefault("providers", {})
            for p, pv in val.items():
                target["providers"].setdefault(p, {}).update(pv)
        else:
            target[key] = val


# ---------------------------------------------------------------------------
# Internal: env loading
# ---------------------------------------------------------------------------

def _load_env_values() -> dict[str, Any]:
    values: dict[str, Any] = {}
    if os.getenv(_ENV_PROVIDER):
        values["provider"] = os.environ[_ENV_PROVIDER]
    if os.getenv("ANTHROPIC_API_KEY"):
        values.setdefault("anthropic_api_key", os.environ["ANTHROPIC_API_KEY"])
    if os.getenv("ANTHROPIC_BASE_URL"):
        values.setdefault("anthropic_base_url", os.environ["ANTHROPIC_BASE_URL"])
    if os.getenv("OPENAI_API_KEY"):
        values.setdefault("openai_api_key", os.environ["OPENAI_API_KEY"])
    if os.getenv("OPENAI_BASE_URL"):
        values.setdefault("openai_base_url", os.environ["OPENAI_BASE_URL"])
    if os.getenv("DEEPSEEK_API_KEY"):
        values.setdefault("deepseek_api_key", os.environ["DEEPSEEK_API_KEY"])
    if os.getenv(_ENV_MODEL):
        values["model"] = os.environ[_ENV_MODEL]
    if os.getenv(_ENV_MAX_TOKENS):
        values["max_tokens"] = os.environ[_ENV_MAX_TOKENS]
    if os.getenv(_ENV_MEMORY_DIR):
        values["memory_dir"] = os.environ[_ENV_MEMORY_DIR]
    return values


def _provider_env_values(env_values: dict[str, Any], provider: str) -> dict[str, Any]:
    provider = provider.lower()
    if provider == "anthropic":
        return {
            "api_key": env_values.get("anthropic_api_key"),
            "base_url": env_values.get("anthropic_base_url"),
        }
    if provider == "deepseek":
        return {
            "api_key": env_values.get("deepseek_api_key"),
            "base_url": env_values.get("deepseek_base_url", DEEPSEEK_API_BASE),
        }
    return {
        "api_key": env_values.get("openai_api_key"),
        "base_url": env_values.get("openai_base_url"),
    }


def _parse_max_tokens(raw_value: Any, default: int) -> int:
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid max_tokens value: {raw_value!r}") from exc
    if value <= 0:
        raise ValueError("max_tokens must be a positive integer")
    return value
