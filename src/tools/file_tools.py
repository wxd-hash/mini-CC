"""File-related tools and path-safety utilities.

Each tool now follows the cc-mini protocol: execute() returns ToolResult,
is_read_only() declares concurrency safety, get_activity_description()
provides a human-readable one-liner for the terminal spinner.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.config import WORKSPACE_DIR
from src.tools.base import Tool, ToolResult

_IGNORE_DIRS = frozenset({".git", "__pycache__", "node_modules", ".venv"})


def _fmt_size(n: int) -> str:
    """Human-readable file size."""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


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

class FileEditTool(Tool):
    """Edit a file by exact string replacement (matches claude-code's FileEditTool)."""

    MAX_RESULT_CHARS = 5_000

    def __init__(self, workspace_dir: Path) -> None:
        self._ws = workspace_dir.resolve()

    @property
    def maxResultSizeChars(self) -> int:
        return self.MAX_RESULT_CHARS

    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return (
            "精确替换文件中的字符串。old_string 必须在文件中唯一。"
            "用 replace_all=True 可替换所有出现。"
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to workspace",
                },
                "old_string": {
                    "type": "string",
                    "description": "Exact text to replace (must be unique in file)",
                },
                "new_string": {
                    "type": "string",
                    "description": "Replacement text",
                },
                "replace_all": {
                    "type": "boolean",
                    "description": "Replace all occurrences (default: false)",
                },
            },
            "required": ["path", "old_string", "new_string"],
        }

    def get_activity_description(self, **kwargs: Any) -> str | None:
        p = kwargs.get("path", "")
        return f"Editing {p}" if p else "Editing file"

    def execute(self, **kwargs: Any) -> ToolResult:
        path_str = kwargs.get("path", "")
        old = kwargs.get("old_string", "")
        new = kwargs.get("new_string", "")
        replace_all = kwargs.get("replace_all", False)

        try:
            path = safe_path(path_str, self._ws)
        except ValueError as e:
            return ToolResult(content=str(e), is_error=True)

        if not path.is_file():
            return ToolResult(content=f"Error: file not found: {path_str!r}", is_error=True)

        try:
            content = path.read_text(encoding="utf-8")
        except Exception as e:
            return ToolResult(content=f"Error reading file: {e}", is_error=True)

        if not replace_all and old not in content:
            return ToolResult(
                content=f"Error: old_string not found in file. The text must match exactly including whitespace.",
                is_error=True,
            )
        if not replace_all and content.count(old) > 1:
            return ToolResult(
                content=f"Error: old_string found {content.count(old)} times in file. "
                        f"It must be unique. Use a larger string with more surrounding context, "
                        f"or use replace_all=True to replace all occurrences.",
                is_error=True,
            )

        new_content = content.replace(old, new) if replace_all else content.replace(old, new, 1)
        try:
            path.write_text(new_content, encoding="utf-8")
        except Exception as e:
            return ToolResult(content=f"Error writing file: {e}", is_error=True)

        found = content.count(old) if replace_all else 1
        return ToolResult(content=f"Replaced {found} occurrence(s) of old_string in {path}")


# ======================================================================
# File-operation tools
# ======================================================================

class ReadFile(Tool):
    """Read the contents of a workspace file."""

    MAX_CHARS = 12000
    MAX_RESULT_CHARS = 80_000  # matches claude-code FileReadTool

    def __init__(self, workspace_dir: Path) -> None:
        self._ws = workspace_dir.resolve()

    @property
    def maxResultSizeChars(self) -> int:
        return self.MAX_RESULT_CHARS

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "读取工作区内指定文件的内容（最多 12000 字）"

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

    def is_read_only(self) -> bool:
        return True

    def get_activity_description(self, **kwargs: Any) -> str | None:
        p = kwargs.get("path", "")
        return f"Reading {p}" if p else "Reading file"

    def execute(self, **kwargs: Any) -> ToolResult:
        path_str = kwargs.get("path", "")
        try:
            path = safe_path(path_str, self._ws)
        except ValueError as e:
            return ToolResult(content=str(e), is_error=True)
        if not path.is_file():
            return ToolResult(content=f"Error: file not found: {path_str!r}", is_error=True)
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as e:
            return ToolResult(content=f"Error reading file: {e}", is_error=True)
        if len(content) <= self.MAX_CHARS:
            return ToolResult(content=content)
        return ToolResult(content=(
            content[: self.MAX_CHARS]
            + f"\n\n... [truncated at {self.MAX_CHARS} chars, "
            + f"total {len(content)} chars]"
        ))


