# Mini Claude Code 架构设计文档

## 一、概述

Mini Claude Code（以下简称 minicc）是一个终端编程助手，你用自然语言告诉它要做什么，它会自主读取文件、搜索代码、运行命令、修改文件，直到任务完成。

核心设计参考了 Anthropic 官方的 Claude Code CLI 工具，但用纯 Python 实现，约 5600 行源码，零 Web 框架依赖。

### 一句话理解它

```
你: "帮我把所有测试跑通"

minicc:
  ↳ search_files(query='def test_')   ← 先找测试文件在哪
  ↳ ✓ 找到 12 个测试文件               ← 找到了
  ↳ run_shell(command='pytest')        ← 运行全部测试
  ↳ ✓ 3 个失败                         ← 有测试挂了
  ↳ read_file(path='test_calc.py')    ← 读失败的测试文件
  ↳ ✓ 读到第 23 行有 bug               ← 定位问题
  ↳ edit_file(old='x+y', new='x*y')   ← 修 bug
  ↳ run_shell(command='pytest')        ← 再跑一遍测试
  ↳ ✓ 18 个测试全部通过                 ← 全部通过！

"改完了。test_calc.py 第 23 行把加法写成了乘法，已修复。"
```

---

## 二、核心架构

### 2.1 整体数据流

```
用户输入 → REPL 循环 → Engine.submit() →
  ┌─────────────────────────────────────────┐
  │ while True:                              │
  │   ① Microcompact（截断旧的工具输出）     │
  │   ② Auto-compact（token 过多时 LLM 摘要）│
  │   ③ 调用 API（流式获取模型响应）         │
  │   ④ 如果模型返回纯文本 → 结束            │
  │   ⑤ 如果模型调用工具 → 执行 → 回到 ①    │
  └─────────────────────────────────────────┘
```

### 2.2 为什么用 `while True` 而不是限制轮次

早期版本用 `for i in range(20)` 限制最多 20 轮工具调用。这导致复杂任务（比如"创建一个完整的前后端项目"）经常到 20 轮就被截断。

现在改为 `while True` 无限循环，由模型自己判断任务是否完成。当轮次超过 60 时给出黄色警告，超过 300 时才强制终止。这和 Claude Code 的做法一致——模型比你更清楚任务做没做完。

### 2.3 并行工具执行

这是一项重要优化。当模型在一次响应中同时调用多个**只读**工具（比如同时读取三个文件，或同时搜索两个目录），minicc 会用线程池并行执行它们。

**并行条件**：只有连续的只读工具才能并行。比如模型依次调用了 read_file A → read_file B → search_files C → write_file D，前面的 A、B、C 会被打包并行执行，D 单独串行。

**为什么写入必须串行**：避免竞争条件。两个工具同时写同一个文件会导致数据损坏。

---

## 三、系统提示工程

系统提示是 minicc 的"说明书"——它告诉模型该怎么做。好的系统提示是 agent 表现好坏的关键。

### 3.1 整体结构

minicc 的系统提示是分块拼接的：

```
[基础提示]   — "你是 Mini Claude Code，请用中文回复"
[工作目录]   — 告诉模型当前在哪里
[项目记忆]   — 跨会话的持久记忆内容
[项目指令]   — 从 CLAUDE.md 文件中加载的规则
[Skills 列表] — 可用的技能（/review, /commit, /test, /simplify）
[Shell 规则] — 防止死循环的关键规则
[行为规则]   — 通用行为准则
```

其中基础提示和 Shell 规则是最重要的两个部分。

### 3.2 语言控制

系统提示开头第一句就是"请始终用中文回复用户"。所有 7 个工具的描述也都是中文。这样确保了模型输出中文。

### 3.3 Shell 防循环规则

这部分是从大量实际使用中总结出来的。模型最常见的"跑偏"模式是：

- **超时重试循环**：命令超时 → 模型以为失败 → 重试同样的命令 → 又超时...
- **服务器重启循环**：启动服务器 → 超时被 kill → 模型以为崩溃 → kill 所有 Python → 重启 → 又被 kill...

系统提示明确告诉模型：
- 禁止用 sleep 循环或轮询
- 命令失败就分析原因，不要重试
- 服务器命令用 `run_in_background=true`，启动一次就让它跑
- 超过 15 秒会自动转入后台，这不是错误，进程还在运行

### 3.4 工具选择优先级

