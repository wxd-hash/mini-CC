"""Project context — reads CLAUDE.md, builds system prompts, and compacts history."""

from __future__ import annotations

from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Base prompt (always included)
# ---------------------------------------------------------------------------

BASE_SYSTEM_PROMPT = """\
你是 Mini Claude Code，一个终端 coding agent。

## 重要：工作目录
你的 workspace 根目录是 {workspace}。
- read_file / write_file / list_files / search_files 的 path 参数都是相对于此目录
- run_shell 的命令会在此目录下执行，不需要 cd
- 所有文件操作都在此目录内

## 项目记忆
项目的 <project_memory> 区块包含跨 session 持久化的记忆：用户偏好、架构决策、常见陷阱。
当你发现重要的用户偏好、架构决策或项目陷阱时，用 write_file 更新记忆文件：
  path = "{memory_path}"
写入时保留已有内容，添加新条目。如果文件不存在就创建。

## 规则
- 修改文件前先 read_file
- 修改后尽量 run_shell 测试
- 如果同一个操作连续失败 2 次，停下来分析原因，不要继续重试
- 不要连续 3 次读同一个文件不修改
- 对危险操作要谨慎
- 修改完成后调用 git_diff 总结所有变更
- 最后总结改了什么、运行了什么验证命令"""

# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------

MAX_INSTRUCTIONS_CHARS = 8000
MEMORY_MAX_CHARS = 6000


def _read_text_safe(path: Path) -> str | None:
    """Read a file trying UTF-8 first, then GBK, then UTF-8 replace.
    Returns ``None`` if the file cannot be read at all.
    """
    for enc in ("utf-8", "gbk"):
        try:
            return path.read_text(encoding=enc)
        except (OSError, ValueError):
            continue
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_project_instructions(workspace_dir: Path) -> str:
    """Walk from *workspace_dir* up to the filesystem root, collecting
    ``CLAUDE.md``, ``.claude/CLAUDE.md``, and ``CLAUDE.local.md`` at each
    level.

    Files closer to the workspace appear later and can override earlier
    (higher-level) instructions.  Each file is capped at
    ``MAX_INSTRUCTIONS_CHARS`` characters individually.
    """
    parts: list[str] = []
    seen: set[str] = set()
    ws = workspace_dir.resolve()

    for parent in [ws, *ws.parents]:
        for name in ("CLAUDE.md", ".claude/CLAUDE.md", "CLAUDE.local.md"):
            path = parent / name
            key = str(path.resolve())
            if not path.is_file() or key in seen:
                continue
            seen.add(key)
            content = _read_text_safe(path)
            if content is None:
                continue
            if len(content) > MAX_INSTRUCTIONS_CHARS:
                content = content[:MAX_INSTRUCTIONS_CHARS] + (
                    f"\n\n... [truncated at {MAX_INSTRUCTIONS_CHARS} chars]"
                )
            label = _relative_label(path, ws)
            parts.append(f"<!-- {label} -->\n{content}")

    # Reversed: root-level instructions first, workspace-last (highest priority)
    return "\n\n".join(reversed(parts))


def _relative_label(path: Path, workspace: Path) -> str:
    """Human-readable label showing where a CLAUDE.md was found."""
    try:
        return str(path.relative_to(workspace))
    except ValueError:
        return str(path)


def load_memory(workspace_dir: Path, sessions_dir: Path | None = None) -> str:
    """Load the project memory file (cross-session persistent preferences)."""
    if sessions_dir is None:
        return ""
    from src.session.logger import _workspace_dir_name
    ws_name = _workspace_dir_name(str(workspace_dir.resolve()))
    memory_path = sessions_dir / ws_name / "memory.md"
    if not memory_path.is_file():
        return ""
    content = _read_text_safe(memory_path)
    if content is None:
        return ""
    if len(content) > MEMORY_MAX_CHARS:
        content = content[:MEMORY_MAX_CHARS] + "\n... [truncated]"
    return content


def build_system_prompt(
    workspace_dir: Path,
    sessions_dir: Path | None = None,
) -> str:
    """Build the full system prompt: base + instructions + memory."""
    from src.session.logger import _workspace_dir_name
    ws_name = _workspace_dir_name(str(workspace_dir.resolve()))
    memory_path = f"{sessions_dir / ws_name / 'memory.md'}" if sessions_dir else "(sessions dir not configured)"
    base = BASE_SYSTEM_PROMPT.format(
        workspace=str(workspace_dir.resolve()),
        memory_path=memory_path,
    )
    parts = [base]

    instructions = load_project_instructions(workspace_dir)
    if instructions:
        parts.append(f"<project_instructions>\n{instructions}\n</project_instructions>")

    memory = load_memory(workspace_dir, sessions_dir)
    if memory:
        parts.append(f"<project_memory>\n{memory}\n</project_memory>")

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Compaction
# ---------------------------------------------------------------------------

