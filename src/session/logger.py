"""Session persistence — matches cc-mini's SessionStore pattern.

SessionStore: auto-saves messages as JSONL + metadata as meta.json.
Also retains the original SessionLogger class and free functions for backward compat.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# SessionStore (matches cc-mini)
# ---------------------------------------------------------------------------

@dataclass
class SessionMeta:
    """Metadata for a saved session."""
    session_id: str
    cwd: str = ""
    model: str = ""
    mode: str = "ask"
    title: str = ""
    created_at: str = ""


class SessionStore:
    """Auto-save session messages to a JSONL file + meta.json.

    Matches cc-mini's SessionStore: append_message() on every turn,
    list_sessions() for resume picker, load_session() for restore.
    """

    MAX_MESSAGE_CHARS = 8000

    def __init__(
        self,
        cwd: str = "",
        model: str = "",
        session_id: str | None = None,
        mode: str = "ask",
        sessions_dir: Path | None = None,
    ) -> None:
        self.cwd = cwd
        self.model = model
        self.mode = mode
        if session_id is None:
            session_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.session_id = session_id
        self._ws_name = _workspace_dir_name(cwd) if cwd else "_default"
        if sessions_dir is None:
            sessions_dir = Path.cwd() / ".sessions"
        self._dir = sessions_dir / self._ws_name
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / f"session-{session_id}.jsonl"
        self._meta_path = self._dir / f"session-{session_id}.meta.json"
        self._file = self._path.open("a", encoding="utf-8")
        self._msg_count = 0
        self._first_user_text = ""
        self._write_meta()

    # -- properties -----------------------------------------------------------

    @property
    def path(self) -> Path:
        return self._path

    @property
    def title(self) -> str:
        return self._first_user_text[:60] if self._first_user_text else "(new session)"

    # -- message persistence --------------------------------------------------

    def append_message(self, message: dict[str, Any]) -> None:
        """Append one message to the session JSONL file."""
        try:
            role = message.get("role", "?")
            content = message.get("content", "")

            # Truncate long content for storage
            stored_content = content
            if isinstance(stored_content, str) and len(stored_content) > self.MAX_MESSAGE_CHARS:
                stored_content = stored_content[:self.MAX_MESSAGE_CHARS] + (
                    f"\n... [truncated at {self.MAX_MESSAGE_CHARS} chars in log]"
                )

            entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "role": role,
                "content": stored_content,
            }
            line = json.dumps(entry, ensure_ascii=False, default=str)
            self._file.write(line + "\n")
            self._file.flush()
            self._msg_count += 1

            # Capture first user text as session title
            if not self._first_user_text and role == "user":
                text = content if isinstance(content, str) else str(content)[:100]
                self._first_user_text = text.split("\n")[0].strip()
                self._write_meta()
        except Exception:
            pass  # don't break the conversation on I/O errors

    def close(self) -> None:
        try:
            self._file.close()
        except Exception:
            pass

    def reopen(self, path: Path) -> None:
        """Reopen this store to append to an existing session file.

        Used after session resume to continue writing to the original file
        instead of creating a duplicate.
        """
        self.close()
        self._path = path
        self._file = path.open("a", encoding="utf-8")
        # Update session_id from the file name
        stem = path.stem  # e.g. "session-20260701T083029Z"
        if stem.startswith("session-"):
            self.session_id = stem[len("session-"):]
        self._write_meta()

    # -- metadata -------------------------------------------------------------

    def _write_meta(self) -> None:
        try:
            meta = {
                "session_id": self.session_id,
                "cwd": self.cwd,
                "model": self.model,
                "mode": self.mode,
                "title": self.title,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
        except Exception:
            pass

    # -- static helpers -------------------------------------------------------

    @staticmethod
    def list_sessions(
        cwd: str,
        sessions_dir: Path | None = None,
    ) -> list[SessionMeta]:
        """Return sorted list of SessionMeta for the given workspace."""
        if sessions_dir is None:
            sessions_dir = Path.cwd() / ".sessions"
        ws_name = _workspace_dir_name(cwd) if cwd else "_default"
        ws_dir = sessions_dir / ws_name
        if not ws_dir.is_dir():
            return []

        result: list[SessionMeta] = []
        for meta_file in sorted(ws_dir.glob("session-*.meta.json"), reverse=True):
            try:
                data = json.loads(meta_file.read_text(encoding="utf-8"))
                result.append(SessionMeta(
                    session_id=data.get("session_id", meta_file.stem.replace("session-", "").replace(".meta", "")),
                    cwd=data.get("cwd", cwd),
                    model=data.get("model", ""),
                    mode=data.get("mode", "ask"),
                    title=data.get("title", "(empty)"),
                    created_at=data.get("created_at", ""),
                ))
            except Exception:
                continue
        # Sort by session_id descending (which is timestamp-based)
        result.sort(key=lambda m: m.session_id, reverse=True)
        return result

    @staticmethod
    def load_session(
        session_id: str,
        cwd: str,
        sessions_dir: Path | None = None,
    ) -> tuple[SessionMeta | None, list[dict[str, Any]]]:
        """Load a session by ID. Returns (meta, messages)."""
        if sessions_dir is None:
            sessions_dir = Path.cwd() / ".sessions"
        ws_name = _workspace_dir_name(cwd) if cwd else "_default"
        ws_dir = sessions_dir / ws_name

        jsonl_path = ws_dir / f"session-{session_id}.jsonl"
        meta_path = ws_dir / f"session-{session_id}.meta.json"

        # Load meta
        meta: SessionMeta | None = None
        if meta_path.is_file():
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
                meta = SessionMeta(**data)
            except Exception:
                pass

        # Load messages
        messages: list[dict[str, Any]] = []
        if jsonl_path.is_file():
            try:
                with jsonl_path.open("r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            ev = json.loads(line)
                            messages.append({
                                "role": ev.get("role", "user"),
                                "content": ev.get("content", ""),
                            })
                        except json.JSONDecodeError:
                            continue
            except Exception:
                pass

        return meta, messages

    @staticmethod
    def cleanup_empty(path: Path) -> None:
        """Delete a session file if it has no user messages."""
        try:
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    try:
                        ev = json.loads(line.strip())
                        if ev.get("role") == "user":
                            return  # has content
                    except json.JSONDecodeError:
                        continue
            path.unlink(missing_ok=True)
            # Also clean up meta file
            meta_path = path.with_suffix(".meta.json")
            meta_path.unlink(missing_ok=True)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Legacy SessionLogger (kept for backward compatibility)
# ---------------------------------------------------------------------------

class SessionLogger:
    """Writes structured session events to a timestamped JSONL file.

    Now delegates to TraceLogger internally. Kept for backward compatibility
    with existing code. New code should use TraceLogger directly.
    """

    MAX_RESULT_CHARS = 4000

    def __init__(self, path: Path, workspace: str = "", trace: Any = None) -> None:
        from src.session.trace import TraceLogger as _TL
        self.path = path
        self._trace: Any = trace if trace is not None else _TL(path, workspace=workspace)

    def user_input(self, content: str) -> None:
        self._trace.user_input(content)

    def assistant_text(self, content: str) -> None:
        self._write({"type": "assistant_text", "content": content})

    def tool_use(self, name: str, args: dict[str, Any]) -> None:
        self._trace.tool_start(name, _fmt_tool_args(args) if args else "")

    def tool_result(self, name: str, content: str) -> None:
        if len(content) > self.MAX_RESULT_CHARS:
            content = content[: self.MAX_RESULT_CHARS] + (
                f"\n... [truncated at {self.MAX_RESULT_CHARS} chars in log]"
            )
        self._trace.tool_done(name, result_preview=content[:300])

    def permission_denied(self, name: str) -> None:
        self._trace.tool_permission(name, "", "deny", "")

    def error(self, message: str) -> None:
        self._trace.error("tool", message)

    def compact(self, events_before: int, events_after: int) -> None:
        self._trace.compact_done(events_after)

    def close(self) -> None:
        self._trace.close()

    def _write(self, entry: dict[str, Any]) -> None:
        """Legacy raw write — still supported for non-standard events."""
        entry["ts"] = _utc_now()
        try:
            line = json.dumps(entry, ensure_ascii=False, default=str)
        except Exception:
            line = json.dumps({"type": "log_error", "message": "failed to serialize entry"})
        with self._trace._lock:
            self._trace._file.write(line + "\n")
            self._trace._file.flush()


# ---------------------------------------------------------------------------
# Resume helpers (free functions)
# ---------------------------------------------------------------------------

MAX_RESUME_EVENTS = 200


def load_session_messages(path: Path, max_events: int = MAX_RESUME_EVENTS) -> list[dict[str, Any]]:
    """Reconstruct provider-agnostic messages from a session log."""
    if not path.is_file():
        raise FileNotFoundError(f"Session file not found: {path}")

    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if len(events) > max_events:
        events = events[-max_events:]

    # Detect format: old SessionLogger uses "type", new SessionStore uses "role"
    if not events:
        return []
    first = events[0]
    # SessionLogger starts with type="session_start"
    # SessionStore entries always have role
    if first.get("type") == "session_start":
        return _load_from_session_logger(events)
    if "role" in first:
        return _load_from_session_store(events)
    # Fallback: try both
    return _load_from_session_logger(events)


def _fmt_tool_args(args: dict[str, Any]) -> str:
    """Format tool arguments as key='value' pairs (matches live terminal display)."""
    parts = []
    for k, v in args.items():
        s = str(v).replace("\n", "\\n")
        if len(s) > 60:
            s = s[:57] + "..."
        parts.append(f"{k}={s!r}")
    return ", ".join(parts)


def _load_from_session_logger(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Load from legacy SessionLogger format (type-based events)."""
    messages: list[dict[str, Any]] = []
    pending_tools: list[str] = []

    for ev in events:
        t = ev.get("type", "?")
        if t == "user_input":
            if pending_tools:
                for tool in pending_tools:
                    messages.append({"_type": "tool_call", "content": tool})
                pending_tools.clear()
            messages.append({"_type": "user_input", "content": ev.get("content", "")})

        elif t == "assistant_text":
            content = ev.get("content", "")
            if content.strip():
                if pending_tools:
                    for tool in pending_tools:
                        messages.append({"_type": "tool_call", "content": tool})
                    pending_tools.clear()
                messages.append({"_type": "assistant_text", "content": content})

        elif t == "tool_use":
            name = ev.get("name", "?")
            args = ev.get("args", {})
            args_str = _fmt_tool_args(args)
            pending_tools.append(f"{name}({args_str})")

        elif t == "tool_result":
            name = ev.get("name", "?")
            content = ev.get("content", "")
            if len(content) > 300:
                content = content[:500] + "..."
            if pending_tools:
                tool_name = pending_tools.pop(0)
                messages.append({"_type": "tool_call", "content": tool_name})
            is_err = (
                "error" in content[:50].lower() or "Error" in content[:50]
                or "denied" in content[:50].lower() or "BLOCKED" in content[:50]
            )
            messages.append({
                "_type": "tool_result",
                "content": content,
                "tool_name": name,
                "is_error": is_err,
            })

        elif t == "permission_denied":
            messages.append({"_type": "permission_denied", "content": ev.get('name', '?')})
        elif t == "error":
            messages.append({"_type": "error", "content": ev.get('message', '?')})

    if pending_tools:
        for tool in pending_tools:
            messages.append({"_type": "tool_call", "content": tool})
    return messages


