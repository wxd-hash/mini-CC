# Mini Claude Code

![mini cc--meow~](fig/a7c1b575-9457-46d5-a1f8-e8b1d8f77abc.png)

基于 Claude Code 架构的轻量级终端编程助手。纯 Python 实现，零框架依赖。用自然语言和代码库对话——agent 会自主读取、写入、搜索、运行 shell 命令并修复 bug。

## 核心特性

- **Agent 工具循环** — while True 状态机模式，无硬性轮次上限
- **并行工具执行** — 只读工具（read/search/list）ThreadPoolExecutor 并行批处理，写入工具串行
- **自动压缩** — token 阈值触发（非消息数），circuit breaker 防重复压缩
- **重试机制** — 指数退避 + jitter，最多 10 次重试，自动处理限流/超时/上下文溢出
- **7 个工具** — read_file, write_file, edit_file, list_files, search_files, git_diff, run_shell
- **流式输出** — 实时逐字显示，不用等完整生成
- **双层权限系统** — 自杀防护（taskkill /IM python 永不允许）+ 高危命令检测 + 键盘菜单确认
- **会话持久化** — SessionStore 自动保存，按 workspace 组织，支持 --resume 恢复
- **双 LLM 后端** — Anthropic Claude + DeepSeek（OpenAI 兼容协议）
- **系统提示** — 全中文，内置 Shell 防循环规则
- **KAIROS 记忆** — 子 Agent 自动提取 + 独立文件存储 + frontmatter + MEMORY.md 索引
- **Skills 系统** — 内建 /review, /commit, /test, /simplify 技能
- **Plan 模式** — 子 agent 探索代码库后再实施
- **多源配置** — CLI 参数 > 环境变量 > TOML 文件

## 安装

```bash
git clone https://github.com/wxd-hash/mini-CC.git
cd mini-CC
python -m venv .venv

# Windows
.venv\Scripts\python.exe -m pip install -r requirements.txt

# macOS / Linux
.venv/bin/pip install -r requirements.txt
```

### 全局命令

```bash
.venv\Scripts\python.exe -m pip install -e .
```

之后可以在任何目录用 `minicc` 启动。

## 环境变量

```powershell
# PowerShell
$env:ANTHROPIC_API_KEY = "sk-ant-..."    # Claude
$env:DEEPSEEK_API_KEY = "sk-..."          # DeepSeek
```

```bash
# Bash / Zsh
export ANTHROPIC_API_KEY="sk-ant-..."
export DEEPSEEK_API_KEY="sk-..."
```

### 配置文件（可选）

支持 TOML 配置文件：`~/.config/mini-claude/config.toml` 或 `./.mini-claude.toml`

```toml
provider = "deepseek"
model = "deepseek-chat"
max_tokens = 8192
max_rounds = 30

[deepseek]
api_key = "sk-..."
base_url = "https://api.deepseek.com/v1"
```

## 使用

```bash
# 默认 DeepSeek，ask 模式
minicc

# 用 Anthropic
minicc --provider anthropic --model sonnet

# 指定工作目录
minicc --workspace E:\my_project

# CI / 管道模式
minicc --no-color

# 一行命令（非交互）
minicc "项目里有哪些测试文件？"

# 恢复之前的会话
minicc --resume

# 自动允许模式（跳过权限确认）
minicc --mode auto
```

