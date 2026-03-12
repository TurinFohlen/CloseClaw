#!/usr/bin/env python3
"""
motor_nerve.py — 运动神经

轮询 /shared/response_latest.txt，
发现新 seq 时把内容打印到 stdout（或转发给宏引擎）。

单独运行用于调试：
    python motor_nerve.py

与 AutoKey / xdotool 集成：
    把 on_new_response(text) 里的 print() 换成 xdotool 注入即可。
"""

import os
import sys
import time
from pathlib import Path

SHARED_DIR   = Path(os.getenv("CLOSECLAW_SHARED", "/shared"))
LATEST_FILE  = SHARED_DIR / "response_latest.txt"
POLL_INTERVAL = 0.3   # 秒，和 Poller 的 streaming_interval 对齐


def read_latest() -> tuple[int, str] | None:
    """
    返回 (seq, text)，文件不存在或格式错误返回 None。
    """
    try:
        content = LATEST_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None

    lines = content.split("\n", 1)
    if len(lines) < 2 or not lines[0].startswith("seq:"):
        return None

    try:
        seq = int(lines[0][4:])
    except ValueError:
        return None

    return seq, lines[1]


def on_new_response(text: str) -> None:
    """
    处理新回复。在这里接入你的宏引擎。

    示例：xdotool 注入到活跃窗口
        import subprocess
        subprocess.run(["xdotool", "type", "--clearmodifiers", text])

    现在只是打印，方便调试。
    """
    print("=" * 60)
    print(text)
    print("=" * 60, flush=True)


def main():
    last_seq = -1
    print(f"[motor_nerve] watching {LATEST_FILE} (interval={POLL_INTERVAL}s)")

    while True:
        result = read_latest()
        if result is not None:
            seq, text = result
            if seq != last_seq:
                last_seq = seq
                print(f"[motor_nerve] new response seq={seq}", file=sys.stderr)
                on_new_response(text)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