模型有时会用 shell 命令来做本该由专用工具做的事（比如用 `cat` 读文件而不是用 `read_file`）。系统提示明确列出了优先级：

- 读文件 → read_file（不是 cat/head/tail）
- 写文件 → write_file（不是 echo >）
- 编辑文件 → edit_file（不是 sed/awk）
- 搜索文件 → search_files（不是 grep/rg）
- 列出目录 → list_files（不是 ls）

---

## 四、工具系统

### 4.1 工具协议

每个工具都遵循统一的接口，回答五个问题：

1. **name**：工具的唯一标识，模型通过名字调用它。比如 `"read_file"`
2. **description**：做什么的，用中文写。模型看描述来决定用哪个工具
3. **input_schema**：需要什么参数，JSON Schema 格式。比如 `{"path": "string"}`
4. **is_read_only()**：返回 True 表示这个工具不会修改任何东西，可以并行执行
5. **get_activity_description()**：执行时终端上显示的简短描述

### 4.2 七个工具

| 工具 | 用途 | 并发 |
|------|------|------|
| read_file | 读取文件内容（UTF-8，最多 12000 字符） | 是 |
| write_file | 创建/覆盖文件 | 否 |
| edit_file | 精确替换文件中的字符串 | 否 |
| list_files | 列出目录内容（最多 200 条） | 是 |
| search_files | 搜索文件内容（优先用 ripgrep） | 是 |
| git_diff | 显示 git 差异（只读） | 是 |
| run_shell | 执行 shell 命令 | 否 |

### 4.3 edit_file —— 为什么需要它

早期版本只有 write_file，它只能覆盖整个文件。但大多数修改只需要改一两行。如果每次都要读整个文件、修改、再写回，token 消耗会非常大。

edit_file 的工作方式是：
- 你提供要替换的原文（old_string）和新文（new_string）
- 系统在文件中精确查找 old_string，替换为 new_string
- 如果 old_string 找到了多处且没传 replace_all=True → 拒绝执行并告知
- 如果找不到 old_string → 拒绝执行并告知（通常是因为空格/缩进不匹配）

### 4.4 run_shell —— 最复杂也最常用的工具

run_shell 有三个独特的设计：

**自动后台化**
当命令中包含服务器相关的关键词（uvicorn、flask run、npm run dev 等），系统识别后直接后台运行，立即返回 PID，不等待结果。模型被告知"进程在后台运行，直接用"。

**15 秒自动转入后台**
普通命令如果执行超过 15 秒，不会被 kill。而是弹出一条消息：
```
[auto-backgrounded after 15s] PID 12345 is still running.
This is NOT an error — the process continues.
Do NOT kill and restart. It's already running.
```
这直接根除了"超时→重试"的死循环问题。

**Exit code 语义解释**
shell 命令的退出码不只是 0=成功/非0=失败。比如 grep 找不到匹配时返回 1，diff 发现差异时也返回 1——这些都不是错误。run_shell 会在输出中解释退出码的含义，防止模型误判。

---

## 五、权限系统

权限系统有三层防护，从绝对禁止到可以商量：

### 5.1 第一层：自杀防护（绝对禁止）

以下命令在任何情况下都会被**直接拒绝**，不弹窗，不商量：
- `taskkill /IM python` — 杀死所有 Python 进程，包括 minicc 自己
- `killall python` / `pkill python`
- `kill -9 -1` — 杀死全系统所有进程
- Fork bomb（`:(){ :|:& };:` 及其变体）

但允许杀**特定 PID** 的进程（比如 `taskkill /PID 12345`）。这让模型可以用 "先找到服务器 PID，再杀那个 PID" 的方式重启服务。

### 5.2 第二层：权限模式

| 模式 | 读文件 | 写文件 | 跑命令 |
|------|--------|--------|--------|
| plan（计划模式） | 自动允许 | 直接拒绝 | 直接拒绝 |
| ask（询问模式） | 自动允许 | 弹窗确认 | 弹窗确认 |
| auto（自动模式） | 自动允许 | 自动允许 | 低风险自动 / 高风险弹窗 |

### 5.3 第三层：高危命令检测

即使在 auto 模式下，以下命令还是需要确认：
- 删除操作：rm, del, rmdir
- 权限提升：sudo, chmod, chown
- 网络下载：curl, wget
- 系统操作：shutdown, reboot, format
- Docker 危险操作：docker rm/rmi/stop
- 包安装：pip install, npm install
- Git 破坏性操作：git push --force, git reset --hard

