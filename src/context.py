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
- 绝对禁止 taskkill /IM python / killall python 等批量杀进程命令，
  会杀死 agent 自身。杀进程必须用 taskkill /PID <具体PID>。
- 绝对禁止 sleep 循环或轮询。命令失败就诊断根因，不要重试
- 如果必须 sleep，控制在 2 秒以内
- 优先用专用工具：read_file 不用 cat，write_file 不用 echo >，
  list_files 不用 ls，search_files 不用 grep/rg

## 行为规则
- 对于复杂多步任务，用 todo_write 拆分并追踪进度。每完成一个子任务立即标记为 completed，不要攒着批量更新
- 终端支持 Markdown 渲染（**粗体**、`代码`、# 标题等），可用但勿滥用
- 修改文件前先 read_file
- 修改后尽量 run_shell 跑测试
- 同一操作连续失败 2 次就停下来分析根因
- 不要连续 3 次读同一个文件不修改
- 危险操作要谨慎
- 改完用 git_diff 总结所有变更
- 如果你在对话中发现了新的项目约定、命令、陷阱或架构决策，
  用 edit_file 无感更新 CLAUDE.md——就像维护记忆一样，不需要特别告知用户。
  只写 Claude 不知道会出错的内容，不要写明显的或通用的规则

## 输出规范
- 第一次工具调用前，一句话说一下你要做什么。执行中途，卡点时简短更新进度。
- 不要叙述内部心路历程（"我来调用 Grep"、"用 run_shell 跑"）——用用户语言说你在干嘛。
- 用流畅的自然段写作。简单回答不要用标题和列表。只有并列项确实比段落更难读时才用 bullet，
  每个 bullet 至少 1-2 句话。不要滥用标题和列表。
- 创建或修改文件后，一句话说改了什么，不重复文件内容。跑完命令汇报结果，不重复解释命令。
- 引用代码位置时用 file_path:line_number 格式。
- 写完整句子，不加内部缩写和行话。假设用户回来捡起对话——能从零跟上。
- 只在用户明确要求时用 emoji。
- 别在工具调用开头加冒号（"我来读一下：" 应为 "我来读一下。"）。
- 任务完成直接汇报结果。禁止 "还有什么需要吗？""请告诉我" 之类套话。
- 说"完成"前先验证：测试跑通了吗？能执行吗？如果不能验证就明确说"未验证"，不假装完成。
- 最后用中文总结改了什么、验证了什么，精炼克制不啰嗦。

## Skills（技能调用）
你有可用的技能（Skills），每个技能是一段预设的专业提示词。
- 用 Skill 工具调用：Skill(name="技能名")，可选传 args 参数
- **主动调用**：当用户说"帮我 review 代码"、"提交一下"、"跑测试"、"简化代码"时，
  自己判断匹配哪个技能，立刻调用 Skill 工具，不要当普通请求处理
- 调用后你会收到该技能的完整指令，按指令逐步执行
- 不要编造技能名称——只使用下方 ## Available Skills 中列出的
- 用户也可以直接输入 /skill-name 手动触发，效果相同"""

# Limits
MAX_INSTRUCTIONS_CHARS = 8000
MEMORY_MAX_CHARS = 6000

PLAN_MODE_PROMPT = """\
## Plan 模式——强制只读探索

**重要：你现在处于 Plan 模式。你只能使用只读工具：read_file、list_files、search_files、git_diff。**

**绝对不要调用 write_file、edit_file、run_shell。这些工具在当前模式下被禁用，
调用它们只会得到 "Denied" 错误。** 你的唯一任务是探索代码库、设计方案、向用户呈现计划。

工作流程：
1. 用 search_files / list_files 探索项目结构和关键文件
2. 用 read_file 深入理解相关代码
3. 用 git_diff 查看已有改动（如果存在）
4. 分析后给出详细的执行计划
5. 必要时比较多个方案的优缺点
6. 计划呈现后等待用户切换到 ask/auto 模式再执行

