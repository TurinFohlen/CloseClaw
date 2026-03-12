"""
mouse_lock.py — 全局鼠标互斥锁

运动神经（motor_nerve）和感觉反馈神经（feedback_nerve）都会操控鼠标。
同一时刻只允许一个持有者，避免点击/输入序列被打断。

用法：
    from closeclaw.control.mouse_lock import mouse_lock

    with mouse_lock():
        subprocess.run(["xdotool", "click", ...])

实现：fcntl.flock — POSIX 原生文件锁，跨进程有效，进程崩溃后内核自动释放。
不用 threading.Lock，因为 motor_nerve / feedback_nerve 是独立进程。
"""

import contextlib
import fcntl
import time
from pathlib import Path

LOCK_FILE = Path("/tmp/closeclaw_mouse.lock")


class MouseLockTimeout(Exception):
    pass


@contextlib.contextmanager
def mouse_lock(timeout: float = 10.0, retry_interval: float = 0.05):
    """
    Context manager. 持有期间独占鼠标/键盘控制权。

    Args:
        timeout: 最长等待秒数，超时抛 MouseLockTimeout。
        retry_interval: 轮询间隔（秒）。

    Example:
        with mouse_lock():
            subprocess.run(["xdotool", "type", text])
    """
    fd = open(LOCK_FILE, "w")
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise MouseLockTimeout(
                        f"Could not acquire mouse_lock within {timeout}s. "
                        "Another nerve process is holding it."
                    )
                time.sleep(retry_interval)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()