### 5.4 交互确认菜单

弹窗确认不是简单的 y/n 输入，而是键盘导航的三选一菜单：
```
[auth] Allow run_shell(pytest tests/)?
    ▸ Yes
      Yes, and don't ask again
      No
```

用 `↑↓` 或 `jk` 或 `ws` 导航，Enter 或空格确认。"Don't ask again" 的授权在**当前回合**内有效，下个回合自动重置。

---

## 六、自动压缩

当对话很长时（比如和 minicc 连续交互了 50 轮），消息历史会变得极其庞大，超出模型的上下文窗口。自动压缩就是用来解决这个问题的。

### 6.1 两级压缩

**Microcompact（零成本）**
把旧消息中工具返回的具体内容替换成 `[content truncated]`。保留消息结构（谁调了什么工具、返回了什么），但扔掉具体文件内容。这是纯内存操作，不调用 API。

**Auto-compact（LLM 摘要）**
把旧消息发给一个模型，让它生成结构化摘要，然后用摘要替换掉原始消息。这一步需要额外一次 API 调用，但能大幅减少 token 数。

### 6.2 触发时机

不是按消息数量触发（旧方案的"30 条就压"），而是基于 token 估算：

```
当前 token 估算 + 预估本轮增长 > 上下文窗口 × 80%
→ 触发 auto-compact
```

预估本轮增长 = max_output_tokens + 平均工具结果大小（~15000 tokens）

### 6.3 Circuit Breaker（熔断器）

如果连续 3 次 auto-compact 都失败了（通常是因为上下文已经远超窗口，无法挽回），系统会停止尝试压缩。此时用户需要手动 `/clear` 重置对话。

---

## 七、重试机制

API 调用可能因为各种原因失败。minicc 有完整的重试策略：

### 7.1 指数退避 + 随机抖动

- 第 1 次重试等待 ~0.5 秒
- 第 2 次重试等待 ~1 秒
- 第 3 次重试等待 ~2 秒
- ...
- 最多 10 次，最大等待 32 秒

每次等待会加上 ±25% 的随机抖动，防止多个请求同时重试（惊群效应）。

### 7.2 智能判断

- **认证错误**（密钥无效）→ 立即停止
- **限流**（429 Too Many Requests）→ 重试，优先用 API 返回的 Retry-After 时间
- **服务端故障**（5xx）→ 重试
- **上下文溢出**（prompt too long）→ 自动把 max_tokens 减半后重试

### 7.3 上下文溢出自动降级

max_tokens 影响模型能输出的最大 token 数，也间接影响 API 对上下文大小的容忍度。当 API 报"prompt too long"时：
- 32000 → 16000 → 8000 → 4000 → 2048 → 1024
- 降到 1024 还不行 → 报错给用户

---

## 八、循环检测

防止模型陷入"鬼打墙"的三重机制：

### 8.1 重复调用检测

如果同一个工具用**完全相同的参数**在最近 5 次调用中出现了 3 次，触发警告。模型会看到 "你已用相同参数调用了 3 次，结果不会改变，换个方法"。

### 8.2 连续错误熔断

如果连续 5 个工具调用都返回错误，自动中止当前回合。防止模型在一连串失败中浪费 token。

### 8.3 重复内容检测

如果 read_file、search_files 或 git_diff 连续 3 次返回完全一样的内容（SHA256 哈希判断），触发警告。模型看到 "你已连续读到同样的内容 3 次了，不要再读了，基于已知信息行动"。

---

## 九、会话持久化

每次对话自动保存，即使意外退出也能恢复。

### 9.1 存储结构

```
.sessions/
├── E_mini_claude_code/                    ← 按 workspace 组织目录
│   ├── session-20260624T103402Z.jsonl     ← 消息记录（每行一条 JSON）
│   ├── session-20260624T103402Z.meta.json ← 元数据
│   └── session-20260624T150000Z.jsonl
└── E_my_project/
    └── session-20260624T120000Z.jsonl
```

### 9.2 实时保存

每条消息（用户的输入、模型的回复、工具的调用和结果）都会实时追加到 JSONL 文件。元数据文件（.meta.json）包含：
- 会话标题（第一条用户消息的前 60 个字符）
- 使用的模型
- 权限模式
- 创建时间

