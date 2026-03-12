#!/usr/bin/env python3
"""
feedback_nerve.py — 感觉反馈神经

把 /shared/ 里的执行结果（文本 + 图片）上传回 claude.ai 对话框，
闭合反射弧：AI 指令 → 执行 → 结果 → AI 看到。

用法：
    # 自动模式（文件有更新就回传）
    python feedback_nerve.py

    # 单次触发（外部调用）
    python feedback_nerve.py --once

依赖：
    xdotool（文字注入）
    xdotool + file dialog（图片上传，需要先 calibrate 按钮位置）

图片上传流程：
    1. mouse_lock 获取鼠标控制权
    2. xdotool click 上传按钮
    3. 等文件对话框弹出
    4. xdotool type 文件路径 + Enter
    5. 等上传完成
    6. 释放锁
"""

import os
import sys
import subprocess
import time
from pathlib import Path

# 添加项目根到 path（独立脚本运行时用）
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from closeclaw.control.mouse_lock import mouse_lock, MouseLockTimeout
from closeclaw.control.upload_locator import locate_upload_button

SHARED_DIR       = Path(os.getenv("CLOSECLAW_SHARED", "/shared"))
TEXT_FILE        = SHARED_DIR / "command_output.txt"
IMAGE_FILE       = SHARED_DIR / "screen_output.jpg"
SENT_MARKER      = SHARED_DIR / ".last_sent_seq"   # 记录上次已回传的 seq

POLL_INTERVAL    = 0.5    # 轮询文件变化间隔
UPLOAD_WAIT      = 1.5    # 点击上传按钮后等文件对话框弹出的秒数
FILE_DIALOG_WAIT = 2.0    # 输入路径后等上传完成的秒数
TYPE_DELAY_MS    = 0      # xdotool type 字符间隔（ms），0=最快


# ── 内部工具 ──────────────────────────────────────────────────────────────────

def _read_seq(file: Path) -> int | None:
    try:
        first_line = file.read_text(encoding="utf-8").split("\n", 1)[0]
        if first_line.startswith("seq:"):
            return int(first_line[4:])
    except (FileNotFoundError, ValueError):
        pass
    return None


def _read_last_sent() -> int:
    try:
        return int(SENT_MARKER.read_text().strip())
    except (FileNotFoundError, ValueError):
        return -1


def _write_last_sent(seq: int) -> None:
    SENT_MARKER.write_text(str(seq))


def _xdotool(*args) -> None:
    subprocess.run(["xdotool"] + list(args), check=True)


def _paste_text(text: str) -> None:
    """剪贴板注入，比 xdotool type 快，适合长文本。"""
    proc = subprocess.Popen(
        ["xclip", "-selection", "clipboard"],
        stdin=subprocess.PIPE
    )
    proc.communicate(text.encode("utf-8"))
    time.sleep(0.1)
    _xdotool("key", "ctrl+v")


# ── 核心操作 ──────────────────────────────────────────────────────────────────

def send_text_result(text_content: str) -> None:
    """把文字结果注入到输入框并提交。"""
    message = f"[EXECUTION_RESULT]\n```\n{text_content.strip()}\n```"
    with mouse_lock():
        _paste_text(message)
        time.sleep(0.1)
        _xdotool("key", "Return")
    print(f"[feedback_nerve] text sent ({len(text_content)} chars)", flush=True)


def send_image(image_path: Path) -> None:
    """
    点击上传按钮 → 文件对话框 → 输入路径 → 上传图片。
    坐标从 upload_locator 缓存读取（opencv 模板匹配定位，一次性）。
    """
    x, y = locate_upload_button()

    with mouse_lock():
        # 1. 点上传按钮
        _xdotool("mousemove", str(x), str(y))
        _xdotool("click", "1")
        time.sleep(UPLOAD_WAIT)

        # 2. 文件对话框接受键盘输入：直接 type 路径
        _xdotool("type", "--clearmodifiers",
                 f"--delay", str(TYPE_DELAY_MS),
                 str(image_path.absolute()))
        _xdotool("key", "Return")
        time.sleep(FILE_DIALOG_WAIT)

    print(f"[feedback_nerve] image sent: {image_path}", flush=True)


def send_feedback(text_file: Path, image_file: Path) -> None:
    """
    完整回传：先上传图片（如果存在），再发文字结果。
    顺序重要：图片附在消息里，文字是这条消息的内容。
    """
    has_image = image_file.exists()
    has_text  = text_file.exists()

    if not has_image and not has_text:
        return

    if has_image:
        send_image(image_file)

    if has_text:
        content = text_file.read_text(encoding="utf-8")
        # 去掉 seq: 和 source: 头两行，只发正文
        lines = content.split("\n", 2)
        body  = lines[2] if len(lines) >= 3 else content
        send_text_result(body)


# ── 主循环 ────────────────────────────────────────────────────────────────────

def run_once() -> None:
    current_seq = _read_seq(TEXT_FILE)
    last_sent   = _read_last_sent()

    if current_seq is not None and current_seq != last_sent:
        print(f"[feedback_nerve] new seq={current_seq}, sending...", flush=True)
        try:
            send_feedback(TEXT_FILE, IMAGE_FILE)
            _write_last_sent(current_seq)
        except MouseLockTimeout as e:
            print(f"[feedback_nerve] lock timeout: {e}", flush=True)
        except Exception as e:
            print(f"[feedback_nerve] error: {e}", flush=True)


def run_loop() -> None:
    print(f"[feedback_nerve] watching {TEXT_FILE} (interval={POLL_INTERVAL}s)")
    while True:
        run_once()
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    if "--once" in sys.argv:
        run_once()
    else:
        run_loop()
