#!/usr/bin/env python3
"""
feedback_nerve.py — 感觉反馈神经

把 /shared/{source}/command_output.txt 和 screen_output.jpg
回传到 AI 对话框，闭合反射弧。

多源：每个容器读自己命名空间，互不干扰。
乐观锁：先标记已发，再上传（防止重复触发）。
"""

import os
import sys
import subprocess
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from mouse_lock import mouse_lock, MouseLockTimeout
from upload_locator import locate_upload_button

SHARED_DIR       = Path(os.getenv("CLOSECLAW_SHARED", "/shared"))
SOURCE           = os.getenv("CLOSECLAW_SOURCE", "default")
NS_DIR           = SHARED_DIR / SOURCE

TEXT_FILE        = NS_DIR / "command_output.txt"
IMAGE_FILE       = NS_DIR / "screen_output.jpg"
SENT_MARKER      = NS_DIR / ".last_sent_seq"

POLL_INTERVAL    = 0.5
UPLOAD_WAIT      = 1.5
FILE_DIALOG_WAIT = 2.0


def _read_seq(file: Path) -> int | None:
    try:
        first = file.read_text(encoding="utf-8").split("\n", 1)[0]
        return int(first[4:]) if first.startswith("seq:") else None
    except (FileNotFoundError, ValueError):
        return None


def _read_last_sent() -> int:
    try:
        return int(SENT_MARKER.read_text().strip())
    except (FileNotFoundError, ValueError):
        return -1


def _xdotool(*args) -> None:
    subprocess.run(["xdotool"] + list(args), check=True)


def _paste_text(text: str) -> None:
    proc = subprocess.Popen(["xclip", "-selection", "clipboard"], stdin=subprocess.PIPE)
    proc.communicate(text.encode("utf-8"))
    time.sleep(0.1)
    _xdotool("key", "ctrl+v")


def send_text_result(text_content: str) -> None:
    message = f"[EXECUTION_RESULT]\n```\n{text_content.strip()}\n```"
    with mouse_lock():
        _paste_text(message)
        time.sleep(0.1)
        _xdotool("key", "Return")
    print(f"[feedback_nerve/{SOURCE}] text sent ({len(text_content)} chars)")


def send_image(image_path: Path) -> None:
    x, y = locate_upload_button()
    with mouse_lock():
        _xdotool("mousemove", str(x), str(y))
        _xdotool("click", "1")
        time.sleep(UPLOAD_WAIT)
        _xdotool("type", "--clearmodifiers", "--delay", "0", str(image_path.absolute()))
        _xdotool("key", "Return")
        time.sleep(FILE_DIALOG_WAIT)
    print(f"[feedback_nerve/{SOURCE}] image sent: {image_path}")


def send_feedback() -> None:
    has_image = IMAGE_FILE.exists()
    has_text  = TEXT_FILE.exists()
    if not has_image and not has_text:
        return
    if has_image:
        send_image(IMAGE_FILE)
    if has_text:
        content = TEXT_FILE.read_text(encoding="utf-8")
        lines = content.split("\n", 2)
        body = lines[2] if len(lines) >= 3 else content
        send_text_result(body)


def run_once() -> None:
    current_seq = _read_seq(TEXT_FILE)
    last_sent   = _read_last_sent()
    if current_seq is not None and current_seq != last_sent:
        print(f"[feedback_nerve/{SOURCE}] new seq={current_seq}")
        # 乐观锁：先标记，再上传
        SENT_MARKER.write_text(str(current_seq))
        try:
            send_feedback()
        except MouseLockTimeout as e:
            print(f"[feedback_nerve/{SOURCE}] lock timeout: {e}")
        except Exception as e:
            print(f"[feedback_nerve/{SOURCE}] error: {e}")


def run_loop() -> None:
    NS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[feedback_nerve/{SOURCE}] watching {NS_DIR}")
    while True:
        run_once()
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    if "--once" in sys.argv:
        run_once()
    else:
        run_loop()
