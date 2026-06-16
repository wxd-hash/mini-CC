"""Git-related tools — read-only operations only."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from src.tools.base import Tool


class GitDiff(Tool):
    """Show git diff of working-tree or staged changes."""

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
        return "Show git diff of unstaged or staged changes (read-only)"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path relative to workspace (default '.')",
                },
                "staged": {
                    "type": "boolean",
                    "description": "Show staged changes instead of unstaged (default false)",
                },
            },
            "required": [],
        }

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def run(self, args: dict[str, Any]) -> str:
        target = args.get("path", ".")
        staged = args.get("staged", False)

        try:
            subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                capture_output=True, text=True,
                cwd=str(self._ws), check=True, timeout=10,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            return "Not a git repository. Initialize one with: git init"

        cmd = ["git", "diff"]
        if staged:
            cmd.append("--staged")
        cmd += ["--", target]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                cwd=str(self._ws), timeout=30,
            )
        except subprocess.TimeoutExpired:
            return "Error: git diff timed out"
        except FileNotFoundError:
            return "Error: git is not installed or not on PATH"

        output = result.stdout.rstrip()
        label = "staged" if staged else "working tree"
        if not output:
            return f"No changes ({label} clean)"

        if result.stderr:
            output += f"\n\n[stderr]\n{result.stderr.rstrip()}"

        if len(output) > self.MAX_OUTPUT:
            output = (
                output[: self.MAX_OUTPUT]
                + f"\n\n... [truncated at {self.MAX_OUTPUT} chars]"
            )

        return output
