"""KAIROS Memory system — matches claude-code extractMemories sub-agent pattern.

Storage format (matches claude-code):
  ~/.config/mini-claude/memory/
  ├── MEMORY.md              ← index of pointers, loaded into system prompt
  ├── user_role.md            ← one file per memory with YAML frontmatter
  ├── feedback_testing.md
  └── ...

Memory types: user, feedback, project, reference

Flow:
1. System prompt tells main agent it can write to memory/ with WriteFile/EditFile
2. After each turn, if main agent didn't write, a background sub-agent extracts
   memories from the conversation transcript
3. Memory files use YAML frontmatter for metadata
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Memory file frontmatter format (matches claude-code)
# ---------------------------------------------------------------------------

MEMORY_FRONTMATTER = """\
---
name: {{name}}
description: {{description}}
metadata:
  type: {{type}}
---
"""

MEMORY_TYPES = {
    "user":      "用户角色、偏好、知识背景",
    "feedback":  "用户给的反馈：要做什么、不要做什么",
    "project":   "项目状态：谁在做什么、为什么、截止日期",
    "reference": "外部资源：Bug 追踪在哪、文档在哪、Slack 频道",
}

WHAT_NOT_TO_SAVE = """\
## What NOT to save
- Code patterns, conventions, architecture — these live in the code
- Git history or recent changes — `git log` is authoritative
- Debugging solutions — the fix is in the code
- Anything already in CLAUDE.md files
- Ephemeral task details: in-progress work, temporary state
"""

EXTRACTION_PROMPT = """\
You are the memory extraction agent. Analyze the conversation above and extract durable memories.

## Memory types
{memory_types}

## How to save memories

Each memory goes in its own file under {memory_dir}. Use this frontmatter format:

```markdown
---
name: short-kebab-case-slug
description: one-line summary for future relevance checks
metadata:
  type: {types_list}
---

(memory content — for feedback/project: rule/fact, then **Why:** and **How to apply:**)
```

Then add a pointer to MEMORY.md: `- [Title](file.md) — one-line hook`

{what_not_to_save}

