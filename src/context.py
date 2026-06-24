"""Project context — builds system prompts, loads CLAUDE.md, compacts history.

Matches cc-mini's context builder with added skills/memory sections.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Base prompt (kept from original project, enhanced with cc-mini structure)
# ---------------------------------------------------------------------------

BASE_SYSTEM_PROMPT = """\
你是 Mini Claude Code，由 wxd 参考 Claude Code 设计制造的 vibe coding 工具。请始终用中文回复用户。

## 工作目录
你的工作目录是 {workspace}。
- read_file / write_file / list_files / search_files 的 path 参数相对于此目录
- run_shell 命令在此目录下执行，不需要 cd
- 所有文件操作都在此目录内

## 项目记忆
记忆系统帮你跨会话记住关键信息。每个记忆是独立文件，保存在 {memory_dir}。

当你发现重要信息时，直接用 write_file / edit_file 写入记忆目录：
- 每个记忆一个文件，用 frontmatter 格式（name、description、type）
- 然后在 MEMORY.md 添加索引行
- 记忆类型：user（用户偏好）、feedback（反馈）、project（项目状态）、reference（外部资源）

MEMORY.md 会被注入到后续所有对话的系统提示中。

## Shell 命令规则（防无限循环）
- 服务器/长运行命令：用 run_in_background=true，启动后立即返回。
  进程在后台运行，不需要等结果。不要杀进程，不要重启。
- 命令超过 15 秒会自动转入后台，这不是错误，进程还在跑。
- 绝对禁止 sleep 循环或轮询。命令失败就诊断根因，不要重试
- 如果必须 sleep，控制在 2 秒以内
- 优先用专用工具：read_file 不用 cat，write_file 不用 echo >，
  list_files 不用 ls，search_files 不用 grep/rg

## 行为规则
- 修改文件前先 read_file
- 修改后尽量 run_shell 跑测试
- 同一操作连续失败 2 次就停下来分析根因
- 不要连续 3 次读同一个文件不修改
- 危险操作要谨慎
- 改完用 git_diff 总结所有变更
- 最后用中文总结改了什么、验证了什么"""

# Limits
MAX_INSTRUCTIONS_CHARS = 8000
MEMORY_MAX_CHARS = 6000


def _read_text_safe(path: Path) -> str | None:
    """Read a file trying UTF-8 first, then GBK, then UTF-8 replace.
    Returns None if the file cannot be read at all.
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
# CLAUDE.md loading (kept from original project)
# ---------------------------------------------------------------------------

def load_project_instructions(workspace_dir: Path) -> str:
    """Walk from workspace_dir up to root, collecting CLAUDE.md files."""
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

    # Reversed: root-level first, workspace last (highest priority)
    return "\n\n".join(reversed(parts))


def _relative_label(path: Path, workspace: Path) -> str:
    """Human-readable label showing where a CLAUDE.md was found."""
    try:
        return str(path.relative_to(workspace))
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# Memory loading
# ---------------------------------------------------------------------------

def load_memory(memory_dir: Path | None = None) -> str:
    """Load MEMORY.md from the KAIROS memory directory."""
    if memory_dir is None:
        return ""
    from src.features.memory import load_memory_index
    content = load_memory_index(memory_dir)
    if not content:
        return ""
    return content


# ---------------------------------------------------------------------------
# System prompt builder (matches cc-mini pattern)
# ---------------------------------------------------------------------------

def build_system_prompt(
    cwd: str = "",
    model: str = "",
    memory_dir: Path | None = None,
    workspace_dir: Path | None = None,
    sessions_dir: Path | None = None,
) -> str:
    """Build the full system prompt: base + instructions + memory + skills.

    Matches cc-mini's build_system_prompt() pattern.
    """
    # Backward compat: workspace_dir takes precedence, fall back to cwd
    if workspace_dir is None:
        workspace_dir = Path(cwd) if cwd else Path.cwd()
    ws = workspace_dir.resolve() if isinstance(workspace_dir, str) else workspace_dir.resolve()

    # Memory path
    if memory_dir:
        mem_dir_str = str(memory_dir.resolve())
    elif sessions_dir:
        from src.session.logger import _workspace_dir_name
        ws_name = _workspace_dir_name(str(ws))
        mem_dir_str = str(sessions_dir / ws_name)
    else:
        mem_dir_str = "(not configured)"

    base = BASE_SYSTEM_PROMPT.format(
        workspace=str(ws),
        memory_dir=mem_dir_str,
    )
    parts = [base]

    # Project instructions (CLAUDE.md hierarchy)
    instructions = load_project_instructions(ws)
    if instructions:
        parts.append(f"<project_instructions>\n{instructions}\n</project_instructions>")

    # KAIROS memory
    memory = load_memory(memory_dir)
    if memory:
        parts.append(f"<project_memory>\n{memory}\n</project_memory>")

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Compaction
# ---------------------------------------------------------------------------

COMPACT_SYSTEM_PROMPT = """\
You are a conversation summarizer. Summarize the conversation segment below.
Include these sections:

## User Goal
(What the user asked for)

## Files Read
(Files that were read)

## Files Modified
(Files that were modified/created)

## Commands Run
(Shell commands that were executed and their results)

## Current Issues
(Any unresolved issues)

## Next Steps
(Recommended next steps)

Be concise."""


def micro_compact(
    messages: list[dict[str, Any]],
    keep_recent: int = 6,
) -> list[dict[str, Any]]:
    """Truncate old tool results in-place — zero cost, no API call.

    Only messages beyond *keep_recent* are affected. Tool-result content
    is replaced with ``[content truncated]``, keeping the structure intact.
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

    # Don't split a tool-call sequence
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

    # Strip orphaned tool messages at front
    while recent and recent[0].get("role") == "tool":
        recent = recent[1:]
    if not recent:
        return messages

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
