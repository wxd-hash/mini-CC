# Claude Code 内部流程详解

本文档描述 Claude Code 从用户输入一句话到最终回复的完整内部流程。尽量用自然语言描述，关键位置标注代码路径。

---

## 一、整体架构

```
用户输入 → REPL → QueryEngine → query() → queryLoop() → 模型 API → 工具执行 → 循环直到完成
```

核心文件就是 `src/query.ts`。它导出一个 `query()` 异步生成器，里面委托给 `queryLoop()`——一个 `while(true)` 无限循环，每次迭代做五件事：微压缩、自动压缩、API 调用、工具执行、后处理。模型没调用工具就结束，调了工具就把结果反馈回去继续。

---

## 二、以 "帮我创建 hello.py" 为例

### 2.1 用户输入到达

终端里输入完按回车，`PromptInput` 组件把字符串传给上层。先检查是不是斜杠命令（`/exit`、`/compact` 等）——不是，就当作普通消息提交给 `QueryEngine`。

### 2.2 构建系统提示词

**关键文件**：`src/constants/prompts.ts`、`src/context.ts`

系统提示词分成两部分，中间用 `SYSTEM_PROMPT_DYNAMIC_BOUNDARY` 标记分界：

**静态部分**（可被 Anthropic prompt cache 跨请求缓存，省 token）：
- Claude Code 的身份定义和行为约束
- 所有工具的详细使用说明（BashTool、FileEdit、FileRead、FileWrite 等各自一大段 prompt）
- 安全规则（禁止 sleep 循环、禁止杀进程、权限模式说明）
- 输出格式要求（不报工具名、不客套、不滥用标题列表）

**动态部分**（每轮重新构建，不能缓存）：
- 当前工作目录
- 日期和时间
- Git 状态（当前分支、是否有未提交变更）
- 从工作目录向上递归加载的 CLAUDE.md 内容
- 项目记忆文件（MEMORY.md 及其引用的独立记忆文件，带过期标注）
- 已注册的 Skills 列表
- Shell 环境信息

两部分合并后约 5000-8000 token。

### 2.3 构建消息列表

当前的对话历史加上刚输入的用户消息，组成 `messages` 数组。如果历史很长，旧消息已经被压缩过（替换为摘要）。

在发送给 API 前，会用 `prependUserContext()` 在用户消息前面插入一条 `<system-reminder>`，包含当前目录等上下文信息。

### 2.4 怎么让模型知道我有哪些工具

关键点：**工具不在系统提示词里，而是通过 API 请求中一个独立的 `tools` 参数传入。**

系统提示词里写的是工具的"使用说明"——比如"用 read_file 而不是 cat"、"Bash 执行命令时要避免 sleep 循环"。但工具本身的定义（名称、描述、参数 schema）是作为 API 请求的 `tools` 字段发送的。

每次调用 API 时，60+ 个工具的完整 JSON Schema 被拼进请求体：

```json
{
  "model": "claude-sonnet-4-6",
  "system": "你是 Claude Code...",
  "messages": [...],
  "tools": [
    {"name": "Bash", "description": "执行 shell 命令...", "input_schema": {...}},
    {"name": "FileRead", "description": "读取文件内容...", "input_schema": {...}},
    {"name": "FileWrite", "description": "创建或覆盖文件...", "input_schema": {...}},
    ... 60+ 个工具
  ]
}
```

注意：`tools` 和 `system`（系统提示词）是 HTTP 请求体中并列的两个字段，不是包含关系。模型通过 `tools` 字段知道"我能调什么"，通过 `system` 字段知道"我该怎么用"。

工具是启动时注册的（`src/tools.ts`），每轮 while 循环中保持不变。同一个工具清单会被反复发送 20、50、甚至 100 次——模型通过这种"重复提醒"来记住它可以调用哪些工具。

### 2.5 API 调用与流式响应

**关键文件**：`src/services/api/claude.ts`

第一轮 API 请求体如下——`messages` 里只有系统提示词和用户输入，`tools` 是 60+ 个工具的完整定义：

```json
{
  "model": "claude-sonnet-4-6",
  "max_tokens": 32000,
  "system": "你是 Claude Code...",
  "messages": [
    {"role": "user", "content": "<system-reminder>当前目录: /home/user/project</system-reminder>"},
    {"role": "user", "content": "帮我创建 hello.py"}
  ],
  "tools": [
    {"name": "Bash", "description": "执行 shell 命令...", "input_schema": {...}},
    {"name": "FileRead", "description": "读取文件内容...", "input_schema": {...}},
    {"name": "FileWrite", "description": "创建或覆盖文件...", "input_schema": {...}},
    {"name": "FileEdit", "description": "精确替换文件内容...", "input_schema": {...}},
    {"name": "Glob", "description": "按模式查找文件...", "input_schema": {...}},
    {"name": "Grep", "description": "搜索文件内容...", "input_schema": {...}}
    ... 60+ 个工具
  ],
  "stream": true
}
```

