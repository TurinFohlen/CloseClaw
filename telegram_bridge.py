#!/usr/bin/env python3
"""
telegram_bridge.py — 老板远程下指令的神经末梢

老板在外面喝茶，手机发一条 Telegram 消息，
AI 在机房收到，干完活把结果发回来。

依赖：
    pip install python-telegram-bot

配置：
    1. 找 @BotFather 创建 bot，拿到 TOKEN
    2. 找 @userinfobot 拿到你自己的 CHAT_ID
    3. 写进环境变量或 /shared/telegram.env

用法：
先启动
TELEGRAM_BOT_TOKEN=xxx TELEGRAM_CHAT_ID=yyy \
docker compose --profile telegram up -d
python telegram_bridge.py
然后手机发消息就行，普通文本直接当 task，不需要加 /task 前缀。

    # Telegram 里发：
    /task 帮我写一个爬虫爬取 example.com
    /status        → 看最新 AI 回复
    /log 5         → 看最近 5 条历史
    /alert         → 看 /shared/ALERT_* 文件
"""

import asyncio
import logging
import os
from pathlib import Path
from datetime import datetime

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ── 配置 ──────────────────────────────────────────────────────────────────────

SHARED_DIR   = Path(os.getenv("CLOSECLAW_SHARED", "/shared"))
TOKEN        = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED_CHAT = int(os.getenv("TELEGRAM_CHAT_ID", "0"))  # 只响应这个 chat_id

TASK_FILE    = SHARED_DIR / "telegram_task.txt"    # 写给 motor_nerve 的指令
RESPONSE_FILE = SHARED_DIR / "response_latest.txt"
LOG_FILE     = SHARED_DIR / "response_log.jsonl"

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


# ── 鉴权装饰器 ────────────────────────────────────────────────────────────────

def owner_only(func):
    """只有 ALLOWED_CHAT 的消息才处理，其他一律忽略。"""
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.id != ALLOWED_CHAT:
            await update.message.reply_text("⛔ Unauthorized")
            return
        return await func(update, ctx)
    return wrapper


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _read_latest() -> tuple[int, str] | None:
    try:
        content = RESPONSE_FILE.read_text(encoding="utf-8")
        lines = content.split("\n", 1)
        if lines[0].startswith("seq:"):
            return int(lines[0][4:]), lines[1] if len(lines) > 1 else ""
    except FileNotFoundError:
        pass
    return None


def _read_log(n: int = 5) -> list[dict]:
    import json
    try:
        lines = LOG_FILE.read_text(encoding="utf-8").strip().split("\n")
        entries = []
        for line in lines[-n:]:
            try:
                entries.append(json.loads(line))
            except Exception:
                pass
        return entries
    except FileNotFoundError:
        return []


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.rename(path)


# ── 命令处理器 ────────────────────────────────────────────────────────────────

@owner_only
async def cmd_task(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /task <内容>  — 向 AI 发送一条新任务
    motor_nerve 轮询 telegram_task.txt，发现后注入到对话框
    """
    if not ctx.args:
        await update.message.reply_text("用法：/task 你的指令")
        return

    task_text = " ".join(ctx.args)
    _atomic_write(TASK_FILE, task_text)
    await update.message.reply_text(f"✅ 已发送任务：\n{task_text}")
    log.info(f"Task sent: {task_text[:80]}")


@owner_only
async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/status — 查看 AI 最新回复"""
    result = _read_latest()
    if result is None:
        await update.message.reply_text("📭 暂无回复")
        return

    seq, text = result
    preview = text[:1000] + ("..." if len(text) > 1000 else "")
    await update.message.reply_text(f"📨 seq:{seq}\n\n{preview}")


@owner_only
async def cmd_log(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/log [n] — 查看最近 n 条历史（默认 3）"""
    n = int(ctx.args[0]) if ctx.args else 3
    entries = _read_log(n)
    if not entries:
        await update.message.reply_text("📭 暂无历史")
        return

    msg = ""
    for e in entries:
        ts = datetime.fromtimestamp(e.get("ts", 0)).strftime("%H:%M:%S")
        preview = e.get("text", "")[:200]
        msg += f"[seq:{e.get('seq')} {ts}]\n{preview}\n\n"
    await update.message.reply_text(msg[:4000])


@owner_only
async def cmd_alert(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/alert — 查看所有 ALERT 文件"""
    alerts = list(SHARED_DIR.glob("ALERT_*.txt"))
    if not alerts:
        await update.message.reply_text("✅ 无告警")
        return

    msg = f"⚠️ {len(alerts)} 条告警：\n\n"
    for f in alerts:
        msg += f"**{f.name}**\n{f.read_text()[:300]}\n\n"
    await update.message.reply_text(msg[:4000])


@owner_only
async def cmd_clear_alert(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/clear_alert — 清除所有 ALERT 文件（处理完问题后用）"""
    alerts = list(SHARED_DIR.glob("ALERT_*.txt"))
    for f in alerts:
        f.unlink()
    await update.message.reply_text(f"🗑️ 已清除 {len(alerts)} 条告警")


@owner_only
async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/task <内容>   → 发送任务给 AI\n"
        "/status        → 最新 AI 回复\n"
        "/log [n]       → 最近 n 条历史\n"
        "/alert         → 查看告警\n"
        "/clear_alert   → 清除告警\n"
        "/help          → 本帮助"
    )


@owner_only
async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """普通消息（非命令）直接当 task 处理，更自然"""
    text = update.message.text
    _atomic_write(TASK_FILE, text)
    await update.message.reply_text(f"✅ 已发送：{text[:100]}")


# ── motor_nerve 扩展：轮询 telegram_task.txt ──────────────────────────────────
#
# 在 motor_nerve.py 里加这个逻辑：
#
# TELEGRAM_TASK = SHARED_DIR / "telegram_task.txt"
# TELEGRAM_SENT = SHARED_DIR / ".telegram_task_sent"
#
# def check_telegram_task():
#     if not TELEGRAM_TASK.exists(): return
#     sent_mtime = TELEGRAM_SENT.stat().st_mtime if TELEGRAM_SENT.exists() else 0
#     task_mtime = TELEGRAM_TASK.stat().st_mtime
#     if task_mtime > sent_mtime:
#         task = TELEGRAM_TASK.read_text().strip()
#         # 注入到 AI 输入框（复用现有逻辑）
#         inject_to_browser(task)
#         TELEGRAM_SENT.touch()


# ── 主入口 ────────────────────────────────────────────────────────────────────

def main():
    if not TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN 未设置")
    if not ALLOWED_CHAT:
        raise ValueError("TELEGRAM_CHAT_ID 未设置")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("task", cmd_task))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("log", cmd_log))
    app.add_handler(CommandHandler("alert", cmd_alert))
    app.add_handler(CommandHandler("clear_alert", cmd_clear_alert))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    log.info(f"Bot started. Listening for chat_id={ALLOWED_CHAT}")
    SHARED_DIR.mkdir(parents=True, exist_ok=True)

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