### 9.3 恢复流程

`minicc --resume` 会：
1. 列出当前 workspace 的所有历史会话
2. 显示标题和最后活动时间
3. 用键盘导航选择要恢复的会话
4. 完整加载消息历史（不是摘要）
5. 恢复后可以继续对话，就像从未中断过

---

## 十、配置系统

### 10.1 三层优先级

CLI 参数 > 环境变量 > TOML 文件 > 默认值

### 10.2 模型别名

支持简短的别名，自动映射到完整模型名：
- `sonnet` → `claude-sonnet-4-6`
- `opus` → `claude-opus-4-6`
- `haiku` → `claude-haiku-4-5-20251001`

### 10.3 TOML 配置文件

两个位置（项目级覆盖全局）：
- `~/.config/mini-claude/config.toml`（全局配置）
- `./.mini-claude.toml`（项目配置）

```toml
provider = "deepseek"
model = "deepseek-chat"
max_tokens = 8192

[deepseek]
api_key = "sk-..."
base_url = "https://api.deepseek.com/v1"
```

---

## 十一、Skills 技能系统

Skills 是可复用的工作流，本质上是一段预设的提示词，提交给模型执行。

### 11.1 内建 Skills

- `/review` — 代码审查：自动 git diff，检查 bug、安全隐患、可改进的代码
- `/commit` — 提交代码：分析变更 → 生成 commit message → git add → git commit
- `/test` — 运行测试：找到测试文件 → 运行 pytest → 报告结果
- `/simplify` — 代码优化：git diff → 找重复代码、过于复杂的逻辑 → 简化

### 11.2 自定义 Skills

可以在 `~/.mini-claude/skills/` 或 `./.mini-claude/skills/` 下放 `.py` 文件来添加自定义技能。

---

## 十二、KAIROS 记忆系统

跨会话的持久记忆——让 minicc 在不同对话中"记住"关键信息。参考了 Claude Code 的 `extractMemories` 子 agent 模式。

### 12.1 存储格式

记忆不是一个大文件，而是**每个记忆一个独立文件 + 一个索引**：

```
~/.config/mini-claude/memory/
├── MEMORY.md              ← 索引文件（每行一个链接，注入系统提示）
├── user_role.md           ← "用户是后端工程师，偏好 pytest"
├── feedback_testing.md    ← "测试必须用真实数据库"
├── project_deadline.md    ← "3 月 5 日合并冻结"
└── reference_linear.md    ← "Bug 追踪在 Linear 的 INGEST 项目"
```

每个记忆文件用 YAML frontmatter 标记元数据：

```
---
name: user-role
description: 用户是后端工程师，偏好 pytest
metadata:
  type: user
---

用户是资深后端工程师，写 Python 和 Go。测试用 pytest，不用 unittest。
```

四种记忆类型：
- **user** — 用户角色、偏好、知识背景
- **feedback** — 用户给的反馈：要做什么、不要做什么
- **project** — 项目状态：谁在做什么、为什么、截止日期
- **reference** — 外部资源：Bug 追踪在哪、文档在哪

### 12.2 提取方式：后台子 Agent

每轮对话结束后，系统自动判断是否需要提取记忆。这模仿了 Claude Code 的 `extractMemories` 设计：

1. **先检查主 Agent** — 如果主 agent 在对话中已经自己写了记忆文件，跳过提取
2. **启动后台子 Agent** — 如果主 agent 没写，启动一个独立的子 agent
3. **子 Agent 收到**：对话 transcript + 现有记忆文件列表 + 记忆目录的读写权限
4. **子 Agent 的策略**：第一轮并行读现有文件，第二轮并行写更新
5. **非阻塞** — 整个过程在后台线程运行，不影响 REPL 交互

### 12.3 为什么用子 Agent 而不是标签

被动提取 `<memory>` 标签有几个问题：
- 模型不一定记得加标签
- 容易遗漏重要信息
- 无法去重和更新已有记忆

子 Agent 模式解决了这些问题：它主动审阅完整对话，独立判断什么值得记住、什么该更新、什么已过时。整个过程对用户完全透明。

### 12.4 记忆注入

`MEMORY.md` 会被加载到系统提示的 `<project_memory>` 区块中。这意味着每次对话，模型都能看到之前积累的所有记忆。超过 200 行的部分会被截断，保持系统提示的精简。