API 请求发出去后，模型以流式方式返回内容。Claude Code 逐个处理流式事件：

- **文本块**（`content_block_delta`）→ 实时打印到终端，同时累积到 `assistantMessages`
- **工具调用块**（`tool_use` block）→ 解析出工具名和参数，暂存到 `toolUseBlocks`
- 流结束时返回 `stop_reason`

模型返回的流式事件序列：

```
content_block_start  (type=text)
  content_block_delta  text="好的，"
  content_block_delta  text="我来创建"
  content_block_delta  text=" hello.py"
content_block_stop

content_block_start  (type=tool_use, id="toolu_xxx", name="FileWrite")
  content_block_delta  partial_json={"path": "hello.py", "content": "print(\"Hello World\")"}
content_block_stop

message_stop  stop_reason="tool_use"
```

返回结果：**文本** "好的，我来创建 hello.py" + **工具调用** FileWrite(path="hello.py", content='print("Hello World")')

### 2.6 工具执行

**关键文件**：`src/services/tools/toolOrchestration.ts`

工具不是一股脑全执行的。关键是 `partitionToolCalls`——按"能否并发"分成批次：

- 连续的只读工具（FileRead、Grep、Glob）→ 同一批，线程池并行执行（最多 10 个并发）
- 写入工具（FileWrite、FileEdit、Bash）→ 单独一批，串行执行

权限检查在每个工具执行前跑一遍：deny 规则 → ask 规则 → tool-specific check → mode bypass → always-allow。需要用户确认的就弹窗。

我们的 FileWrite 工具执行：写入 `hello.py`，返回 `"Wrote 43 bytes to hello.py"`。

### 2.7 第二轮 API 调用

工具结果作为新的 user 消息追加到对话历史。第二轮 API 请求体中的 `tools` 字段和第一轮完全相同，但 `messages` 多了两条——一条 assistant（含 tool_use），一条 user（含 tool_result）：

```json
{
  "model": "claude-sonnet-4-6",
  "max_tokens": 32000,
  "system": "你是 Claude Code...",    // 和第一轮完全一样
  "messages": [
    {"role": "user", "content": "<system-reminder>当前目录: /home/user/project</system-reminder>"},
    {"role": "user", "content": "帮我创建 hello.py"},
    // ── 第一轮新增 ──
    {
      "role": "assistant",
      "content": [
        {"type": "text", "text": "好的，我来创建 hello.py"},
        {"type": "tool_use", "id": "toolu_xxx", "name": "FileWrite", "input": {"path": "hello.py", "content": "print(\"Hello World\")"}}
      ]
    },
    {
      "role": "user",
      "content": [
        {"type": "tool_result", "tool_use_id": "toolu_xxx", "content": "Wrote 43 bytes to hello.py"}
      ]
    }
  ],
  "tools": [                           // 完全不变，60+ 个工具原样发送
    {"name": "Bash", ...},
    {"name": "FileRead", ...},
    ...
  ],
  "stream": true
}
```

模型看到工具执行成功，第二轮返回纯文本，不再调用工具。

模型返回的最终消息结构：

```json
{
  "role": "assistant",
  "content": [
    {"type": "text", "text": "已创建 hello.py，内容为 print(\"Hello World\")。需要我运行验证吗？"}
  ]
}
```

`tool_use` 为空 → `return { reason: 'completed' }` → 循环结束。

### 2.8 最终展示

REPL 收到 `completion` 终端状态，渲染最终消息。然后回到提示符等待下一条用户输入。

---
## 三、循环熔断机制

Claude Code 并不是 "无限 while(true) 直到模型说完"。有多层保护防止失控：

### 3.1 maxTurns（硬上限）

`QueryParams` 中有一个可选的 `maxTurns` 参数。如果设置，当 `turnCount > maxTurns` 时，系统发出 `max_turns_reached` 附件并立即终止（`reason: 'max_turns'`）。默认不设上限——模型自己决定何时完成。

### 3.2 Max Output Tokens 恢复（`MAX_OUTPUT_TOKENS_RECOVERY_LIMIT = 3`）

当模型返回的 `stop_reason === 'max_output_tokens'`（输出被截断）：

1. **第一次**：将 `maxOutputTokensOverride` 设为 `ESCALATED_MAX_TOKENS`（64K），直接进入下一轮 state 让模型在更大预算下继续
2. **后续 2 次**（最多 3 次恢复）：注入一条 meta user message `"Output token limit hit. Resume directly — no apology, no recap..."`，告诉模型不要道歉、不要复述、直接从中断处继续，把工作拆小
3. **3 次恢复用完**：返回 `reason: 'model_error'`，回合结束

