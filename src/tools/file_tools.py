"""File-related tools and path-safety utilities."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.config import WORKSPACE_DIR
from src.tools.base import Tool

_IGNORE_DIRS = frozenset({".git", "__pycache__", "node_modules", ".venv"})


def safe_path(path_str: str, workspace_dir: Path | None = None) -> Path:
    """Resolve *path_str* relative to the workspace directory.

    Rules:
    - Absolute paths are rejected (``/etc/passwd``, ``C:\\...``).
    - ``..`` escapes are rejected.
    - The returned ``Path`` is guaranteed to live under *workspace_dir*.

    Args:
        path_str: Relative path string provided by the user / LLM.
        workspace_dir: Workspace root (defaults to ``config.WORKSPACE_DIR``).

    Returns:
        Resolved absolute ``Path`` inside the workspace.

    Raises:
        ValueError: If the path is absolute or escapes the workspace.
    """
    if workspace_dir is None:
        workspace_dir = WORKSPACE_DIR

    p = Path(path_str)
    if p.is_absolute():
        raise ValueError(f"Absolute paths not allowed: {path_str!r}")

    ws = workspace_dir.resolve()
    resolved = (ws / p).resolve()

    if not resolved.is_relative_to(ws):
        raise ValueError(
            f"Path escapes workspace: {path_str!r} -> {str(resolved)!r}"
        )
    return resolved


# ======================================================================
# File-operation tools
# ======================================================================

class ReadFile(Tool):
    """Read the contents of a workspace file."""

    MAX_CHARS = 12000

    def __init__(self, workspace_dir: Path) -> None:
        self._ws = workspace_dir.resolve()

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "Read contents of a file within the workspace (max 12000 chars)"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to workspace",
                }
            },
            "required": ["path"],
        }

    def run(self, args: dict[str, Any]) -> str:
        path = safe_path(args["path"], self._ws)
        if not path.is_file():
            return f"Error: file not found: {args['path']!r}"
        content = path.read_text(encoding="utf-8")
        if len(content) <= self.MAX_CHARS:
            return content
        return content[: self.MAX_CHARS] + (
            f"\n\n... [truncated at {self.MAX_CHARS} chars, "
            f"total {len(content)} chars]"
        )


class WriteFile(Tool):
    """Write content to a workspace file."""

    def __init__(self, workspace_dir: Path) -> None:
        self._ws = workspace_dir.resolve()

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "Write content to a file within the workspace"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to workspace",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write to the file",
                },
            },
            "required": ["path", "content"],
        }

    def run(self, args: dict[str, Any]) -> str:
        path = safe_path(args["path"], self._ws)
        content = args["content"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        rel = path.relative_to(self._ws)
        return f"Wrote {len(content)} chars to {rel}"


class ListFiles(Tool):
    """List files in a workspace directory."""

    MAX_ENTRIES = 200

    def __init__(self, workspace_dir: Path) -> None:
        self._ws = workspace_dir.resolve()

    @property
    def name(self) -> str:
        return "list_files"

    @property
    def description(self) -> str:
        return "List files and directories within the workspace"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path relative to workspace (default '.')",
                },
            },
            "required": [],
        }

    def run(self, args: dict[str, Any]) -> str:
        dir_path = safe_path(args.get("path", "."), self._ws)
        if not dir_path.is_dir():
            return f"Error: not a directory: {args.get('path', '.')!r}"

        entries: list[str] = []
        for p in sorted(dir_path.iterdir()):
            if p.name in _IGNORE_DIRS:
                continue
            tag = "/" if p.is_dir() else ""
            entries.append(f"  {p.name}{tag}")

        total = len(entries)
        if total > self.MAX_ENTRIES:
            entries = entries[: self.MAX_ENTRIES]
            entries.append(f"  ... (truncated at {self.MAX_ENTRIES}, {total} total)")

        label = args.get("path", ".")
        return (
            f"Contents of {label} ({min(total, self.MAX_ENTRIES)} entries):\n"
            + "\n".join(entries)
            if entries
            else f"Contents of {label}: (empty)"
        )


class SearchFiles(Tool):
    """Search for a string across text files in the workspace."""

    MAX_RESULTS = 100
    MAX_LINE_CHARS = 300

    def __init__(self, workspace_dir: Path) -> None:
        self._ws = workspace_dir.resolve()

    @property
    def name(self) -> str:
        return "search_files"

    @property
    def description(self) -> str:
        return "Search for a keyword across text files within the workspace"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search keyword",
                },
                "path": {
                    "type": "string",
                    "description": "Directory path relative to workspace (default '.')",
                },
            },
            "required": ["query"],
        }

    def run(self, args: dict[str, Any]) -> str:
        query: str = args["query"]
        root = safe_path(args.get("path", "."), self._ws)
        if not root.is_dir():
            return f"Error: not a directory: {args.get('path', '.')!r}"

        results: list[str] = []
        for fpath in sorted(root.rglob("*")):
            # Skip ignored dirs
            if any(p.name in _IGNORE_DIRS for p in fpath.parents) or fpath.name in _IGNORE_DIRS:
                continue
            if not fpath.is_file():
                continue

            # Try reading as text; skip binary
            try:
                lines = fpath.read_text(encoding="utf-8").splitlines()
            except (UnicodeDecodeError, OSError):
                continue

            for lineno, line in enumerate(lines, start=1):
                if query not in line:
                    continue
                rel = fpath.relative_to(self._ws)
                if len(line) > self.MAX_LINE_CHARS:
                    line = line[: self.MAX_LINE_CHARS] + "..."
                results.append(f"{rel}:{lineno}: {line}")
                if len(results) >= self.MAX_RESULTS:
                    break
            if len(results) >= self.MAX_RESULTS:
                break

        if not results:
            return f"No matches for {query!r} in {args.get('path', '.')}"

        header = f"Search results for {query!r} ({min(len(results), self.MAX_RESULTS)} matches):\n"
        if len(results) >= self.MAX_RESULTS:
            return header + "\n".join(results) + f"\n  ... (truncated at {self.MAX_RESULTS} results)"
        return header + "\n".join(results)


# ---------------------------------------------------------------------------
# Self-test (call from main.py at startup)
# ---------------------------------------------------------------------------

def self_test() -> None:  # noqa: D401
    """Run a quick smoke-test of ``safe_path`` in a temp workspace."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp).resolve()

        # --- allowed ---
        assert safe_path("a.txt", ws) == ws / "a.txt"
        assert safe_path("sub/a.txt", ws) == ws / "sub" / "a.txt"

        # --- escape via .. ---
        try:
            safe_path("../secret.txt", ws)
            raise AssertionError("Expected ValueError for ../secret.txt")
        except ValueError:
            pass

        # --- absolute path ---
        try:
            safe_path("/etc/passwd", ws)
            raise AssertionError("Expected ValueError for /etc/passwd")
        except ValueError:
            pass

    print("sandbox ok")
