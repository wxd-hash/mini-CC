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
## 什么不要保存
- 代码模式、规范、架构——这些在代码中就能看到
- Git 历史或近期变更——`git log` 才是权威
- 调试解决方案——修复在代码里
- 任何已经在 CLAUDE.md 中的内容
- 临时任务细节：进行中的工作、临时状态
"""

EXTRACTION_PROMPT = """\
你是记忆提取 Agent。分析上述对话并提取持久记忆。

## 记忆类型
{memory_types}

## 如何保存记忆

每条记忆保存在 {memory_dir} 下的独立文件中。使用以下 frontmatter 格式：

```markdown
---
name: 简短-kebab-case-名称
description: 一行摘要，用于将来的相关性判断
metadata:
  type: {types_list}
last_updated: YYYY-MM-DD
---

（记忆内容——对于 feedback/project 类型：规则/事实，然后是**原因：**和**如何应用：**）
```

然后在 MEMORY.md 中添加一行索引：`- [标题](file.md) — 一行摘要`

## 更新规则

- 如果记忆过时或错误，原地更新（刷新 mtime）
- 如果记忆不再相关，从 MEMORY.md 中移除但保留文件
- MEMORY.md 索引控制在 50 行以内——必要时删减最不重要的条目
- 时间戳显示每个文件的最后修改时间——用于判断新鲜度

{what_not_to_save}

## CLAUDE.md 维护

如果工作区根目录存在 CLAUDE.md，也检查一下对话中是否揭示了新的项目知识
应该放入其中：不易发现的命令、架构决策、测试规范、陷阱、代码风格规则。
如果需要，通过 edit_file 更新它。CLAUDE.md 会被注入到每次会话的
系统提示中——保持简洁且项目专用，不要写通用的。

## 策略
第 1 轮：读取 MEMORY.md、已有的记忆文件、CLAUDE.md（并行读取）
第 2 轮：写入新的/更新的记忆文件 + 更新 MEMORY.md 索引 + 更新 CLAUDE.md（如果需要）（并行写入）
不要浪费轮次去调查——只使用上述对话内容。
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

def _read_text_safe(path: Path) -> str | None:
    """Read a text file with encoding fallback (UTF-8 → GBK → UTF-8 replace)."""
    for enc in ("utf-8", "gbk"):
        try:
            return path.read_text(encoding=enc)
        except (OSError, ValueError, UnicodeDecodeError):
            continue
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def memory_age_days(mtime: float) -> int:
    """Days since file modification. 0=today, 1=yesterday, 2+=older."""
    return max(0, int((time.time() - mtime) / 86400))


def memory_freshness_warning(filepath: Path) -> str:
    """Staleness warning for memories >1 day old (matches claude-code memoryFreshnessText).

    Returns empty string for fresh memories. For old ones, returns a
    <system-reminder> note telling the model to verify the memory.
    """
    try:
        mtime = filepath.stat().st_mtime
    except OSError:
        return ""
    days = memory_age_days(mtime)
    if days <= 1:
        return ""
    return (
        f"此记忆已有 {days} 天。"
        f"记忆是时间点快照，不是实时状态——"
        f"关于代码行为的断言可能已过时。"
        f"在作为事实使用前请验证当前代码。"
    )


def load_memory_index(base: str | Path) -> str:
    """Load MEMORY.md and referenced memory files into system prompt.

    Lines after 200 are truncated. Memories >1 day old get staleness
    warnings injected (matching claude-code).
    """
    base = Path(base)
    idx = _index_path(base)
    if not idx.exists():
        return ""
    content = _read_text_safe(idx)
    if not content:
        return ""

    # Inject staleness warnings for linked memory files
    import re
    result_lines = []
    for line in content.split("\n"):
        result_lines.append(line)
        # Check if this line links to a memory file
        m = re.search(r'\[([^\]]+)\]\(([^)]+\.md)\)', line)
        if m:
            mem_file = base / m.group(2)
            if mem_file.exists():
                warning = memory_freshness_warning(mem_file)
                if warning:
                    result_lines.append(f"  [STALE: {warning}]")

    content = "\n".join(result_lines)
    if len(result_lines) > 200:
        content = "\n".join(result_lines[:200]) + "\n... [truncated]"
    return content


def scan_memory_files(base: str | Path) -> str:
    """Build a manifest of existing memory files for the extraction agent.

    Includes mtime so the agent can judge freshness (matching claude-code).
    """
    base = Path(base)
    if not base.is_dir():
        return ""
    # Sort by mtime, newest first (matches claude-code)
    files = sorted(
        [f for f in base.glob("*.md") if f.name != "MEMORY.md"],
        key=lambda f: f.stat().st_mtime if f.exists() else 0,
        reverse=True,
    )[:200]  # Cap at 200 (matches claude-code MAX_MEMORY_FILES)
    parts = []
    for f in files:
        try:
            age = memory_age_days(f.stat().st_mtime)
            age_str = "today" if age == 0 else f"{age}d ago"
        except OSError:
            age_str = "unknown"
        content = _read_text_safe(f)
        if content:
            parts.append(f"### {f.name} (modified: {age_str})\n{content[:200]}")
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
    trace = getattr(main_engine, '_trace', None)

    # Check if main agent already wrote to memory this turn
    if _main_agent_already_wrote(messages, memory_dir):
        if trace:
            trace.memory_extract(skipped=True, reason="main_agent_wrote")
        return

    # Check if enough new content to warrant extraction
    if not _should_extract(messages, memory_dir):
        if trace:
            trace.memory_extract(skipped=True, reason="not_enough_content")
        return

    # Lock to prevent concurrent extractions
    if not _try_acquire_lock(memory_dir):
        if trace:
            trace.memory_extract(skipped=True, reason="locked")
        return

    def _run():
        _ts = time.time()
        _cleaned = 0
        try:
            _cleaned = _cleanup_orphaned_files(memory_dir)
            _do_extraction(main_engine, memory_dir, messages, provider_factory, model)
        except Exception:
            pass
        finally:
            _release_lock(memory_dir)
            if trace:
                trace.memory_extract(
                    cleaned=_cleaned,
                    skipped=False,
                    elapsed_ms=(time.time() - _ts) * 1000,
                )

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


def _cleanup_orphaned_files(memory_dir: Path) -> int:
    """Remove .md files not referenced in MEMORY.md index. Returns count removed."""
    idx = _index_path(memory_dir)
    if not idx.exists():
        return 0
    content = _read_text_safe(idx)
    if not content:
        return 0
    # Extract all linked filenames from MEMORY.md
    import re
    linked = set()
    for m in re.finditer(r'\[.*?\]\(([^)]+)\)', content):
        linked.add(m.group(1))
    # Delete .md files not in the index
    removed = 0
    for f in memory_dir.glob("*.md"):
        if f.name == "MEMORY.md":
            continue
        if f.name not in linked:
            try:
                f.unlink()
                removed += 1
            except OSError:
                pass
    return removed


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

    # Run the extraction silently (suppress terminal output)
    import sys, io
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        sub_engine.run(full_prompt, quiet=True)
    finally:
        sys.stdout = old_stdout

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
