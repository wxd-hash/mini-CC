# minicc — Docker 版全局命令 (PowerShell)
# 复制到 PATH 中的某个目录后即可在任意目录使用
#
# 用法:
#   minicc                          # 交互模式
#   minicc "跑一下测试"             # 一行命令
#   minicc --resume                 # 恢复会话

$EnvArgs = @(
    "-e", "ANTHROPIC_API_KEY=$env:ANTHROPIC_API_KEY",
    "-e", "DEEPSEEK_API_KEY=$env:DEEPSEEK_API_KEY",
    "-e", "OPENAI_API_KEY=$env:DEEPSEEK_API_KEY",
    "-e", "MINICLAUDE_PROVIDER=$env:MINICLAUDE_PROVIDER"
)

docker run -it --rm `
  -v "${pwd}:/home/coder/workspace" `
  -v "$env:USERPROFILE/.config/mini-claude:/home/coder/.config/mini-claude" `
  $EnvArgs `
  minicc @args
