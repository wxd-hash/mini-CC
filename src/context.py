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

## 规则
- 修改文件前先 read_file
- 修改后尽量 run_shell 测试
- 对危险操作要谨慎
- 修改完成后调用 git_diff 总结所有变更
- 最后总结改了什么、运行了什么验证命令"""

# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------

MAX_INSTRUCTIONS_CHARS = 8000


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_project_instructions(workspace_dir: Path) -> str:
    """Read CLAUDE.md from the workspace root (one file only, no recursion).

    Returns an empty string if the file does not exist.
    """
    path = workspace_dir / "CLAUDE.md"
    if not path.is_file():
        return ""

    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    if len(content) > MAX_INSTRUCTIONS_CHARS:
        content = content[:MAX_INSTRUCTIONS_CHARS] + (
            f"\n\n... [truncated at {MAX_INSTRUCTIONS_CHARS} chars]"
        )
    return content


def build_system_prompt(workspace_dir: Path) -> str:
    """Build the full system prompt, including CLAUDE.md if present."""
    base = BASE_SYSTEM_PROMPT.format(workspace=str(workspace_dir.resolve()))
    instructions = load_project_instructions(workspace_dir)
    if not instructions:
        return base

    return (
        base
        + f"\n\n<project_instructions>\n{instructions}\n</project_instructions>"
    )


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

    # Don't split a tool-call sequence: if the first kept message is a
    # 'tool' response, walk back to include its parent assistant message
    # (OpenAI requires every tool message to follow tool_calls).
    adjusted = cutoff
    while adjusted > 0 and messages[adjusted].get("role") == "tool":
        adjusted -= 1
    if adjusted < cutoff:
        # Find the assistant message that owns these tool calls
        parent = adjusted
        while parent > 0 and not (
            messages[parent].get("role") == "assistant"
            and messages[parent].get("tool_calls")
        ):
            parent -= 1
        if parent > 0:
            adjusted = parent

    old = messages[:adjusted]
    recent = messages[adjusted:]
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
