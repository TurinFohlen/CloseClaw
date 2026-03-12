#!/bin/bash
# entrypoint.sh — 无头工作站启动序列
#
# 启动顺序（像机器开机一样有序）：
#   1. Xvfb        虚拟显示器
#   2. mitmproxy   脑干（要在 Chrome 之前起，否则丢最初几条 SSE）
#   3. Chrome      大脑容器（带代理 + 调试端口）
#   4. 四条神经    后台运行，写 /app/logs/
#   5. x11vnc      可选，按环境变量开关
#   6. watchdog    监控所有进程，任何一个挂了就重启

set -e

LOG_DIR=/app/logs
SHARED=${CLOSECLAW_SHARED:-/shared}
mkdir -p "$LOG_DIR" "$SHARED"

log() { echo "[$(date '+%H:%M:%S')] [entrypoint] $*"; }

# ── 1. 虚拟显示器 ─────────────────────────────────────────────────────────────
log "Starting Xvfb on :99 (1280x720x24)..."
Xvfb :99 -screen 0 1280x720x24 -ac &
XVFB_PID=$!
sleep 1
log "Xvfb PID=$XVFB_PID"

# ── 2. 脑干（mitmproxy）─────────────────────────────────────────────────────
log "Starting brainstem (mitmproxy on :8080)..."
mitmdump \
    -s /app/brainstem/brainstem.py \
    --listen-port 8080 \
    --ssl-insecure \
    > "$LOG_DIR/brainstem.log" 2>&1 &
MITM_PID=$!
sleep 2
log "mitmproxy PID=$MITM_PID"

# mitmproxy CA 信任（首次运行后 CA 在 ~/.mitmproxy/）
# Chrome 用 --ignore-certificate-errors 绕过（仅本地用，可接受）

# ── 3. Chrome ─────────────────────────────────────────────────────────────────
log "Starting Chrome → claude.ai..."
google-chrome \
    --no-sandbox \
    --disable-dev-shm-usage \
    --disable-gpu \
    --proxy-server="http://127.0.0.1:8080" \
    --ignore-certificate-errors \
    --remote-debugging-port=9222 \
    --user-data-dir=/app/chrome-profile \
    --window-size=1280,720 \
    --app=https://claude.ai \
    > "$LOG_DIR/chrome.log" 2>&1 &
CHROME_PID=$!
sleep 3
log "Chrome PID=$CHROME_PID"

# ── 4. 运动神经 ───────────────────────────────────────────────────────────────
log "Starting motor_nerve..."
python3 /app/brainstem/motor_nerve.py \
    > "$LOG_DIR/motor_nerve.log" 2>&1 &
MOTOR_PID=$!

# ── 5. 感觉反馈神经 ───────────────────────────────────────────────────────────
log "Starting feedback_nerve..."
python3 /app/brainstem/feedback_nerve.py \
    > "$LOG_DIR/feedback_nerve.log" 2>&1 &
FEEDBACK_PID=$!

log "All processes started."
log "  Xvfb:         $XVFB_PID"
log "  mitmproxy:    $MITM_PID"
log "  Chrome:       $CHROME_PID"
log "  motor_nerve:  $MOTOR_PID"
log "  feedback:     $FEEDBACK_PID"

# ── 6. VNC（可选）────────────────────────────────────────────────────────────
if [ "${ENABLE_VNC:-0}" = "1" ]; then
    log "Starting x11vnc on :5900 (no auth — LAN only)..."
    x11vnc -display :99 -forever -nopw -quiet \
        > "$LOG_DIR/vnc.log" 2>&1 &
    log "VNC PID=$!"
fi

# ── 7. Watchdog ───────────────────────────────────────────────────────────────
# 任何关键进程退出 → 整个容器退出（让 Docker restart policy 重启）
log "Watchdog active. Monitoring critical processes..."

while true; do
    for pid_var in MITM_PID CHROME_PID MOTOR_PID FEEDBACK_PID; do
        eval pid=\$$pid_var
        if ! kill -0 "$pid" 2>/dev/null; then
            log "FATAL: $pid_var (PID=$pid) died. Exiting for restart."
            exit 1
        fi
    done
    sleep 5
done
