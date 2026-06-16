"""Git-related tools — read-only operations only."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from src.tools.base import Tool


class GitDiff(Tool):
    """Show unstaged changes via ``git diff`` inside the workspace."""

    MAX_OUTPUT = 12000

    def __init__(self, workspace_dir: Path) -> None:
        self._ws = workspace_dir.resolve()

    # ------------------------------------------------------------------
    # Tool metadata
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "git_diff"

    @property
    def description(self) -> str:
        return "Show git diff of unstaged changes within the workspace (read-only)"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path relative to workspace (default '.')",
                },
            },
            "required": [],
        }

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def run(self, args: dict[str, Any]) -> str:
        target = args.get("path", ".")

        # Check if we're in a git repo
        try:
            subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                capture_output=True,
                text=True,
                cwd=str(self._ws),
                check=True,
                timeout=10,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            return (
                "Not a git repository. "
                "Initialize one with: git init"
            )

        # Run git diff
        try:
            result = subprocess.run(
                ["git", "diff", "--", target],
                capture_output=True,
                text=True,
                cwd=str(self._ws),
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            return "Error: git diff timed out"
        except FileNotFoundError:
            return "Error: git is not installed or not on PATH"

        output = result.stdout.rstrip()
        if not output:
            return "No changes (working tree clean)"

        if result.stderr:
            output += f"\n\n[stderr]\n{result.stderr.rstrip()}"

        if len(output) > self.MAX_OUTPUT:
            output = (
                output[: self.MAX_OUTPUT]
                + f"\n\n... [truncated at {self.MAX_OUTPUT} chars]"
            )

        return output
