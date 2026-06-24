#!/bin/bash
# minicc — Docker 版全局命令
# 复制到 /usr/local/bin/minicc 后即可在任意目录使用
#
# 用法:
#   minicc                          # 交互模式
#   minicc "跑一下测试"              # 一行命令
#   minicc --resume                 # 恢复会话
#   minicc --provider anthropic     # 用 Claude

# 挂载当前目录为 workspace，透传所有 API 环境变量
exec docker run -it --rm \
  -v "$(pwd):/home/coder/workspace" \
  -v "$HOME/.config/mini-claude:/home/coder/.config/mini-claude" \
  -e ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}" \
  -e DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:-}" \
  -e OPENAI_API_KEY="${DEEPSEEK_API_KEY:-}" \
  -e MINICLAUDE_PROVIDER="${MINICLAUDE_PROVIDER:-}" \
  -e MINICLAUDE_MODEL="${MINICLAUDE_MODEL:-}" \
  minicc "$@"
