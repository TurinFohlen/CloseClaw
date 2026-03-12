#!/usr/bin/env python3
"""
motor_nerve.py — 运动神经

轮询 /shared/{source}/response_latest.txt，
发现新 seq 时执行 on_new_response()。

同时轮询 /shared/{source}/prompt.txt（来自 telegram_bridge 或外部写入），
有新 prompt 时注入到 AI 输入框，并同步写 prompt.txt 供 brainstem 记录。

多源：每个容器通过 CLOSECLAW_SOURCE 读自己命名空间下的文件，互不干扰。
"""

import os
import sys
import subprocess
import time
from pathlib import Path

SHARED_DIR    = Path(os.getenv("CLOSECLAW_SHARED", "/shared"))
SOURCE        = os.getenv("CLOSECLAW_SOURCE", "default")
NS_DIR        = SHARED_DIR / SOURCE

LATEST_FILE   = NS_DIR / "response_latest.txt"
PROMPT_FILE   = NS_DIR / "prompt.txt"           # 外部写入的待发 prompt
PROMPT_SENT   = NS_DIR / ".prompt_sent"         # 已注入标记（乐观锁）

POLL_INTERVAL = 0.3


# ── 文件读取 ──────────────────────────────────────────────────────────────────

def read_latest() -> tuple[int, str] | None:
    try:
        content = LATEST_FILE.read_text(encoding="utf-8")
        lines = content.split("\n", 1)
        if lines[0].startswith("seq:"):
            return int(lines[0][4:]), lines[1] if len(lines) > 1 else ""
    except (FileNotFoundError, ValueError):
        pass
    return None


def check_pending_prompt() -> str | None:
    """
    检查是否有待注入的 prompt（来自 telegram_bridge 或手动写文件）。
    乐观锁：先标记已读，再注入。
    """
    if not PROMPT_FILE.exists():
        return None
    sent_mtime = PROMPT_SENT.stat().st_mtime if PROMPT_SENT.exists() else 0
    if PROMPT_FILE.stat().st_mtime <= sent_mtime:
        return None
    # 先标记（乐观锁）
    PROMPT_SENT.touch()
    return PROMPT_FILE.read_text(encoding="utf-8").strip()


# ── 执行动作 ──────────────────────────────────────────────────────────────────

def inject_prompt(text: str) -> None:
    """
    把 prompt 注入到 AI 网页输入框。
    用 xclip 走剪贴板，比 xdotool type 快，适合长文本。
    """
    proc = subprocess.Popen(
        ["xclip", "-selection", "clipboard"],
        stdin=subprocess.PIPE
    )
    proc.communicate(text.encode("utf-8"))
    time.sleep(0.1)
    subprocess.run(["xdotool", "key", "ctrl+v"], check=False)
    time.sleep(0.1)
    subprocess.run(["xdotool", "key", "Return"], check=False)
    print(f"[motor_nerve/{SOURCE}] injected prompt ({len(text)} chars)")


def on_new_response(text: str) -> None:
    """
    收到 AI 新回复时调用。
    在这里接入宏引擎或自动化逻辑。
    现在只打印，方便调试。
    """
    print("=" * 60)
    print(text)
    print("=" * 60, flush=True)


# ── 主循环 ────────────────────────────────────────────────────────────────────

def main():
    NS_DIR.mkdir(parents=True, exist_ok=True)
    last_seq = -1
    print(f"[motor_nerve/{SOURCE}] watching {NS_DIR}")

    while True:
        # 1. 有待注入的 prompt？
        prompt = check_pending_prompt()
        if prompt:
            inject_prompt(prompt)

        # 2. 有新回复？
        result = read_latest()
        if result is not None:
            seq, text = result
            if seq != last_seq:
                last_seq = seq
                print(f"[motor_nerve/{SOURCE}] new response seq={seq}", file=sys.stderr)
                on_new_response(text)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