## Strategy
Turn 1: read existing MEMORY.md and any files you might update (parallel reads)
Turn 2: write new/updated memory files + update MEMORY.md index (parallel writes)
Do NOT waste turns investigating — only use conversation content above.
"""

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _memory_dir(base: str | Path) -> Path:
    return Path(base)


def _index_path(base: Path) -> Path:
    return base / "MEMORY.md"


def _lock_path(base: Path) -> Path:
    return base / ".extraction.lock"


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def ensure_memory_dir(base: str | Path) -> None:
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    idx = _index_path(base)
    if not idx.exists():
        idx.write_text("# Project Memory\n\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Memory index loading (for system prompt injection)
# ---------------------------------------------------------------------------

def load_memory_index(base: str | Path) -> str:
    """Load MEMORY.md as the memory index for system prompt injection.

    Lines after 200 are truncated (matches claude-code's behavior).
    """
    idx = _index_path(Path(base))
    if not idx.exists():
        return ""
    content = idx.read_text(encoding="utf-8")
    lines = content.split("\n")
    if len(lines) > 200:
        content = "\n".join(lines[:200]) + "\n... [truncated]"
    return content


def scan_memory_files(base: str | Path) -> str:
    """Build a manifest of existing memory files for the extraction agent."""
    base = Path(base)
    if not base.is_dir():
        return ""
    parts = []
    for f in sorted(base.glob("*.md")):
        if f.name == "MEMORY.md":
            continue
        content = f.read_text(encoding="utf-8")[:200]
        parts.append(f"### {f.name}\n{content}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Memory extraction sub-agent (matches claude-code extractMemories)
# ---------------------------------------------------------------------------

def run_memory_extraction(
    engine: Any,
    memory_dir: str | Path,
    messages: list[dict[str, Any]],
    provider_factory: Any = None,
    model: str = "",
) -> None:
    """Run memory extraction sub-agent in background thread.

    Matches claude-code's extractMemories pattern:
    - Runs after each turn (non-blocking)
    - Only if main agent didn't already write to memory dir
    - Uses a forked engine with limited tools
    - Reads existing memories, writes/updates as needed
    """
    _start_extraction_thread(engine, Path(memory_dir), messages, provider_factory, model)


def _start_extraction_thread(
    main_engine: Any,
    memory_dir: Path,
    messages: list[dict[str, Any]],
    provider_factory: Any,
    model: str,
) -> None:
    """Launch extraction in a background daemon thread."""
    # Check if main agent already wrote to memory this turn
    if _main_agent_already_wrote(messages, memory_dir):
        return

    # Check if enough new content to warrant extraction
    if not _should_extract(messages, memory_dir):
        return

    # Lock to prevent concurrent extractions
    if not _try_acquire_lock(memory_dir):
        return

    def _run():
        try:
            _do_extraction(main_engine, memory_dir, messages, provider_factory, model)
        except Exception:
            pass
        finally:
            _release_lock(memory_dir)

    threading.Thread(target=_run, daemon=True).start()


def _main_agent_already_wrote(messages: list[dict[str, Any]], memory_dir: Path) -> bool:
    """Check if main agent already wrote to memory files this turn.

    Matches claude-code's hasMemoryWritesSince().
    """
    memory_dir_str = str(memory_dir.resolve())
    for msg in messages[-10:]:  # Check last 10 messages
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            for block in content:
                if block.get("type") == "tool_use":
                    fp = (block.get("input", {}) or {}).get("file_path", "")
                    if memory_dir_str in str(fp):
                        return True
        # Also check text for memory file references
        if isinstance(content, str) and memory_dir_str in content:
            return True
    return False


def _should_extract(messages: list[dict[str, Any]], memory_dir: Path) -> bool:
    """Check if there's enough new content to warrant extraction.

    Matches claude-code's minimal message count check.
    """
    # Count model-visible messages since last extraction
    visible_count = sum(
        1 for m in messages
        if m.get("role") in ("user", "assistant")
    )
    return visible_count >= 3  # At least 3 exchanges


def _do_extraction(
    main_engine: Any,
    memory_dir: Path,
    messages: list[dict[str, Any]],
    provider_factory: Any,
    model: str,
) -> None:
    """Run the actual extraction: build prompt, submit to sub-agent engine.

    The sub-agent gets Read/Write/Edit tools restricted to memory_dir.
    """
    from src.tools.file_tools import ReadFile, WriteFile, FileEditTool
    from src.tools.registry import ToolRegistry
    from src.security.permission import PermissionChecker
    from src.context import build_system_prompt

    # Build the extraction prompt
    existing = scan_memory_files(memory_dir)
    type_descs = "\n".join(f"- **{k}**: {v}" for k, v in MEMORY_TYPES.items())
    types_list = ", ".join(MEMORY_TYPES.keys())

    prompt = EXTRACTION_PROMPT.format(
        memory_dir=str(memory_dir.resolve()),
        memory_types=type_descs,
        types_list=types_list,
        what_not_to_save=WHAT_NOT_TO_SAVE,
    )

    # Convert recent messages to transcript text
    transcript = _messages_to_transcript(messages[-20:])  # Last 20 messages

    full_prompt = (
        f"{prompt}\n\n"
        f"## Existing memories\n{existing if existing else '(none yet)'}\n\n"
        f"## Conversation transcript\n{transcript}"
    )

    # Build sub-agent engine with restricted tools
    if provider_factory:
        provider = provider_factory()
    else:
        provider = main_engine._provider

    # Restricted tools: only Read/Write/Edit, and only for memory dir
    sub_tools = [
        ReadFile(Path("/")),  # read anywhere needed
        WriteFile(memory_dir),
        FileEditTool(memory_dir),
    ]
    sub_registry = ToolRegistry()
    for t in sub_tools:
        sub_registry.register(t)

    sub_permissions = PermissionChecker(auto_approve=True)
    sub_prompt = build_system_prompt(cwd=str(memory_dir))

    from src.agent.loop import Engine
    sub_engine = Engine(
        tools=sub_tools,
        system_prompt=sub_prompt,
        permission_checker=sub_permissions,
        provider=provider,
        model=model or main_engine.get_model(),
        max_tokens=main_engine._max_tokens,
        tool_registry=sub_registry,
        workspace_dir=memory_dir,
    )

    # Run the extraction
    sub_engine.run(full_prompt)

    # Record extraction timestamp
    _record_extraction(memory_dir)


def _messages_to_transcript(messages: list[dict[str, Any]]) -> str:
    """Convert messages to a compact transcript for the extraction agent."""
    lines = []
    for msg in messages:
        role = msg.get("role", "?")
        content = msg.get("content", "")
        if isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", "")[:300])
                    elif block.get("type") == "tool_use":
                        text_parts.append(f"[tool:{block.get('name', '?')}]")
            content = " ".join(text_parts)
        elif isinstance(content, str):
            content = content[:500]
        else:
            content = str(content)[:300]
        lines.append(f"[{role}] {content}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Lock file
# ---------------------------------------------------------------------------

def _try_acquire_lock(base: Path) -> bool:
    lp = _lock_path(base)
    if lp.exists():
        try:
            age = time.time() - lp.stat().st_mtime
            if age > 600:  # stale lock > 10 min
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


def _release_lock(base: Path) -> None:
    try:
        _lock_path(base).unlink(missing_ok=True)
    except OSError:
        pass


def _record_extraction(base: Path) -> None:
    cp = base / ".last_extraction.json"
    try:
        cp.write_text(json.dumps({"timestamp": time.time()}))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Legacy compat (keep old API working)
# ---------------------------------------------------------------------------

def extract_memory_tags(text: str) -> list[str]:
    """Legacy: extract <memory> tags from text (still supported)."""
    pattern = re.compile(r"<memory>(.*?)</memory>", re.DOTALL | re.IGNORECASE)
    return [m.group(1).strip() for m in pattern.finditer(text)]


def append_to_daily_log(base: Path, entry: str) -> None:
    """Legacy: append to daily_log.md (still works alongside new system)."""
    path = Path(base) / "daily_log.md"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(f"\n## {ts}\n{entry}\n")
    except Exception:
        pass