### CLI 选项

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--provider anthropic\|deepseek` | `deepseek` | LLM 提供商 |
| `--model MODEL` | 默认模型 | 模型名（支持别名：sonnet, opus, haiku） |
| `--api-key KEY` | 环境变量 | API 密钥 |
| `--api-base URL` | 自动 | API 地址 |
| `--workspace PATH` | 当前目录 | 工作目录 |
| `--log-dir PATH` | `./.sessions` | 会话日志目录 |
| `--mode plan\|ask\|auto` | `ask` | 权限模式 |
| `--max-tokens N` | 模型默认 | 每次响应的最大 token 数 |
| `--resume` | — | 显示会话恢复列表 |
| `--no-color` | — | 关闭 ANSI 颜色 |
| `--config PATH` | — | 指定 TOML 配置文件 |
| `--memory-dir PATH` | `~/.config/mini-claude/memory` | 记忆目录 |

### 斜杠命令

| 命令 | 说明 |
|------|------|
| `/exit` | 退出 |
| `/tools` | 列出所有工具 |
| `/tool <name> <json>` | 手动调用工具 |
| `/perm <plan\|ask\|auto\|status>` | 切换/查看权限模式 |
| `/clear` | 重置对话 |
| `/reload` | 刷新系统提示 |
| `/compact` | 手动压缩对话 |
| `/history` | 查看历史会话 |
| `/resume [编号]` | 恢复会话 |
| `/skills` | 列出可用技能 |
| `/review` | 代码审查 |
| `/commit` | 创建 git commit |
| `/test` | 运行测试 |
| `/simplify` | 代码优化 |

## 权限系统

| 模式 | read/list/search/git | write_file/edit_file | run_shell |
|------|---------------------|---------------------|-----------|
| **plan** | 自动允许 | 拒绝 | 拒绝 |
| **ask** | 自动允许 | 交互菜单 | 交互菜单 |
| **auto** | 自动允许 | 自动允许 | 低风险自动 / 高风险菜单 |

### 高危命令检测

以下命令即使 auto 模式也要确认：`rm`, `del`, `taskkill`, `kill`, `killall`, `sudo`, `curl`, `wget`, `shutdown`, `docker rm/rmi`, `git reset --hard`, `pip install`, `npm install`

### 自杀防护

以下命令**在任何模式下永不允许**，不弹窗，直接拒绝：
- `taskkill /IM python` — 会杀死 agent 自身
- `killall python` / `pkill python`
- `kill -9 -1`（杀全系统进程）
- fork bomb

## 工具

| 工具 | 参数 | 说明 |
|------|------|------|
| `read_file` | `path` | 读取文件，最多 12000 字符 |
| `write_file` | `path`, `content` | 写入/覆盖文件 |
| `edit_file` | `path`, `old_string`, `new_string`, `replace_all?` | 精确字符串替换 |
| `list_files` | `path` (可选，默认 `.`) | 列出目录，最多 200 条 |
| `search_files` | `query`, `path` (可选) | 搜索文件内容，优先用 ripgrep |
| `git_diff` | `path` (可选), `staged` (可选) | git 差异（只读） |
| `run_shell` | `command`, `run_in_background?`, `timeout?` | 执行 shell 命令 |

### run_shell 特性

- 30 秒默认超时
- **自动驾驶后台化**：服务器命令（uvicorn, flask run 等）自动后台运行，超时不会 kill
- `run_in_background=true` — 命令在后台运行，立即返回 PID
- 命令超过 15 秒自动转入后台（不杀进程）
- Exit code 语义解释：exit 1 = 预期行为（grep 无匹配），exit 126+ = 致命错误不要重试

## 会话与恢复

会话存储在 `.sessions/` 下，按 workspace 组织：

```
.sessions/
├── E_test_minicc/
│   ├── session-20260615T103402Z.jsonl
│   ├── session-20260615T103402Z.meta.json
│   └── session-20260615T110000Z.jsonl
└── E_mini_claude_code/
    └── session-20260615T100000Z.jsonl
```

恢复历史会话：

```bash
minicc --resume
```

```
Select a session to resume:
──────────────────────────────────────────────
  ▸ Fix failing pytest tests             06-15 11:30
    Create a hello world script          06-15 10:15
    (start fresh)
