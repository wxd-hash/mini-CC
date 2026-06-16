"""Shell execution tool."""

from __future__ import annotations

import locale
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from src.tools.base import Tool


class RunShell(Tool):
    """Execute a shell command inside the workspace directory."""

    TIMEOUT = 30
    MAX_OUTPUT = 12000

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
            "Run a shell command inside the workspace directory. "
            f"Timeout after {self.TIMEOUT}s. "
            "Returns exit code, stdout, and stderr."
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
            },
            "required": ["command"],
        }

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def run(
        self,
        args: dict[str, Any],
        permission_manager: Any = None,
    ) -> str:
        """Run the command.  *permission_manager* is reserved."""
        command: str = args["command"]

        stdout_f = tempfile.NamedTemporaryFile(
            mode="wb+", suffix=".stdout", delete=False
        )
        stderr_f = tempfile.NamedTemporaryFile(
            mode="wb+", suffix=".stderr", delete=False
        )

        try:
            # Prepend venv to PATH so python/pip/pytest resolve correctly
            env = os.environ.copy()
            env["PATH"] = self._venv_bin + os.pathsep + env.get("PATH", "")
            # Also set VIRTUAL_ENV so the subprocess knows it's in a venv
            env["VIRTUAL_ENV"] = str(Path(sys.executable).parent.parent)

            proc = subprocess.Popen(
                command,
                shell=True,
                stdout=stdout_f,
                stderr=stderr_f,
                cwd=str(self._ws),
                env=env,
            )

            deadline = time.monotonic() + self.TIMEOUT
            while True:
                ret = proc.poll()
                if ret is not None:
                    break
                if time.monotonic() > deadline:
                    self._kill_tree(proc)
                    proc.wait(timeout=5)
                    return (
                        f"Error: command timed out after {self.TIMEOUT}s\n"
                        f"  command: {command[:200]!r}"
                    )
                time.sleep(0.1)

            stdout_f.flush()
            stderr_f.flush()
            stdout_f.seek(0)
            stderr_f.seek(0)
            stdout_bytes = stdout_f.read()
            stderr_bytes = stderr_f.read()

            stdout = _decode(stdout_bytes)
            stderr = _decode(stderr_bytes)

            return self._format(ret, stdout, stderr)

        except Exception as exc:
            return f"Error: {exc}"
        finally:
            stdout_f.close()
            stderr_f.close()
            _unlink(stdout_f.name)
            _unlink(stderr_f.name)

    # ------------------------------------------------------------------
    # Output formatting
    # ------------------------------------------------------------------

    def _format(
        self,
        returncode: int,
        stdout: str,
        stderr: str,
    ) -> str:
        lines: list[str] = [f"exit_code: {returncode}"]

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