class WriteFile(Tool):
    """Write content to a workspace file."""

    MAX_RESULT_CHARS = 5_000

    def __init__(self, workspace_dir: Path) -> None:
        self._ws = workspace_dir.resolve()

    @property
    def maxResultSizeChars(self) -> int:
        return self.MAX_RESULT_CHARS

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "将内容写入工作区内的指定文件"

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

    def get_activity_description(self, **kwargs: Any) -> str | None:
        p = kwargs.get("path", "")
        return f"Writing {p}" if p else "Writing file"

    def execute(self, **kwargs: Any) -> ToolResult:
        path_str = kwargs.get("path", "")
        content = kwargs.get("content", "")
        try:
            path = safe_path(path_str, self._ws)
        except ValueError as e:
            return ToolResult(content=str(e), is_error=True)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        except Exception as e:
            return ToolResult(content=f"Error writing file: {e}", is_error=True)
        return ToolResult(content=f"Wrote {len(content)} chars to {path}")


class ListFiles(Tool):
    """List files in a workspace directory."""

    MAX_ENTRIES = 200
    MAX_RESULT_CHARS = 8_000

    def __init__(self, workspace_dir: Path) -> None:
        self._ws = workspace_dir.resolve()

    @property
    def maxResultSizeChars(self) -> int:
        return self.MAX_RESULT_CHARS

    @property
    def name(self) -> str:
        return "list_files"

    @property
    def description(self) -> str:
        return "列出工作区内指定目录的文件和子目录"

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

    def is_read_only(self) -> bool:
        return True

    def get_activity_description(self, **kwargs: Any) -> str | None:
        p = kwargs.get("path", ".")
        return f"Listing {p}"

    def execute(self, **kwargs: Any) -> ToolResult:
        dir_path_str = kwargs.get("path", ".")
        try:
            dir_path = safe_path(dir_path_str, self._ws)
        except ValueError as e:
            return ToolResult(content=str(e), is_error=True)
        if not dir_path.is_dir():
            return ToolResult(content=f"Error: not a directory: {dir_path_str!r}", is_error=True)

        entries: list[str] = []
        for p in sorted(dir_path.iterdir()):
            if p.name in _IGNORE_DIRS:
                continue
            if p.is_dir():
                entries.append(f"  {p.name}/")
            else:
                size = _fmt_size(p.stat().st_size)
                entries.append(f"  {p.name}  ({size})")

        total = len(entries)
        if total > self.MAX_ENTRIES:
            entries = entries[: self.MAX_ENTRIES]
            entries.append(f"  ... (truncated at {self.MAX_ENTRIES}, {total} total)")

        if entries:
            return ToolResult(content=(
                f"Contents of {dir_path_str} ({min(total, self.MAX_ENTRIES)} entries):\n"
                + "\n".join(entries)
            ))
        return ToolResult(content=f"Contents of {dir_path_str}: (empty)")


