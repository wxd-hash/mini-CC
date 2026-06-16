"""Append-only JSONL session logger + resume helpers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class SessionLogger:
    """Writes structured session events to a timestamped JSONL file.

    Events are append-only — never overwritten.  Tool results are capped at
    *MAX_RESULT_CHARS* in the log.
    """

    MAX_RESULT_CHARS = 4000

    def __init__(self, path: Path, workspace: str = "") -> None:
        self.path = path
        self._file = path.open("a", encoding="utf-8")
        if workspace:
            self._write({"type": "session_start", "workspace": workspace})

    # ------------------------------------------------------------------
    # Event API (one method per event type)
    # ------------------------------------------------------------------

    def user_input(self, content: str) -> None:
        self._write({"type": "user_input", "content": content})

    def assistant_text(self, content: str) -> None:
        self._write({"type": "assistant_text", "content": content})

    def tool_use(self, name: str, args: dict[str, Any]) -> None:
        self._write({"type": "tool_use", "name": name, "args": args})

    def tool_result(self, name: str, content: str) -> None:
        if len(content) > self.MAX_RESULT_CHARS:
            content = content[: self.MAX_RESULT_CHARS] + (
                f"\n... [truncated at {self.MAX_RESULT_CHARS} chars in log]"
            )
        self._write({"type": "tool_result", "name": name, "content": content})

    def permission_denied(self, name: str) -> None:
        self._write({"type": "permission_denied", "name": name})

    def error(self, message: str) -> None:
        self._write({"type": "error", "message": message})

    def compact(self, events_before: int, events_after: int) -> None:
        self._write({
            "type": "compact",
            "events_before": events_before,
            "events_after": events_after,
        })

    def close(self) -> None:
        self._file.close()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _write(self, entry: dict[str, Any]) -> None:
        entry["ts"] = datetime.now(timezone.utc).isoformat()
        try:
            line = json.dumps(entry, ensure_ascii=False, default=str)
        except Exception:
            line = json.dumps({"type": "log_error", "message": "failed to serialize entry"})
        self._file.write(line + "\n")
        self._file.flush()


# ---------------------------------------------------------------------------
# Resume helpers (free functions, not bound to a SessionLogger instance)
# ---------------------------------------------------------------------------

MAX_RESUME_EVENTS = 200


def load_session_messages(path: Path, max_events: int = MAX_RESUME_EVENTS) -> list[dict[str, Any]]:
    """Reconstruct provider-agnostic messages from a session log.

    Returns a list of ``{"role": "user"|"assistant", "content": str}``
    messages that can be injected into any provider's history.
    """
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

    messages: list[dict[str, Any]] = []
    pending_tools: list[str] = []  # tool names waiting for results

    for ev in events:
        t = ev.get("type", "?")
        if t == "user_input":
            # Flush any pending tool entries first
            if pending_tools:
                messages.append({"role": "assistant", "content": f"[called: {', '.join(pending_tools)}]"})
                pending_tools.clear()
            messages.append({"role": "user", "content": ev.get("content", "")})

        elif t == "assistant_text":
            content = ev.get("content", "")
            if content.strip():
                if pending_tools:
                    messages.append({"role": "assistant", "content": f"[called: {', '.join(pending_tools)}]"})
                    pending_tools.clear()
                messages.append({"role": "assistant", "content": content})

        elif t == "tool_use":
            name = ev.get("name", "?")
            args = ev.get("args", {})
            args_str = json.dumps(args, ensure_ascii=False)
            pending_tools.append(f"{name}({args_str})")

        elif t == "tool_result":
            name = ev.get("name", "?")
            content = ev.get("content", "")
            if len(content) > 500:
                content = content[:500] + "..."
            # Emit as a user message so the LLM sees tool call + result as a pair
            tool_text = f"[tool] {name}\n→ {content}"
            if pending_tools:
                tool_text = f"{pending_tools.pop(0)}\n{tool_text}"
            messages.append({"role": "user", "content": tool_text})

        elif t in ("permission_denied", "error"):
            text = f"[{t}] {ev.get('name', '')} {ev.get('message', '')}".strip()
            messages.append({"role": "user", "content": text})

    # Flush remaining
    if pending_tools:
        messages.append({"role": "assistant", "content": f"[called: {', '.join(pending_tools)}]"})

    return messages


def _workspace_dir_name(workspace: str) -> str:
    p = Path(workspace).resolve()
    # Normalize: remove drive letter colon, replace separators with underscore
    name = p.as_posix().replace(":", "").lstrip("/").replace("/", "_")
    # Guard against empty result
    return name or "_default"


def find_latest_session(sessions_dir: Path, workspace: str = "") -> Path | None:
    """Return the most recent session for the given workspace."""
    sessions = list_sessions(sessions_dir, workspace)
    return sessions[0][0] if sessions else None


def list_sessions(sessions_dir: Path, workspace: str = "") -> list[tuple[Path, str, str]]:
    """Return ``[(path, name, last_ts), ...]`` sorted newest-first."""
    ws_name = _workspace_dir_name(workspace) if workspace else "_default"
    ws_dir = sessions_dir / ws_name
    if not ws_dir.is_dir():
        return []

    result: list[tuple[Path, str, str]] = []
    for f in sorted(ws_dir.glob("session-*.jsonl")):
        name = _session_name(f)
        if name == "(empty)":
            cleanup_empty(f)
            continue  # skip empty sessions
        stat = f.stat()
        ts = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime("%m-%d %H:%M")
        result.append((f, name, ts))
    result.sort(key=lambda x: x[0].stat().st_mtime, reverse=True)
    return result


def _session_name(path: Path) -> str:
    """Extract a short name from the first user message in the session."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    ev = json.loads(line.strip())
                    if ev.get("type") == "user_input":
                        content = ev.get("content", "")
                        # Use first line, truncate
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
                    if ev.get("type") == "user_input":
                        return  # has content, keep it
                except json.JSONDecodeError:
                    continue
        path.unlink(missing_ok=True)
    except Exception:
        pass


def load_session_transcript(path: Path, max_events: int = MAX_RESUME_EVENTS) -> str:
    """Read a JSONL session file and compress events into a compact transcript.

    Only the last *max_events* events are included.  The result is a single
    string suitable for injecting as a user message during resume.
    """
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

    # Take tail
    if len(events) > max_events:
        events = events[-max_events:]

    # Build transcript
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
                result = result[:300] + "..."
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
