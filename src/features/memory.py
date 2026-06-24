"""KAIROS memory system — matches cc-mini's cross-session memory.

Features:
- Daily log: append-only file collecting <memory> tags extracted from AI output.
- Dream consolidation: periodic LLM-driven summarization of recent conversations.
- Lock file: prevents concurrent dream runs.
- MEMORY.md: consolidated memory index loaded into the system prompt.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _memory_dir(base: Path) -> Path:
    return base


def _daily_log_path(base: Path) -> Path:
    return base / "daily_log.md"


def _lock_path(base: Path) -> Path:
    return base / ".dream.lock"


def _consolidation_path(base: Path) -> Path:
    return base / ".last_consolidation.json"


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def ensure_memory_dir(base: Path) -> None:
    base.mkdir(parents=True, exist_ok=True)
    # Ensure MEMORY.md exists
    memory_index = base / "MEMORY.md"
    if not memory_index.exists():
        memory_index.write_text("# Project Memory\n\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Memory tag extraction
# ---------------------------------------------------------------------------

def extract_memory_tags(text: str) -> list[str]:
    """Extract <memory>...</memory> tags from assistant output.

    Matches cc-mini's pattern: the AI writes <memory>key facts</memory>
    in its response, and we persist them to the daily log.
    """
    import re
    pattern = re.compile(r"<memory>(.*?)</memory>", re.DOTALL | re.IGNORECASE)
    return [m.group(1).strip() for m in pattern.finditer(text)]


# ---------------------------------------------------------------------------
# Daily log
# ---------------------------------------------------------------------------

def append_to_daily_log(base: Path, entry: str) -> None:
    """Append a memory entry to the daily log with timestamp."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"\n## {ts}\n{entry}\n"
    path = _daily_log_path(base)
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Dream consolidation
# ---------------------------------------------------------------------------

def build_dream_prompt(
    base: Path,
    transcript_dir: str = "",
    session_ids: list[str] | None = None,
) -> str:
    """Build the prompt for dream consolidation.

    Reads the current daily log and any recent session transcripts,
    then asks the LLM to update MEMORY.md with consolidated learnings.
    """
    memory_index = base / "MEMORY.md"
    existing = memory_index.read_text(encoding="utf-8") if memory_index.exists() else ""

    daily_log = ""
    dl_path = _daily_log_path(base)
    if dl_path.exists():
        daily_log = dl_path.read_text(encoding="utf-8")

    transcript_text = ""
    if transcript_dir and session_ids:
        sessions_dir = Path(transcript_dir)
        parts = []
        for sid in session_ids[:10]:  # cap at 10 sessions
            for jf in sorted(sessions_dir.glob(f"session-{sid}*.jsonl")):
                try:
                    content = jf.read_text(encoding="utf-8")[:2000]
                    parts.append(f"<session id={sid}>\n{content}\n</session>")
                except Exception:
                    pass
        transcript_text = "\n\n".join(parts)

    return f"""You are a memory consolidation agent (Dream).

Below is the current MEMORY.md index, recent daily log entries,
and session transcripts. Update MEMORY.md to incorporate new learnings.

## Current MEMORY.md
{existing[:4000]}

## Daily Log
{daily_log[:3000]}

## Session Transcripts
{transcript_text[:3000]}

## Instructions
1. Read the current MEMORY.md and the new content above.
2. Update MEMORY.md: add new entries, remove outdated ones, merge duplicates.
3. Use the EXACT format from the existing MEMORY.md (same markdown structure).
4. Write the FULL updated MEMORY.md content using the write_file tool.
   path = "{memory_index}"
5. Keep it concise — only information useful to future coding sessions.
"""


def should_auto_dream(
    base: Path,
    min_hours: float = 24.0,
    min_sessions: int = 5,
    current_session_id: str = "",
    sessions_dir: str | None = None,
) -> bool:
    """Check if auto-dream consolidation should trigger.

    Returns True if enough time and sessions have passed since last dream.
    """
    last = read_last_consolidated_at(base)
    if last is None:
        # Never dreamed — check if enough sessions exist
        if sessions_dir:
            try:
                sd = Path(sessions_dir)
                count = len(list(sd.glob("session-*.jsonl")))
                return count >= min_sessions
            except Exception:
                pass
        return False

    elapsed_hours = (time.time() - last) / 3600.0
    if elapsed_hours < min_hours:
        return False

    if sessions_dir:
        try:
            sd = Path(sessions_dir)
            new_count = 0
            for jf in sd.glob("session-*.jsonl"):
                if jf.stat().st_mtime > last:
                    new_count += 1
            return new_count >= min_sessions
        except Exception:
            pass

    return elapsed_hours >= min_hours


# ---------------------------------------------------------------------------
# Lock file (prevents concurrent dreams)
# ---------------------------------------------------------------------------

def try_acquire_lock(base: Path) -> bool:
    """Try to acquire the dream lock. Returns True if acquired."""
    lp = _lock_path(base)
    if lp.exists():
        # Check if lock is stale (> 30 min)
        try:
            age = time.time() - lp.stat().st_mtime
            if age > 1800:
                lp.unlink()
            else:
                return False
        except OSError:
            return False
    try:
        lp.write_text(str(os.getpid()))
        return True
    except OSError:
        return False


def release_lock(base: Path) -> None:
    """Release the dream lock."""
    lp = _lock_path(base)
    try:
        lp.unlink(missing_ok=True)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Consolidation tracking
# ---------------------------------------------------------------------------

def read_last_consolidated_at(base: Path) -> float | None:
    """Return timestamp of last successful consolidation, or None."""
    cp = _consolidation_path(base)
    if not cp.exists():
        return None
    try:
        data = json.loads(cp.read_text(encoding="utf-8"))
        return float(data.get("timestamp", 0)) or None
    except Exception:
        return None


def record_consolidation(base: Path) -> None:
    """Record that a consolidation just completed."""
    cp = _consolidation_path(base)
    try:
        cp.write_text(json.dumps({"timestamp": time.time()}))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Session listing for dream
# ---------------------------------------------------------------------------

def list_sessions_since(
    since_timestamp: float | None,
    sessions_dir: str | None = None,
    current_session_id: str = "",
) -> list[str]:
    """List session IDs created/modified since the given timestamp."""
    if not sessions_dir:
        return [current_session_id]
    sd = Path(sessions_dir)
    if not sd.is_dir():
        return [current_session_id]

    ids = set()
    cutoff = since_timestamp or 0
    for jf in sorted(sd.glob("session-*.jsonl")):
        try:
            if jf.stat().st_mtime > cutoff:
                sid = jf.stem.replace("session-", "")
                ids.add(sid)
        except OSError:
            continue
    if current_session_id:
        ids.add(current_session_id)
    return list(ids)