```

## 项目记忆 (KAIROS)

参考 Claude Code 的 `extractMemories` + `memoryAge` 设计。每轮对话后自动启动后台**子 Agent**，审阅对话 transcript 并提取更新记忆。

### 存储格式

每个记忆一个独立 `.md` 文件（YAML frontmatter），`MEMORY.md` 作索引：

```
~/.config/mini-claude/memory/
├── MEMORY.md              ← 索引文件
├── user_role.md           ← 用户角色/偏好
├── feedback_testing.md    ← 用户反馈/规则
├── project_xxx.md         ← 项目状态/截止日期
└── reference_xxx.md       ← 外部资源引用
```

四种类型：**user** / **feedback** / **project** / **reference**。

### 过期处理

**不自动删除**旧记忆，而是根据文件修改时间注入提示：
- 1 天内的记忆：正常加载
- 超过 1 天的记忆：注入 `此记忆是 X 天前的，可能已过时，请验证`

模型看到提示后会自行判断该信还是该验证（"用 pytest"永远不过期，"第 23 行有 bug"三天后大概率失效）。

容量保护：最多 200 个文件 + MEMORY.md 截断 200 行 + 孤儿文件自动清理。

## 项目结构

```
mini-claude-code/
├── main.py                         # CLI 入口
├── test_all.py                     # 测试套件（无需 API key）
├── pyproject.toml
├── requirements.txt
├── src/
│   ├── entry.py                    # console_scripts 入口
│   ├── app.py                      # 引导程序（组装 provider/tools/engine）
│   ├── config.py                   # 多源配置（CLI > env > TOML）
│   ├── context.py                  # 系统提示构建 + 压缩
│   ├── terminal.py                 # ANSI 终端样式 + 键盘菜单
│   ├── repl.py                     # 交互 REPL 循环
│   ├── commands.py                 # 斜杠命令 + 恢复逻辑
│   ├── agent/
│   │   └── loop.py                 # Engine — while True 主循环
│   ├── llm/
│   │   ├── provider.py             # LLMProvider 抽象
│   │   ├── anthropic_provider.py   # Anthropic（流式）
│   │   └── openai_provider.py      # OpenAI/DeepSeek（流式）
│   ├── tools/
│   │   ├── base.py                 # Tool 协议 + ToolResult
│   │   ├── registry.py             # 工具注册表
│   │   ├── file_tools.py           # ReadFile, WriteFile, FileEditTool, ListFiles, SearchFiles
│   │   ├── shell_tool.py           # RunShell（后台/超时/exit code 语义）
│   │   └── git_tools.py            # GitDiff
│   ├── features/
│   │   ├── memory.py               # KAIROS 记忆系统
│   │   ├── skills.py               # Skills 发现和注册
│   │   ├── skills_bundled.py       # 内建 skills
│   │   ├── plan.py                 # Plan 模式
│   │   ├── compact.py              # 压缩服务
│   │   └── cost_tracker.py         # Token 成本追踪
│   ├── workspace/
│   │   └── sandbox.py              # 路径沙箱
│   ├── security/
│   │   └── permission.py           # 权限检查（自杀防护 + 高危命令）
│   └── session/
│       └── logger.py               # SessionStore + SessionLogger
```

## Docker 运行

内置 git + ripgrep，非 root 用户运行，所有操作在挂载的 workspace 内。

```bash
# 构建（只需一次）
docker build -t minicc .

# 交互模式
docker run -it --rm \
  -v "$(pwd):/home/coder/workspace" \
  -e DEEPSEEK_API_KEY="sk-..." \
  minicc

# 一行命令
docker run --rm \
  -v "$(pwd):/home/coder/workspace" \
  -e DEEPSEEK_API_KEY="sk-..." \
  minicc "项目里有哪些测试文件？"
```

### 设为全局命令（推荐）

安装包装脚本后，**任何目录**直接敲 `minicc`，和本地安装完全一样：

**macOS / Linux:**
```bash
sudo cp docker-minicc.sh /usr/local/bin/minicc
sudo chmod +x /usr/local/bin/minicc
# 现在任何目录都可以直接 minicc
```

**Windows PowerShell:**
```powershell
cp docker-minicc.ps1 $PROFILE\..\docker-minicc.ps1
# 在 $PROFILE 中添加:
#   function minicc { & "$env:USERPROFILE\Documents\WindowsPowerShell\docker-minicc.ps1" @args }
```

## 安装（本地 Python）

## 快速测试

```bash
# 18 个单元测试
python -m pytest test_all.py -v
```

## 架构参考

本项目的核心循环、压缩、权限管线基于 Claude Code（Anthropic 官方 CLI）的源码翻译：
- `agent/loop.py` ← `src/query.ts` queryLoop()
- 自动压缩 ← `src/services/compact/autoCompact.ts`
- 终端样式 ← Claude Code 的 ↳ 输出风格
- 工具协议 ← `src/Tool.ts`
