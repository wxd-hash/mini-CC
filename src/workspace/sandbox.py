from __future__ import annotations

from pathlib import Path

from src.tools.file_tools import safe_path


class WorkspaceSandbox:
    """Sandbox that restricts file access to a workspace root directory."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        if not self.root.exists():
            self.root.mkdir(parents=True)

    def resolve(self, path: str) -> Path:
        """Resolve a path within the workspace. Raises on escape attempts."""
        return safe_path(path, workspace_dir=self.root)

    def relpath(self, absolute: Path) -> str:
        """Return a path relative to the workspace root."""
        return str(absolute.relative_to(self.root))
