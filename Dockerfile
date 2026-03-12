# CloseClaw — 无头 AI 工作站
#
# 这个容器就是"网吧的一台机器"：
#   - 虚拟显示器（Xvfb）
#   - Chrome（登录好 claude.ai）
#   - mitmproxy 脑干（拦截 SSE）
#   - 四条神经脚本（motor / sensory / feedback / watchdog）
#   - x11vnc（可选，调试时临时开）
#
# 构建：docker build -t closeclaw .
# 运行：docker compose up -d

FROM ubuntu:22.04
LABEL maintainer="RoseHammer" description="CloseClaw headless AI workstation"

ENV DEBIAN_FRONTEND=noninteractive
ENV DISPLAY=:99
ENV CLOSECLAW_SHARED=/shared
ENV PYTHONUNBUFFERED=1

# ── 系统依赖 ──────────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    # 虚拟显示
    xvfb \
    x11vnc \
    # 输入模拟
    xdotool \
    xclip \
    # 文件监控（inotifywait）
    inotify-tools \
    # Chrome 依赖
    wget \
    gnupg \
    ca-certificates \
    fonts-liberation \
    libnss3 \
    libatk-bridge2.0-0 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    # Python
    python3.11 \
    python3-pip \
    # 工具
    curl \
    git \
    jq \
    procps \
    && rm -rf /var/lib/apt/lists/*

# ── Chrome ────────────────────────────────────────────────────────────────────
RUN wget -q -O - https://dl.google.com/linux/linux_signing_key.pub \
    | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg && \
    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] \
    http://dl.google.com/linux/chrome/deb/ stable main" \
    > /etc/apt/sources.list.d/google-chrome.list && \
    apt-get update && apt-get install -y google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# ── Python 依赖 ───────────────────────────────────────────────────────────────
COPY pyproject.toml /app/
WORKDIR /app

RUN pip install --no-cache-dir \
    mitmproxy \
    playwright \
    && playwright install chromium \
    && playwright install-deps chromium

# ── 安装 CloseClaw ────────────────────────────────────────────────────────────
COPY . /app/
RUN pip install --no-cache-dir -e ".[config]"

# ── mitmproxy CA 预生成（容器启动时会用） ─────────────────────────────────────
# 实际 CA 在首次 mitmdump 运行时生成，挂载到 volume 持久化
# 浏览器信任通过 --ignore-certificate-errors-spki-list 或 NSS certutil 注入

# ── 共享目录 ──────────────────────────────────────────────────────────────────
RUN mkdir -p /shared /app/logs

# ── 入口脚本 ──────────────────────────────────────────────────────────────────
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

VOLUME ["/shared", "/app/chrome-profile", "/root/.mitmproxy"]

EXPOSE 5900  # VNC（仅调试，生产不开）

CMD ["/entrypoint.sh"]
