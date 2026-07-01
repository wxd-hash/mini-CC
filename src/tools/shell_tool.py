"""Shell execution tool — matches cc-mini BashTool pattern."""

from __future__ import annotations

import locale
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from src.tools.base import Tool, ToolResult


class RunShell(Tool):
    """Execute a shell command inside the workspace directory."""

    TIMEOUT = 30
    MAX_OUTPUT = 12000
    MAX_RESULT_CHARS = 30_000  # matches claude-code BashTool

    def __init__(self, workspace_dir: Path) -> None:
        self._ws = workspace_dir.resolve()
        # Include venv Scripts in subprocess PATH so python/pip/pytest are found
        self._venv_bin = str(Path(sys.executable).parent)

    # ------------------------------------------------------------------
    # Tool metadata
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "run_shell"

    @property
    def description(self) -> str:
        return (
            f"在工作区目录内执行 shell 命令（超时 {self.TIMEOUT} 秒）。返回退出码、stdout 和 stderr。\n\n"
            "**⚠️ 不要用 run_shell 做以下操作——专用工具更好用且更安全：**\n"
            "- 读文件 → read_file（不用 cat/head/tail）\n"
            "- 写文件 → write_file / edit_file（不用 echo >/sed/awk）\n"
            "- 找文件 → glob（不用 find/ls）\n"
            "- 搜内容 → search_files（不用 grep/rg）\n"
            "- 获取网页 → web_fetch / web_search（不用 curl/wget）\n"
            "- 文本输出 → 直接输出到对话（不用 echo/printf）\n\n"
            "**run_shell 的正确用途**：包安装（pip, npm）、测试运行（pytest, go test）、"
            "构建命令（make, cmake）、git 操作（git add/commit，不用 --no-verify）、"
            "启动开发服务器（用 run_in_background=true）"
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute",
                },
                "run_in_background": {
                    "type": "boolean",
                    "description": (
                        "Set to true to run in background (for servers, watchers). "
                        "You'll be notified when it completes. Do NOT poll or check."
                    ),
                },
                "timeout": {
                    "type": "number",
                    "description": f"Optional timeout in ms (default: {self.TIMEOUT * 1000})",
                },
            },
            "required": ["command"],
        }

    @property
    def maxResultSizeChars(self) -> int:
        return self.MAX_RESULT_CHARS

    # -- cc-mini protocol --------------------------------------------------

    def get_activity_description(self, **kwargs: Any) -> str | None:
        cmd = kwargs.get("command", "")
        bg = kwargs.get("run_in_background", False)
        prefix = "[bg] " if bg else ""
        short = cmd[:60] + "..." if len(cmd) > 60 else cmd
        return f"{prefix}Running: {short}" if short else "Running command"

    # ------------------------------------------------------------------
    # Execution — matches claude-code BashTool with auto-background
    # ------------------------------------------------------------------

    # Long-running server patterns: auto-background instead of kill
    _SERVER_PATTERNS = [
        "uvicorn", "flask run", "python -m http", "gunicorn",
        "npm run dev", "npm start", "node server", "next dev",
        "cargo run", "go run", "java -jar", "docker compose up",
        "docker run", "docker-compose",
    ]

    # Short timeout for auto-background (matches claude-code ASSISTANT_BLOCKING_BUDGET_MS)
    BLOCKING_TIMEOUT = 15  # seconds — after this, auto-background
    MAX_TIMEOUT = 600  # 10 minutes max

    def execute(self, **kwargs: Any) -> ToolResult:
        """Run the command. Auto-backgrounds long-running commands instead of killing.

        Matches claude-code's pattern:
        - Normal commands: run to completion (up to self.TIMEOUT seconds)
        - Server commands or run_in_background=True: return immediately,
          process keeps running, model gets PID
        - Commands exceeding BLOCKING_TIMEOUT: auto-background instead of kill
        """
        command: str = kwargs.get("command", "")
        run_in_background: bool = kwargs.get("run_in_background", False)
        timeout_ms: float | None = kwargs.get("timeout")
        timeout = (timeout_ms / 1000.0) if timeout_ms else self.TIMEOUT
        timeout = min(timeout, self.MAX_TIMEOUT)

        # Detect if this looks like a server/long-running command
        is_server = any(p in command.lower() for p in self._SERVER_PATTERNS)
        should_background = run_in_background or is_server

        if should_background:
            # Start in background, return immediately with PID
            return self._run_background(command)

        stdout_f = tempfile.NamedTemporaryFile(
            mode="wb+", suffix=".stdout", delete=False
        )
        stderr_f = tempfile.NamedTemporaryFile(
            mode="wb+", suffix=".stderr", delete=False
        )

        try:
            env = os.environ.copy()
            env["PATH"] = self._venv_bin + os.pathsep + env.get("PATH", "")
            env["VIRTUAL_ENV"] = str(Path(sys.executable).parent.parent)

            proc = subprocess.Popen(
                command,
                shell=True,
                stdout=stdout_f,
                stderr=stderr_f,
                cwd=str(self._ws),
                env=env,
            )

            # First phase: wait up to BLOCKING_TIMEOUT for quick commands
            deadline = time.monotonic() + min(self.BLOCKING_TIMEOUT, timeout)
            while True:
                ret = proc.poll()
                if ret is not None:
                    break
                if time.monotonic() > deadline:
                    # Auto-background: command is taking too long, detach
                    stdout_f.flush()
                    stderr_f.flush()
                    stdout_f.seek(0)
                    stderr_f.seek(0)
                    so_far = _decode(stdout_f.read())[:2000]
                    se_far = _decode(stderr_f.read())[:500]
                    return ToolResult(content=(
                        f"[auto-backgrounded after {self.BLOCKING_TIMEOUT}s] "
                        f"PID {proc.pid} is still running in background.\n"
                        f"This is NOT an error — the process continues.\n"
                        f"Do NOT kill and restart. It's already running.\n"
                        f"Output so far:\n{so_far}"
                        + (f"\nstderr:\n{se_far}" if se_far else "")
                    ))
                time.sleep(0.1)

            stdout_f.flush()
            stderr_f.flush()
            stdout_f.seek(0)
            stderr_f.seek(0)
            stdout = _decode(stdout_f.read())
            stderr = _decode(stderr_f.read())

            return ToolResult(content=self._format(ret, stdout, stderr))

        except Exception as exc:
            return ToolResult(content=f"Error: {exc}", is_error=True)
        finally:
            stdout_f.close()
            stderr_f.close()
            _unlink(stdout_f.name)
            _unlink(stderr_f.name)

    def _run_background(self, command: str) -> ToolResult:
        """Start a command in the background, return immediately.

        Matches claude-code's run_in_background / LocalShellTask pattern.
        The process keeps running; the model is told it's backgrounded.
        """
        try:
            env = os.environ.copy()
            env["PATH"] = self._venv_bin + os.pathsep + env.get("PATH", "")
            env["VIRTUAL_ENV"] = str(Path(sys.executable).parent.parent)

            proc = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=str(self._ws),
                env=env,
            )
            return ToolResult(content=(
                f"Started in background. PID: {proc.pid}\n"
                f"Command: {command[:200]!r}\n"
                f"The process is running. Do NOT kill it. "
                f"Use the running server — it's already listening."
            ))
        except Exception as exc:
            return ToolResult(content=f"Error starting background process: {exc}", is_error=True)

    # ------------------------------------------------------------------
    # Output formatting
    # ------------------------------------------------------------------

    def _format(self, returncode: int, stdout: str, stderr: str) -> str:
        lines: list[str] = []
        # Command semantics — interpret exit codes so the model doesn't
        # mistake "no matches" or "files differ" for errors (claude-code pattern)
        hint = self._interpret_exit_code(returncode, stdout, stderr)
        if hint:
            lines.append(f"exit_code: {returncode}  ({hint})")
        else:
            lines.append(f"exit_code: {returncode}")

        s_out = stdout.rstrip()
        if s_out:
            lines.append(f"stdout:\n{s_out}")
        else:
            lines.append("stdout: (empty)")

        s_err = stderr.rstrip()
        if s_err:
            lines.append(f"stderr:\n{s_err}")

        output = "\n".join(lines)

        if len(output) > self.MAX_OUTPUT:
            output = (
                output[: self.MAX_OUTPUT]
                + f"\n\n... [truncated at {self.MAX_OUTPUT} chars, "
                + f"total {len(output)} chars]"
            )

        return output

    @staticmethod
    def _interpret_exit_code(exit_code: int, stdout: str, stderr: str) -> str | None:
        """Interpret exit code semantics — matches claude-code commandSemantics.ts.

        Returns a hint string for non-failure exit codes, helping the model
        avoid retrying commands that didn't actually fail.
        """
        # exit 0 is always success
        if exit_code == 0:
            return None
        # exit 1 for grep/rg = no matches found (not an error)
        # exit 1 for diff = files differ (not an error)
        # exit 1 for test/[ = condition false (not an error)
        if exit_code == 1:
            return "this may be expected (grep 'no match', diff 'files differ', test 'false')"
        if exit_code == 2:
            return "error — fix the issue before retrying"
        if exit_code >= 126:
            return "fatal — cannot execute, do NOT retry the same command"
        return f"unexpected exit code — analyze before retrying"

    # ------------------------------------------------------------------
    # Process cleanup
    # ------------------------------------------------------------------

    @staticmethod
    def _kill_tree(proc: subprocess.Popen[bytes]) -> None:
        try:
            proc.kill()
        except Exception:
            pass

        if sys.platform == "win32":
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                    timeout=5,
                )
            except Exception:
                pass


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _decode(data: bytes) -> str:
    """Decode subprocess output, trying UTF-8 first, then locale encoding.

    UTF-8 is tried first because it reliably raises UnicodeDecodeError on
    non-UTF-8 input, while code-page encodings (cp936, Latin-1) silently
    decode almost any byte sequence into garbage characters.
    """
    if not data:
        return ""

    locale_enc = locale.getpreferredencoding(do_setlocale=False)
    encodings = ["utf-8", locale_enc, "gbk", "cp936", "latin-1"]
    seen: set[str] = set()
    for enc in encodings:
        if enc in seen:
            continue
        seen.add(enc)
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue

    return data.decode("utf-8", errors="replace")


def _unlink(path: str) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass
