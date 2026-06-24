"""Mini Claude Code — entry point.

Matches cc-mini's CLI interface with --provider, --model, --max-tokens, etc.
"""

import argparse
from pathlib import Path

from src.app import run
from src.config import PROVIDER, MAX_TOOL_ROUNDS


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mini Claude Code — a minimal AI coding agent"
    )
    parser.add_argument("--workspace", type=Path, default=Path.cwd(),
                        help="Workspace root directory")
    parser.add_argument("--log-dir", type=Path, default=Path.cwd() / ".sessions",
                        help="Session log directory")
    parser.add_argument("--mode", type=str, choices=["plan", "ask", "auto"], default="ask",
                        help="Permission mode (default: ask)")
    parser.add_argument("--provider", type=str, choices=["anthropic", "deepseek"],
                        default=PROVIDER,
                        help=f"LLM provider (default: {PROVIDER})")
    parser.add_argument("--model", type=str, default=None,
                        help="Model name override (supports aliases: sonnet, opus, haiku)")
    parser.add_argument("--api-key", type=str, default=None,
                        help="API key (env var by default)")
    parser.add_argument("--api-base", type=str, default=None,
                        help="API base URL override")
    parser.add_argument("--max-tokens", type=int, default=None,
                        help="Maximum output tokens per model response")
    parser.add_argument("--resume", nargs="?", const=True, default=False,
                        help="Resume from a previous session")
    parser.add_argument("--max-rounds", type=int, default=MAX_TOOL_ROUNDS,
                        help="Max tool-call rounds per turn")
    parser.add_argument("--no-color", action="store_true", default=False,
                        help="Disable ANSI color output")
    parser.add_argument("--config", type=str, default=None,
                        help="Path to a TOML config file")
    parser.add_argument("--memory-dir", type=str, default=None,
                        help="Override memory directory path")
    args = parser.parse_args()

    if args.no_color:
        from src import terminal as term
        term.set_no_color()

    try:
        run(args)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