用户的问题是一个需求描述。根据你的探索，给出一个完整的实施计划，
但**绝对不要**尝试创建文件、运行命令或其他任何修改操作。

输出格式：
## 分析
（你发现了什么，当前项目状态如何）

## 计划
（按步骤列出要做什么，每一步改哪个文件、怎么改、为什么）

## 风险
（可能的问题或需要注意的地方）"""


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
    mode: str = "ask",
) -> str:
    """Build the full system prompt: base + instructions + memory + skills.

    Matches cc-mini's build_system_prompt() pattern.
    """
    # Backward compat: workspace_dir takes precedence, fall back to cwd
    if workspace_dir is None:
        workspace_dir = Path(cwd) if cwd else Path.cwd()
    if isinstance(workspace_dir, str):
        workspace_dir = Path(workspace_dir)
    ws = workspace_dir.resolve()

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

    # Plan mode — inject exploration prompt, rebuilds on /perm plan
    if mode == "plan":
        parts.append(PLAN_MODE_PROMPT)

    # Project instructions (CLAUDE.md hierarchy)
    instructions = load_project_instructions(ws)
    if instructions:
        parts.append(f"<project_instructions>\n{instructions}\n</project_instructions>")
    else:
        # No CLAUDE.md yet — tell the model it can create one
        claude_md_path = ws / "CLAUDE.md"
        parts.append(
            f"<project_instructions>\n"
            f"在 {claude_md_path} 未找到 CLAUDE.md。运行 /init 自动生成一个，"
            f"或使用 edit_file/write_file 自行创建。CLAUDE.md 会被注入到每次会话中——"
            f"把项目规范、常用命令和注意事项放在里面。\n"
            f"</project_instructions>"
        )

    # KAIROS memory
    memory = load_memory(memory_dir)
    if memory:
        parts.append(f"<project_memory>\n{memory}\n</project_memory>")

    # Available skills — always include so model can call Skill tool
    from src.features.skills import build_skills_prompt_section
    skills_section = build_skills_prompt_section()
    if skills_section:
        parts.append(skills_section)

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Compaction
# ---------------------------------------------------------------------------

COMPACT_SYSTEM_PROMPT = """\
你是一个对话摘要生成器。请总结以下对话片段。

## 用户目标
用户要求了什么，应该完成什么。

## 文件与项目
- 被读取的文件及其关键发现（如 "app.py: Flask 应用有 3 个路由"）
- 被修改/创建的文件（如 "models.py: 给 User 添加了 email 字段"）
- 被搜索的文件及搜索原因

## 命令与结果
已执行的 shell 命令及其结果。只包含其结果仍然相关的命令——
跳过 `ls`、`mkdir` 等临时性命令。

## 错误与修复
遇到的错误及其解决方法。包含文件:行号的引用。

## 当前状态
目前哪些功能正常。哪些仍然有问题或未解决。做了哪些决策。

## 项目结构
本段对话中了解到的代码库关键信息：入口点、使用的框架、
数据库类型、测试运行器、配置格式等。