class SearchFiles(Tool):
    """Search for a string across text files in the workspace."""

    MAX_RESULTS = 100
    MAX_LINE_CHARS = 300
    MAX_RESULT_CHARS = 12_000

    def __init__(self, workspace_dir: Path) -> None:
        self._ws = workspace_dir.resolve()

    @property
    def maxResultSizeChars(self) -> int:
        return self.MAX_RESULT_CHARS

    @property
    def name(self) -> str:
        return "search_files"

    @property
    def description(self) -> str:
        return "在工作区内的文本文件中搜索关键词"

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

    def is_read_only(self) -> bool:
        return True

    def get_activity_description(self, **kwargs: Any) -> str | None:
        q = kwargs.get("query", "")
        return f"Searching for '{q}'" if q else "Searching"

    def execute(self, **kwargs: Any) -> ToolResult:
        query: str = kwargs.get("query", "")
        root_str = kwargs.get("path", ".")
        try:
            root = safe_path(root_str, self._ws)
        except ValueError as e:
            return ToolResult(content=str(e), is_error=True)
        if not root.is_dir():
            return ToolResult(content=f"Error: not a directory: {root_str!r}", is_error=True)

        # Try ripgrep first for speed on large projects
        rg_result = self._rg_search(query, root)
        if rg_result is not None:
            return ToolResult(content=rg_result)

        # Fall back to pure Python
        return ToolResult(content=self._slow_search(query, root, root_str))

    # ------------------------------------------------------------------
    # ripgrep accelerated search
    # ------------------------------------------------------------------

    def _rg_search(self, query: str, root: Path) -> str | None:
        """Try ripgrep. Returns None if rg is unavailable or times out."""
        import subprocess
        try:
            ignores: list[str] = []
            for d in _IGNORE_DIRS:
                ignores += ["--glob", f"!{d}", "--glob", f"!{d}/**"]
            proc = subprocess.run(
                [
                    "rg", "--no-heading", "--line-number",
                    "--max-count", str(self.MAX_RESULTS),
                    *ignores,
                    "--", query, str(root),
                ],
                capture_output=True, text=True, timeout=10,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None

        output = proc.stdout.rstrip()
        if proc.returncode == 1 and not output:
            return f"No matches for {query!r}"
        if not output:
            return None

        lines = output.split("\n")
        truncated = len(lines) > self.MAX_RESULTS
        if truncated:
            lines = lines[: self.MAX_RESULTS]

        result_lines: list[str] = []
        for line in lines:
            if len(line) > self.MAX_LINE_CHARS + 50:
                line = line[: self.MAX_LINE_CHARS + 50] + "..."
            result_lines.append(line)

        header = f"Search results for {query!r} ({len(result_lines)} matches):\n"
        if truncated:
            return header + "\n".join(result_lines) + f"\n  ... (truncated at {self.MAX_RESULTS} results)"
        return header + "\n".join(result_lines) if result_lines else f"No matches for {query!r}"

    # ------------------------------------------------------------------
    # Pure-Python fallback
    # ------------------------------------------------------------------

    def _slow_search(self, query: str, root: Path, label: str) -> str:
        results: list[str] = []
        for fpath in sorted(root.rglob("*")):
            if any(p.name in _IGNORE_DIRS for p in fpath.parents) or fpath.name in _IGNORE_DIRS:
                continue
            if not fpath.is_file():
                continue

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
            return f"No matches for {query!r} in {label}"

        header = f"Search results for {query!r} ({min(len(results), self.MAX_RESULTS)} matches):\n"
        if len(results) >= self.MAX_RESULTS:
            return header + "\n".join(results) + f"\n  ... (truncated at {self.MAX_RESULTS} results)"
        return header + "\n".join(results)


class GlobTool(Tool):
    """Fast file pattern matching using glob patterns.

    Matches Claude Code's Glob tool: finds files by pattern (e.g. "src/**/*.ts")
    without needing shell find/ls commands.
    """

    MAX_RESULTS = 500
    MAX_RESULT_CHARS = 8_000

    def __init__(self, workspace_dir: Path) -> None:
        self._ws = workspace_dir

    @property
    def name(self) -> str:
        return "glob"

    @property
    def description(self) -> str:
        return (
            "- 按通配符模式匹配文件路径，结果按修改时间降序排列\n"
            "- 知道文件命名模式时用 Glob 精准查找（如 `src/**/*.py`、`**/*.md`），"
            "探索目录结构时用 list_files\n"
            "- 支持 *, **, ?, [] 通配符\n"
            "- 不要用 run_shell 的 find 或 ls 代替——Glob 更快更安全"
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "文件匹配模式，支持通配符 *, **, ?, []。如 src/**/*.py 或 **/*.md",
                },
            },
            "required": ["pattern"],
        }

    @property
    def maxResultSizeChars(self) -> int | None:
        return self.MAX_RESULT_CHARS

    def is_read_only(self) -> bool:
        return True

    def get_activity_description(self, **kwargs: Any) -> str | None:
        return f"glob({kwargs.get('pattern', '*')})"

    def execute(self, pattern: str = "", **kwargs: Any) -> ToolResult:
        ws = self._ws
        try:
            matches = sorted(ws.glob(pattern))
        except Exception as e:
            return ToolResult(content=f"Glob 模式错误: {e}", is_error=True)

        if not matches:
            return ToolResult(content=f"没有匹配 '{pattern}' 的文件")

        # Compute name column width for alignment
        max_name_len = max((len(m.name) for m in matches), default=20)
        lines = []
        count = 0
        for m in matches:
            if count >= self.MAX_RESULTS:
                lines.append(f"... (还有 {len(matches) - self.MAX_RESULTS} 个结果未显示)")
                break
            rel = str(m.relative_to(ws)).replace("\\", "/")
            try:
                size = m.stat().st_size
                size_str = _fmt_size(size)
            except OSError:
                size_str = "?"
            name_part = m.name.ljust(max_name_len + 2)
            lines.append(f"{rel}  ({size_str})")
            count += 1

        header = f"匹配 '{pattern}': {len(matches)} 个文件\n\n"
        body = "\n".join(lines)
        result = header + body
        if len(result) > self.MAX_RESULT_CHARS:
            result = result[:self.MAX_RESULT_CHARS] + "\n... [truncated]"
        return ToolResult(content=result)


# ---------------------------------------------------------------------------
# Self-test (call from main.py at startup)
# ---------------------------------------------------------------------------

def self_test() -> None:
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