COMPACT_SYSTEM_PROMPT = """\
You are a conversation summarizer. Summarize the conversation segment below.
Include these sections:

## 用户目标
(What the user asked for)

## 已读文件
(Files that were read)

## 已修改文件
(Files that were modified/created)

## 已运行命令
(Shell commands that were executed and their results)

## 当前问题
(Any unresolved issues)

## 下一步建议
(Recommended next steps)

Be concise. Use Chinese if the conversation is in Chinese."""


def micro_compact(
    messages: list[dict[str, Any]],
    keep_recent: int = 6,
) -> list[dict[str, Any]]:
    """Truncate old tool results in-place — zero cost, no API call.

    Only messages beyond *keep_recent* are affected.  Tool-result content
    is replaced with ``[content truncated]``, keeping the structure intact
    so both Anthropic and OpenAI providers stay happy.
    """
    if len(messages) <= keep_recent:
        return messages

    for i in range(len(messages) - keep_recent):
        msg = messages[i]
        role = msg.get("role", "")
        content = msg.get("content")

        # Anthropic: tool_results live inside a user message's content block list
        if role == "user" and isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    block["content"] = "[content truncated]"

        # OpenAI: tool results are standalone role="tool" messages
        elif role == "tool" and isinstance(content, str):
            msg["content"] = "[content truncated]"

    return messages


def compact_messages(
    provider: Any,
    system_prompt: str,
    messages: list[dict[str, Any]],
    keep_recent: int = 10,
) -> list[dict[str, Any]]:
    """Summarize old messages via LLM, return [summary_msg, ...recent].

    On failure, return just the *keep_recent* tail without a summary.
    """
    if len(messages) <= keep_recent:
        return messages

    cutoff = len(messages) - keep_recent

    # Don't split a tool-call sequence: walk back to include the parent
    # assistant message that owns any leading tool responses.
    adjusted = cutoff
    while adjusted > 0 and messages[adjusted].get("role") == "tool":
        adjusted -= 1
    if adjusted < cutoff:
        parent = adjusted
        while parent > 0 and not (
            messages[parent].get("role") == "assistant"
            and messages[parent].get("tool_calls")
        ):
            parent -= 1
        adjusted = max(parent, 0)

    recent = messages[adjusted:]

    # Belt-and-suspenders: strip any remaining orphaned tool messages
    # at the front of recent (edge case when adjusted lands on 0).
    while recent and recent[0].get("role") == "tool":
        recent = recent[1:]
    if not recent:
        return messages  # nothing left — abort compaction

    old = messages[: messages.index(recent[0])]
    old_text = _messages_to_text(old)

    req_msg = provider.make_user_message(f"Summarize:\n\n{old_text}")
    try:
        summary = provider.compact(COMPACT_SYSTEM_PROMPT, [req_msg])
    except Exception:
        return recent

    summary_msg = provider.make_compaction_summary_message(summary)
    return [summary_msg] + recent


def _messages_to_text(messages: list[dict[str, Any]]) -> str:
    """Flatten provider-native messages into a readable text block."""
    lines: list[str] = []
    for msg in messages:
        role = msg.get("role", "?")
        content = msg.get("content")

        if content is None:
            # OpenAI tool_calls message
            for tc in msg.get("tool_calls", []):
                fn = tc.get("function", {})
                lines.append(f"[tool:{fn.get('name', '?')}] {fn.get('arguments', '')}")
            continue

        if role == "tool":
            c = str(content)
            lines.append(f"[result] {c[:200]}")
            continue

        if isinstance(content, str):
            lines.append(f"[{role}] {content}")
            continue

        if isinstance(content, list):
            for block in content:
                t = block.get("type", "?")
                if t == "text":
                    lines.append(f"[{role}] {block.get('text', '')}")
                elif t == "tool_use":
                    lines.append(f"[tool:{block.get('name', '?')}] {_brief(block.get('input', {}))}")
                elif t == "tool_result":
                    c = str(block.get("content", ""))
                    lines.append(f"[result] {c[:200]}")
    return "\n".join(lines)


def _brief(obj: Any) -> str:
    s = str(obj)
    return s if len(s) <= 100 else s[:97] + "..."