给出具体的路径和值。不要说"修复了一个 bug"——
要说具体是什么问题、用什么改动修复的。优先使用要列点而非段落。"""


# Tools whose results can be compacted (matches claude-code COMPACTABLE_TOOLS)
_COMPACTABLE_TOOLS = {
    "read_file", "list_files", "search_files", "git_diff",
    "run_shell", "write_file", "edit_file", "web_fetch",
}
# NOT compactable (always preserved): ask_user, todo_write, todo_update


def apply_tool_result_budget(
    messages: list[dict[str, Any]],
    tool_limits: dict[str, int],
) -> list[dict[str, Any]]:
    """Apply per-tool maxResultSizeChars before microcompact.

    Matches claude-code's applyToolResultBudget: large results are moved to
    temp files, and the message content is replaced with a preview + path.
    Tools without a limit are never affected.
    """
    import tempfile, os
    persisted_dir = None

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        tool_name = msg.get("_tool_name", "")
        limit = tool_limits.get(tool_name) if tool_name else None
        if limit is None:
            continue

        # Anthropic format: content is a list of blocks
        if role == "user" and isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    _budget_block(block, limit, tool_name)
        # OpenAI format: content is a string
        elif role == "tool" and isinstance(content, str):
            msg["content"] = _budget_string(content, limit, tool_name)

    return messages


def _budget_block(block: dict[str, Any], limit: int, tool_name: str) -> None:
    raw = block.get("content", "")
    if not isinstance(raw, str) or len(raw) <= limit:
        return
    preview = raw[:2000]
    block["content"] = (
        f"<persisted-output>\n"
        f"工具 {tool_name} 返回了 {len(raw)} 字符（限制: {limit}）。\n"
        f"预览（前 2000 字符）:\n{preview}\n"
        f"... [还有 {len(raw) - 2000} 字符被截断]\n"
        f"</persisted-output>"
    )


def _budget_string(raw: str, limit: int, tool_name: str) -> str:
    if len(raw) <= limit:
        return raw
    preview = raw[:2000]
    return (
        f"<persisted-output>\n"
        f"工具 {tool_name} 返回了 {len(raw)} 字符（限制: {limit}）。\n"
        f"预览（前 2000 字符）:\n{preview}\n"
        f"... [还有 {len(raw) - 2000} 字符被截断]\n"
        f"</persisted-output>"
    )


def micro_compact(
    messages: list[dict[str, Any]],
    keep_recent: int = 8,
    protected_window_s: float = 30.0,
) -> list[dict[str, Any]]:
    """Selective microcompact — matches claude-code's approach.

    Only compacts tools in _COMPACTABLE_TOOLS. Agent/Plan/Memory tool
    results are NEVER truncated.

    Uses time-based protection: messages within protected_window_s seconds
    of the most recent message are fully preserved, regardless of count.
    This matches claude-code's time-based MC config.
    """
    if len(messages) <= keep_recent:
        return messages

    # Time-based protection tail: don't compact recent messages
    import time as _time
    now = _time.monotonic()
    protected_count = 0
    for msg in reversed(messages):
        ts = msg.get("_ts", 0)
        if ts and now - ts < protected_window_s:
            protected_count += 1
        else:
            break
    protected_count = max(protected_count, keep_recent)

    for i in range(len(messages) - protected_count):
        msg = messages[i]
        role = msg.get("role", "")
        content = msg.get("content")

        # Anthropic: tool_results in user message content blocks
        if role == "user" and isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                # Only compact whitelisted tools
                tool_name = block.get("_tool_name", "")
                if tool_name and tool_name not in _COMPACTABLE_TOOLS:
                    continue
                if block.get("type") == "tool_result":
                    block["content"] = "[Old tool result content cleared]"

        # OpenAI: standalone tool messages
        elif role == "tool" and isinstance(content, str):
            tool_name = msg.get("_tool_name", "")
            if tool_name and tool_name not in _COMPACTABLE_TOOLS:
                continue
            msg["content"] = "[Old tool result content cleared]"

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
    result = [summary_msg] + recent
    # Clean orphaned tool messages at compact boundary
    # (matches claude-code: only runs at compaction, not every API call)
    result = _clean_compact_boundary(result)
    return result


def _clean_compact_boundary(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip orphaned tool messages at compact boundary (OpenAI/DeepSeek).

    After compaction, the summary_msg separates old and new messages.
    Any tool messages after the summary that reference tool_calls from
    the old (summarized) section are orphans and must be removed.

    This runs ONLY at compaction time, matching claude-code's
    buildPostCompactMessages pattern.
    """
    last_ids: set[str] = set()
    clean: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role", "")
        if role == "assistant" and m.get("tool_calls"):
            last_ids = {tc["id"] if isinstance(tc, dict) else tc.id
                        for tc in m["tool_calls"]}
            clean.append(m)
        elif role == "tool":
            tid = m.get("tool_call_id", "")
            if tid in last_ids:
                clean.append(m)
        else:
            last_ids = set()
            clean.append(m)
    return clean


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
