"""Mini Claude Code — entry point."""
import argparse
from pathlib import Path

from src.app import run
from src.config import PROVIDER


def main() -> None:
    parser = argparse.ArgumentParser(description="Mini Claude Code")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--log-dir", type=Path, default=Path.cwd() / ".sessions")
    parser.add_argument("--mode", type=str, choices=["plan", "ask", "auto"], default="ask",
                        help="Permission mode (default: ask)")
    parser.add_argument("--provider", type=str, choices=["anthropic", "deepseek"], default=PROVIDER,
                        help=f"LLM provider (default: {PROVIDER})")
    parser.add_argument("--model", type=str, default=None,
                        help="Model name override")
    parser.add_argument("--api-key", type=str, default=None,
                        help="API key (env var by default)")
    parser.add_argument("--api-base", type=str, default=None,
                        help="API base URL override (DeepSeek only)")
    parser.add_argument("--resume", nargs="?", const=True, default=False,
                        help="Resume from a previous session")
    parser.add_argument("--max-rounds", type=int, default=None,
                        help="Max tool-call rounds per turn (default: 20)")
    parser.add_argument("--no-color", action="store_true", default=False,
                        help="Disable ANSI color output")
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
