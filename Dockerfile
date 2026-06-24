FROM python:3.14-slim

LABEL description="Mini Claude Code - 轻量级终端编程助手"

# Git 用于版本控制，ripgrep 加速搜索
RUN apt-get update && apt-get install -y --no-install-recommends git ripgrep \
    && rm -rf /var/lib/apt/lists/*

# 非 root 用户
RUN useradd --create-home --shell /bin/bash coder

# 复制项目
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt colorama python-dotenv

COPY src/ /app/src/
COPY main.py /app/
COPY pyproject.toml /app/

# 安装 minicc 命令
RUN pip install --no-cache-dir -e /app/

# 数据目录
RUN mkdir -p /home/coder/workspace \
    /home/coder/.config/mini-claude/memory \
    /home/coder/.config/mini-claude/sessions \
    && chown -R coder:coder /home/coder /app

USER coder
WORKDIR /home/coder/workspace

VOLUME ["/home/coder/workspace"]
VOLUME ["/home/coder/.config/mini-claude/memory"]

ENTRYPOINT ["minicc"]