---

## 十三、LLM Provider 抽象

上层代码（Engine、REPL、Commands）完全不需要关心底层用的是 Anthropic 还是 DeepSeek/OpenAI。

### 13.1 统一接口

所有 provider 都实现：
- `send_message()` — 发送消息，返回流式响应
- `make_user_message()` — 创建用户消息的 provider 原生格式
- `make_tool_result_messages()` — 创建工具结果消息的原生格式
- `tools_for_provider()` — 把工具定义转为 provider 的原生 schema

### 13.2 消息格式差异自动处理

- Anthropic：工具结果是用户消息中的一个 content block（`type: "tool_result"`）
- OpenAI/DeepSeek：工具结果是独立的 `role: "tool"` 消息

这种差异在 provider 层就处理掉了，Engine 完全无感知。

---

## 十四、终端输出设计

参考了 Claude Code 的视觉风格。

### 14.1 关键视觉元素

```
↳ read_file(path='app.py')               ← 工具调用：灰色 ↳ 前缀，青色工具名
↳ read_file(path='app.py')  ...  读取中  ← 执行中：灰色省略号
↳ ✓ Wrote 1234 chars to app.py            ← 成功：绿色 ✓ + 简短摘要
↳ ✗ Error: file not found                 ← 失败：红色 ✗ + 错误信息

[auth] Allow run_shell(pytest)?           ← 权限提示：黄色 [auth] 标签

[compacted 87 → 14 messages]              ← 压缩通知：灰色

[60 tool rounds — still working]          ← 轮次警告：黄色
```

### 14.2 设计原则

- **信息密集但不杂乱**：每行尽量在一行内表达完整信息
- **颜色传达语义**：绿色=成功，红色=失败，黄色=警告，灰色=次要信息
- **不要重复**：工具调用的详细参数只在第一次显示，结果只显示前 200 字符的摘要

---

## 十五、完整的交互示例

这是一个真实的交互序列，展示各系统如何协同工作：

```
用户: "帮我给这个项目加上日志功能，把所有 print 换成 logging"

minicc:
  ↳ list_files(path='.')
  ↳ ✓ 12 files                          ← 了解项目结构

  ↳ search_files(query='print(')
  ↳ ✓ 5 matches in 3 files              ← 找到所有 print

  ↳ read_file(path='app.py')
  ↳ ✓ ...def main(): print("starting")  ← 确认需要改的地方

  ↳ read_file(path='utils.py')
  ↳ ✓ ...print(f"error: {e}")           ← 另一个文件也要改

  ↳ edit_file(path='app.py',
              old='print("starting")',
              new='logging.info("starting")')
  ↳ ✓ 1 occurrence replaced             ← 改第一处

  ↳ edit_file(path='utils.py',
              old='print(f"error: {e}")',
              new='logging.error(f"error: {e}")')
  ↳ ✓ 1 occurrence replaced             ← 改第二处

  [auto-compacting conversation...]      ← 对话变长，自动压缩
  [compacted 28 → 8 messages]

  ↳ read_file(path='app.py',            ← 继续：在文件开头加 import
              offset=1, limit=5)
  ↳ edit_file(path='app.py',
              old='import sys',
              new='import sys\nimport logging')
  ↳ ✓ 1 occurrence replaced

  ↳ run_shell(command='pytest')
  ↳ ✓ 18 passed                          ← 验证通过

"已完成日志功能迁移：
 - app.py: 替换 2 处 print 为 logging，添加 import
 - utils.py: 替换 3 处 print 为 logging
 - 18 个测试全部通过"
```

在这个过程中：
- 中途触发了自动压缩，用户毫无感知
- 所有工具调用都是并行的（因为都是只读的 read_file 和 search_files）
- 如果 API 调用失败，重试机制自动处理
- 所有修改都通过 git_diff 可以追踪

---

## 十六、关键数据

| 指标 | 数值 |
|------|------|
| 核心源码 | ~5600 行 Python |
| 文件数 | 33 个源文件 |
| 工具 | 7 个 |
| 内建 Skills | 4 个 |
| LLM Provider | 2 个 |
| 权限模式 | 3 种 |
| 最大重试 | 10 次 |
| 强制终止 | 300 轮 |
| 压缩阈值 | 上下文窗口 × 80% |
| 测试覆盖率 | 18 个测试用例 |
