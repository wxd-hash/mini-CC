"""Git-related tools — read-only operations only."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from src.tools.base import Tool, ToolResult


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
        return "显示 git 工作区或暂存区的差异（只读）"

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

    # -- cc-mini protocol --------------------------------------------------

    def is_read_only(self) -> bool:
        return True

    def get_activity_description(self, **kwargs: Any) -> str | None:
        staged = kwargs.get("staged", False)
        path = kwargs.get("path", ".")
        label = "staged" if staged else "working tree"
        return f"Git diff ({label})"

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute(self, **kwargs: Any) -> ToolResult:
        target = kwargs.get("path", ".")
        staged = kwargs.get("staged", False)

        try:
            subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                capture_output=True, text=True,
                cwd=str(self._ws), check=True, timeout=10,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            return ToolResult(content="Not a git repository. Initialize one with: git init")

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
            return ToolResult(content="Error: git diff timed out", is_error=True)
        except FileNotFoundError:
            return ToolResult(content="Error: git is not installed or not on PATH", is_error=True)

        output = result.stdout.rstrip()
        label = "staged" if staged else "working tree"
        if not output:
            return ToolResult(content=f"No changes ({label} clean)")

        if result.stderr:
            output += f"\n\n[stderr]\n{result.stderr.rstrip()}"

        if len(output) > self.MAX_OUTPUT:
            output = (
                output[: self.MAX_OUTPUT]
                + f"\n\n... [truncated at {self.MAX_OUTPUT} chars]"
            )

        return ToolResult(content=output)