### 3.3 Blocking Limit（仅当 auto-compact 关闭时）

如果用户主动关闭自动压缩（`isAutoCompactEnabled() === false`），且 token 数达到硬阻断上限，返回 `reason: 'blocking_limit'`。为用户保留空间让他们手动 `/compact`。

### 3.4 Auto-Compact Circuit Breaker

压缩不是无限重试。`MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3`。连续 3 次压缩失败后触发熔断，跳过后续所有自动压缩尝试。

### 3.5 Terminal 状态汇总

`queryLoop` 通过返回不同的 `Terminal` 对象来终止。完整列表：

| reason | 触发条件 |
|---|---|
| `completed` | 模型返回无 tool_use 的回复 |
| `aborted_streaming` | 用户在流式输出期间中断 |
| `aborted_tools` | 用户在工具执行期间中断 |
| `model_error` | API 返回不可恢复的错误 |
| `max_turns` | 超过 maxTurns 限制 |
| `blocking_limit` | 达到硬阻 token 上限（auto-compact 关闭时） |
| `prompt_too_long` | 上下文溢出（reactive compact 捕获后仍失败） |
| `image_error` | 图片验证/处理失败 |
| `hook_stopped` | stop hook 阻止继续 |
| `stop_hook_blocking` | stop hook 指令阻塞继续 |

## 四、上下文压缩（共五层）

Claude Code 的压缩不是简单的 "消息数到了就压"。每轮 while 循环中依次执行以下步骤：

### 第一层：Snip Compact（`HISTORY_SNIP` feature gate）

这个是**模型自己驱动的**，不是系统自动决策的。模型可以通过 Snip tool 在对话中标记哪些消息可以删（`snip_marker`），然后系统在下一轮循环中扫描到最后一个 `snip_boundary`，物理删除标记的 UUID。释放的 token 数被记录为 `snipTokensFreed`，后续 auto-compact 的阈值检查会扣除这个数字。

### 第二层：Microcompact（每轮必跑，零 API 成本）

关键设计：**不是所有工具结果都压缩，是有选择性白名单的。**

`COMPACTABLE_TOOLS` 白名单只包含：FileRead、Shell（所有变体）、Grep、Glob、WebSearch、WebFetch、FileEdit、FileWrite。只有这些工具的结果会被截断为 `[Old tool result content cleared]`。其他工具（如 Agent、Plan 等）的返回结果永远不会被微压缩截断。

另外在微压缩之前，`applyToolResultBudget()` 会先执行：每个工具可以设置 `maxResultSizeChars` 字段（如 Bash 是 30K）。超大的工具结果被移到磁盘存储，在消息中只留一个 `<persisted-output>` 占位符加 2KB 预览。这解决了"一次读了 400KB 的日志文件"把上下文撑爆的问题——`FileRead` 的 maxResultSizeChars 是 80K，超了就持久化。

有两个版本：普通 `microCompact.ts` 和带 prompt cache 优化的 `cachedMicrocompact.ts`。

### 第三层：Context Collapse（`CONTEXT_COLLAPSE` feature gate）

在 auto-compact 之前运行。它将 REPL 的完整历史投射为一个折叠视图，"折叠"的摘要消息存在独立的 collapse store 中，不在 REPL 的消息数组里。这样折叠可以跨轮次持久化——每轮进入时 `projectView()` 重放 commit log。如果 collapse 让 token 降到 auto-compact 阈值以下，auto-compact 就跳过了——因为保留折叠后的细粒度上下文比生成一个大的摘要更好。

### 第四层：Auto-Compact（token 超阈值触发，有 API 成本）

阈值计算：`effectiveContextWindow - getAutocompactBufferTokens(model)`。对于 200K 窗口的模型，buffer 是 13K，阈值约 187K（~93.5%）。

触发后使用 fork agent——一个与主对话共享 prompt cache 的子 agent。旧消息发给子 agent 做摘要，结果通过 `buildPostCompactMessages` 组装：`[compaction boundary marker, summary messages, stripped recent messages, attachments, hook results]`。

连续 3 次失败触发熔断（`MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3`）。

### 第五层：Predictive Auto-Compact

在 API 调用前还有一个预判检查：如果 `当前 token + 本轮预估增长（maxOutput + 平均工具结果 15K）` 已经超过有效窗口，不等下一轮，现在就触发一次 auto-compact。

### 兜底：Reactive Compact（`REACTIVE_COMPACT` feature gate）

如果以上都没拦住，API 返回了 `prompt_too_long` 错误，reactive compact 会捕获这个错误，在后端做一次紧急摘要，然后重试。它相当于一个"安全网"——前面的 proactive 压缩没拦住，它来兜底。

另外还有 `sessionMemoryCompact` 用于仅压缩记忆文件而非对话历史的场景。




