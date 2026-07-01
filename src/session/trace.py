"""TraceLogger — unified event tracing for the entire agent pipeline.

Replaces the fragmented SessionLogger + SessionStore dual-logging with
a single structured JSONL writer. Every significant event in the
pipeline gets recorded with type, timestamp, and sequence number.

Event types:
  session_start, user_input, system_prompt, repl_command,
  api_request, api_response, api_retry, api_stream_done,
  tool_permission, tool_start, tool_done,
  skill_invoke,
  compact_start, compact_done,
  memory_extract,
  error
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class TraceLogger:
    """Structured event tracer writing to a rotating JSONL file.

    Thread-safe. Auto-rotates when file exceeds 50 MB.
    Lazy materialization: file is not created until first event is emitted.
    """

    MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB

    def __init__(self, path: Path, workspace: str = "") -> None:
        self._path = path
        self._lock = threading.Lock()
        self._seq = 0
        self._dir = path.parent
        self._file = None         # lazily opened
        self._pending: list[dict[str, Any]] = []  # buffer before materialize
        self._pending_meta: dict[str, Any] = {}    # workspace/provider/model/mode for session_start

        if workspace:
            self._pending_meta = {
                "workspace": workspace,
                "provider": "",
                "model": "",
                "mode": "ask",
            }

    # -- lifecycle ------------------------------------------------------------

    def adopt(self, path: Path) -> None:
        """Adopt an existing session file (for resume).
        Discards any buffered pending events — the resumed file already
        has its own session_start and history."""
        self._path = path
        self._dir = path.parent
        self._pending.clear()
        self._pending_meta.clear()

    def _materialize(self) -> None:
        """Create the file and flush pending entries on first write."""
        if self._file is not None:
            return
        self._dir.mkdir(parents=True, exist_ok=True)
        self._file = open(self._path, "a", encoding="utf-8")

        # Emit session_start if metadata was provided
        if self._pending_meta:
            self.session_start(**self._pending_meta)
            self._pending_meta.clear()

        # Flush buffered events
        for entry in self._pending:
            self._file.write(
                json.dumps(entry, ensure_ascii=False, default=str) + "\n"
            )
        self._file.flush()
        self._pending.clear()

    # ------------------------------------------------------------------
    # Public API — one method per event type
    # ------------------------------------------------------------------

    def session_start(
        self,
        workspace: str = "",
        provider: str = "",
        model: str = "",
        mode: str = "ask",
    ) -> None:
        # If not materialized yet, update pending_meta instead of buffering
        # a separate event. This avoids duplicate session_start on materialize.
        if self._file is None:
            self._pending_meta = {
                "workspace": workspace,
                "provider": provider,
                "model": model,
                "mode": mode,
            }
            return
        self._emit("session_start", {
            "workspace": workspace,
            "provider": provider,
            "model": model,
            "mode": mode,
        })

    def user_input(self, content: str, is_command: bool = False) -> None:
        self._emit("user_input", {
            "content": content,
            "length": len(content),
            "is_command": is_command,
        })

    def system_prompt(self, sections: dict[str, int], total_chars: int) -> None:
        self._emit("system_prompt", {
            "sections": sections,
            "total_chars": total_chars,
        })

    def repl_command(self, command: str, args: str = "", success: bool = True) -> None:
        self._emit("repl_command", {
            "command": command,
            "args": args,
            "success": success,
        })

    # -- API lifecycle --------------------------------------------------

    def api_request(
        self,
        model: str,
        max_tokens: int,
        message_count: int,
        estimated_tokens: int,
        attempt: int = 0,
    ) -> None:
        self._emit("api_request", {
            "model": model,
            "max_tokens": max_tokens,
            "message_count": message_count,
            "estimated_tokens": estimated_tokens,
            "attempt": attempt,
        })

    def api_response(
        self,
        input_tokens: int = 0,
        output_tokens: int = 0,
        latency_ms: int = 0,
        cost: float = 0.0,
        status: str = "ok",
    ) -> None:
        self._emit("api_response", {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "latency_ms": latency_ms,
            "cost": cost,
            "status": status,
        })

    def api_retry(
        self,
        attempt: int,
        error: str,
        delay_ms: float = 0.0,
        new_max_tokens: int | None = None,
    ) -> None:
        data: dict[str, Any] = {
            "attempt": attempt,
            "error": error[:200],
            "delay_ms": round(delay_ms, 1),
        }
        if new_max_tokens is not None:
            data["new_max_tokens"] = new_max_tokens
        self._emit("api_retry", data)

    def api_stream_done(
        self,
        text_chunks: int = 0,
        tool_calls_count: int = 0,
    ) -> None:
        self._emit("api_stream_done", {
            "text_chunks": text_chunks,
            "tool_calls_count": tool_calls_count,
        })

    # -- Tool lifecycle -------------------------------------------------

    def tool_permission(
        self,
        tool_name: str,
        args_summary: str,
        decision: str,
        mode: str,
        reason: str = "",
    ) -> None:
        self._emit("tool_permission", {
            "tool": tool_name,
            "args": args_summary[:120],
            "decision": decision,
            "mode": mode,
            "reason": reason,
        })

    def tool_start(
        self,
        tool_name: str,
        args_summary: str = "",
        is_read_only: bool | None = None,
        is_skill: bool = False,
    ) -> str:
        """Record tool execution start. Returns a run_id for matching tool_done."""
        run_id = f"{tool_name}-{self._seq}"
        data: dict[str, Any] = {
            "tool": tool_name,
            "run_id": run_id,
        }
        if args_summary:
            data["args"] = args_summary[:200]
        if is_read_only is not None:
            data["is_read_only"] = is_read_only
        if is_skill:
            data["is_skill"] = True
        self._emit("tool_start", data)
        return run_id

    def tool_done(
        self,
        tool_name: str,
        run_id: str = "",
        result_preview: str = "",
        elapsed_ms: float = 0.0,
        is_error: bool = False,
    ) -> None:
        self._emit("tool_done", {
            "tool": tool_name,
            "run_id": run_id,
            "result": result_preview[:300],
            "elapsed_ms": round(elapsed_ms, 1),
            "is_error": is_error,
        })

    def skill_invoke(
        self,
        skill_name: str,
        args: str = "",
        elapsed_ms: float = 0.0,
    ) -> None:
        data: dict[str, Any] = {
            "skill": skill_name,
            "elapsed_ms": round(elapsed_ms, 1),
        }
        if args:
            data["args"] = args[:120]
        self._emit("skill_invoke", data)

    # -- Compaction -----------------------------------------------------

    def compact_start(
        self,
        reason: str = "token",
        messages_before: int = 0,
        estimated_tokens: int = 0,
    ) -> None:
        self._emit("compact_start", {
            "reason": reason,
            "messages_before": messages_before,
            "estimated_tokens": estimated_tokens,
        })

    def compact_done(
        self,
        messages_after: int,
        summary_preview: str = "",
    ) -> None:
        self._emit("compact_done", {
            "messages_after": messages_after,
            "summary": summary_preview[:300],
        })

    # -- Memory extraction ----------------------------------------------

    def memory_extract(
        self,
        extracted: int = 0,
        updated: int = 0,
        cleaned: int = 0,
        skipped: bool = False,
        reason: str = "",
        elapsed_ms: float = 0.0,
    ) -> None:
        self._emit("memory_extract", {
            "extracted": extracted,
            "updated": updated,
            "cleaned": cleaned,
            "skipped": skipped,
            "reason": reason,
            "elapsed_ms": round(elapsed_ms, 1),
        })

    # -- Error ----------------------------------------------------------

    def error(
        self,
        source: str,
        message: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        data: dict[str, Any] = {
            "source": source,
            "message": message[:500],
        }
        if context:
            data["context"] = {k: str(v)[:120] for k, v in context.items()}
        self._emit("error", data)

    # -- Close ----------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            try:
                if self._file is not None:
                    self._file.close()
                    self._file = None
            except Exception:
                pass

    @property
    def path(self) -> Path:
        return self._path

    @property
    def sequence(self) -> int:
        return self._seq

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _emit(self, event_type: str, data: dict[str, Any]) -> None:
        entry = {"type": event_type, "ts": _utc_now(), "seq": self._next_seq()}
        entry.update(data)
        if self._file is None:
            self._pending.append(entry)
            return
        line = json.dumps(entry, ensure_ascii=False, default=str) + "\n"
        with self._lock:
            self._file.write(line)
            self._file.flush()
        self._maybe_rotate()

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _maybe_rotate(self) -> None:
        """Rotate file if it exceeds size limit."""
        try:
            if self._path.stat().st_size > self.MAX_FILE_SIZE:
                with self._lock:
                    self._file.close()
                    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
                    new_name = f"{self._path.stem}-{ts}{self._path.suffix}"
                    new_path = self._dir / new_name
                    self._path.rename(new_path)
                    self._file = open(self._path, "a", encoding="utf-8")
        except Exception:
            pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