def _load_from_session_store(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Load from new SessionStore format (role-based messages)."""
    messages: list[dict[str, Any]] = []

    for ev in events:
        role = ev.get("role", "")
        content = ev.get("content", "")

        if role == "user":
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    bt = block.get("type", "")
                    if bt == "tool_result":
                        result = block.get("content", "")[:500]
                        is_err = block.get("is_error", False)
                        messages.append({
                            "_type": "tool_result",
                            "content": str(result),
                            "is_error": is_err,
                        })
                    elif bt == "text":
                        messages.append({"_type": "user_input", "content": block.get("text", "")})
            elif isinstance(content, str):
                messages.append({"_type": "user_input", "content": content})
            elif content is not None:
                messages.append({"_type": "user_input", "content": str(content)})

        elif role == "assistant":
            if isinstance(content, list):
                # Anthropic format: text first, then tool calls (stream order)
                text_parts = []
                tool_blocks = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif block.get("type") == "tool_use":
                            name = block.get("name", "?")
                            inp = block.get("input", {})
                            tool_blocks.append(f"{name}({_fmt_tool_args(inp)})")
                if text_parts:
                    messages.append({"_type": "assistant_text", "content": "".join(text_parts)})
                for tb in tool_blocks:
                    messages.append({"_type": "tool_call", "content": tb})
            elif isinstance(content, str):
                if content and content.strip():
                    messages.append({"_type": "assistant_text", "content": content})
                tool_calls = ev.get("tool_calls", [])
                for tc in tool_calls:
                    if isinstance(tc, dict):
                        name = tc.get("function", {}).get("name", tc.get("name", "?"))
                        args_raw = tc.get("function", {}).get("arguments", "{}")
                        try:
                            args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                        except json.JSONDecodeError:
                            args = {}
                        args_str = _fmt_tool_args(args)
                        messages.append({"_type": "tool_call", "content": f"{name}({args_str})"})

        elif role == "tool":
            result = content[:500] if isinstance(content, str) else str(content)[:500]
            is_err = "error" in result[:50].lower() or "Permission" in result[:50]
            messages.append({"_type": "tool_result", "content": result, "is_error": is_err})

    return messages


def _workspace_dir_name(workspace: str) -> str:
    p = Path(workspace).resolve()
    # Normalize: remove colon, replace path separators AND spaces with underscore
    name = p.as_posix().replace(":", "").lstrip("/").replace(" ", "_").replace("/", "_")
    # Also collapse consecutive underscores
    while "__" in name:
        name = name.replace("__", "_")
    return name or "_default"


def find_latest_session(sessions_dir: Path, workspace: str = "") -> Path | None:
    """Return the most recent session for the given workspace."""
    sessions = list_sessions(sessions_dir, workspace)
    return sessions[0][0] if sessions else None


def list_sessions(sessions_dir: Path, workspace: str = "") -> list[tuple[Path, str, str]]:
    """Return [(path, name, last_ts), ...] sorted newest-first."""
    ws_name = _workspace_dir_name(workspace) if workspace else "_default"
    ws_dir = sessions_dir / ws_name
    if not ws_dir.is_dir():
        return []

    result: list[tuple[Path, str, str]] = []
    for f in sorted(ws_dir.glob("session-*.jsonl")):
        name = _session_name(f)
        if name == "(empty)":
            cleanup_empty(f)
            continue
        ts = _session_last_ts(f) or _session_file_ts(f)
        result.append((f, name, ts))
    result.sort(key=lambda x: x[0].stat().st_mtime, reverse=True)
    return result


def _session_last_ts(path: Path) -> str | None:
    last_ts: str | None = None
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    ev = json.loads(line.strip())
                    last_ts = ev.get("ts", last_ts)
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass
    if last_ts:
        try:
            dt = datetime.fromisoformat(last_ts)
            return dt.astimezone().strftime("%m-%d %H:%M")
        except (ValueError, OSError):
            pass
    return None


def _session_file_ts(path: Path) -> str:
    ts = path.stat().st_mtime
    dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()
    return dt.strftime("%m-%d %H:%M")


def _session_name(path: Path) -> str:
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    ev = json.loads(line.strip())
                    # Support both legacy SessionLogger format (type=user_input)
                    # and new SessionStore format (role=user)
                    if ev.get("type") == "user_input" or ev.get("role") == "user":
                        content = ev.get("content", "")
                        name = content.split("\n")[0].strip()
                        return name[:60] + ("..." if len(name) > 60 else "")
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass
    return "(empty)"


def cleanup_empty(path: Path) -> None:
    """Delete a session file if it has no user messages."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    ev = json.loads(line.strip())
                    # Support both legacy (type=user_input) and new (role=user) formats
                    if ev.get("type") == "user_input" or ev.get("role") == "user":
                        return
                except json.JSONDecodeError:
                    continue
        path.unlink(missing_ok=True)
    except Exception:
        pass


def load_session_transcript(path: Path, max_events: int = MAX_RESUME_EVENTS) -> str:
    """Read a JSONL session file and compress events into a compact transcript."""
    if not path.is_file():
        raise FileNotFoundError(f"Session file not found: {path}")

    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if len(events) > max_events:
        events = events[-max_events:]

    lines: list[str] = []
    for ev in events:
        t = ev.get("type", "?")
        if t == "user_input":
            lines.append(f"[user] {ev.get('content', '')}")
        elif t == "assistant_text":
            lines.append(f"[assistant] {ev.get('content', '')}")
        elif t == "tool_use":
            args = json.dumps(ev.get("args", {}), ensure_ascii=False)
            lines.append(f"[tool] {ev.get('name', '?')} {args}")
        elif t == "tool_result":
            result = ev.get("content", "")
            if len(result) > 300:
                result = result[:500] + "..."
            lines.append(f"[result] {ev.get('name', '?')}: {result}")
        elif t == "permission_denied":
            lines.append(f"[denied] {ev.get('name', '?')}")
        elif t == "error":
            lines.append(f"[error] {ev.get('message', '')}")
        else:
            lines.append(f"[{t}] {json.dumps(ev, ensure_ascii=False)[:200]}")

    transcript = "\n".join(lines)
    return (
        f"<previous_session file=\"{path.name}\">\n"
        f"The following is a transcript of a previous session. "
        f"Use it to understand what happened before, but do NOT re-execute commands.\n\n"
        f"{transcript}\n"
        f"</previous_session>"
    )
