#!/usr/bin/env python3
"""
motor_nerve.py — 运动神经

轮询 /shared/{source}/response_latest.txt，
发现新 seq 时执行 on_new_response()。

同时轮询 /shared/{source}/prompt.txt，
有新 prompt 时验证签名后注入到 AI 输入框。

安全修复：
  prompt.txt 必须带 HMAC-SHA256 签名才会被执行。
  签名密钥来自 CLOSECLAW_PROMPT_SECRET 环境变量。
  没有密钥或签名不匹配的 prompt 写 ALERT 并丢弃。
  
  这防止了：被劫持的 worker-a 直接写 worker-b 的 prompt.txt
  注入指令——没有密钥，写了也不会被执行。
"""

import hashlib
import hmac
import os
import sys
import subprocess
import time
from pathlib import Path

SHARED_DIR     = Path(os.getenv("CLOSECLAW_SHARED", "/shared"))
SOURCE         = os.getenv("CLOSECLAW_SOURCE", "default")
NS_DIR         = SHARED_DIR / SOURCE

LATEST_FILE    = NS_DIR / "response_latest.txt"
PROMPT_FILE    = NS_DIR / "prompt.txt"
PROMPT_SENT    = NS_DIR / ".prompt_sent"

# HMAC 签名密钥——只有知道这个密钥的组件才能写合法 prompt
# 由 docker-compose 注入，worker 容器不持有此密钥
PROMPT_SECRET  = os.getenv("CLOSECLAW_PROMPT_SECRET", "")

POLL_INTERVAL  = 0.3

# ── 签名验证 ──────────────────────────────────────────────────────────────────

def _verify_prompt(content: str) -> tuple[bool, str]:
    """
    prompt.txt 格式：
        sig:{HMAC-SHA256}\n{prompt正文}

    返回 (valid, prompt文字)
    无密钥配置时降级为警告模式（兼容旧部署）。
    """
    if not PROMPT_SECRET:
        # 未配置密钥：接受但写警告
        _write_alert("ALERT_no_prompt_secret.txt",
                     "PROMPT_SECRET 未配置，prompt 未验证签名。"
                     "建议设置 CLOSECLAW_PROMPT_SECRET。")
        return True, content

    lines = content.split("\n", 1)
    if len(lines) < 2 or not lines[0].startswith("sig:"):
        _write_alert("ALERT_unsigned_prompt.txt",
                     f"收到无签名 prompt，已丢弃。\n内容前100字：{content[:100]}")
        return False, ""

    provided_sig = lines[0][4:].strip()
    body         = lines[1]

    expected_sig = hmac.new(
        PROMPT_SECRET.encode(),
        body.encode(),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(provided_sig, expected_sig):
        _write_alert("ALERT_invalid_prompt_sig.txt",
                     f"Prompt 签名验证失败，已丢弃。\n"
                     f"provided={provided_sig[:16]}... "
                     f"expected={expected_sig[:16]}...")
        return False, ""

    return True, body


def _write_alert(name: str, msg: str) -> None:
    try:
        (NS_DIR / name).write_text(
            f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n{msg}\n"
        )
    except Exception:
        pass


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
    检查是否有待注入的 prompt。
    乐观锁 + HMAC 验证。
    """
    if not PROMPT_FILE.exists():
        return None
    sent_mtime = PROMPT_SENT.stat().st_mtime if PROMPT_SENT.exists() else 0
    if PROMPT_FILE.stat().st_mtime <= sent_mtime:
        return None

    PROMPT_SENT.touch()  # 乐观锁：先标记
    content = PROMPT_FILE.read_text(encoding="utf-8").strip()

    valid, body = _verify_prompt(content)
    if not valid:
        print(f"[motor_nerve/{SOURCE}] prompt rejected (invalid sig)")
        return None

    return body


# ── 执行动作 ──────────────────────────────────────────────────────────────────

def inject_prompt(text: str) -> None:
    proc = subprocess.Popen(
        ["xclip", "-selection", "clipboard"],
        stdin=subprocess.PIPE
    )
    proc.communicate(text.encode("utf-8"))
    time.sleep(0.1)
    subprocess.run(["xdotool", "key", "ctrl+v"], check=False)
    time.sleep(0.1)
    subprocess.run(["xdotool", "key", "Return"], check=False)
    print(f"[motor_nerve/{SOURCE}] injected ({len(text)} chars)")


def on_new_response(text: str) -> None:
    print("=" * 60)
    print(text)
    print("=" * 60, flush=True)


# ── 主循环 ────────────────────────────────────────────────────────────────────

def main():
    NS_DIR.mkdir(parents=True, exist_ok=True)
    if not PROMPT_SECRET:
        print(f"[motor_nerve/{SOURCE}] WARNING: CLOSECLAW_PROMPT_SECRET not set")
    else:
        print(f"[motor_nerve/{SOURCE}] prompt signing enabled")
    print(f"[motor_nerve/{SOURCE}] watching {NS_DIR}")

    last_seq = -1
    while True:
        prompt = check_pending_prompt()
        if prompt:
            inject_prompt(prompt)

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
