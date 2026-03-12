"""
brainstem.py — mitmproxy addon

拦截 AI 网页端的 SSE 响应流，把完整 assistant 回复落盘到命名空间目录。

多源支持：
    每个容器设置 CLOSECLAW_SOURCE=claude|deepseek|grok|...
    文件落在 /shared/{source}/ 下，容器间完全隔离，互不覆盖。

    /shared/claude/response_latest.txt
    /shared/deepseek/response_latest.txt
    /shared/grok/response_latest.txt

覆盖 AI 提供商（无需改代码）：
    CLOSECLAW_TARGET_HOST=chat.deepseek.com
    CLOSECLAW_TARGET_PATH=/api/v0/chat/completion
"""

import json
import os
import shutil
import time
from pathlib import Path
from threading import Lock

from mitmproxy import http

# ── 配置 ────────────────────────────────────────────────────────────────────

SHARED_DIR  = Path(os.getenv("CLOSECLAW_SHARED", "/shared"))
SOURCE      = os.getenv("CLOSECLAW_SOURCE", "default")
NS_DIR      = SHARED_DIR / SOURCE

LATEST_FILE = NS_DIR / "response_latest.txt"
LOG_FILE    = NS_DIR / "response_log.jsonl"
PROMPT_FILE = NS_DIR / "prompt.txt"
ALERT_FILE  = NS_DIR / "ALERT_disk_full.txt"

TARGET_HOST = os.getenv("CLOSECLAW_TARGET_HOST", "claude.ai")
TARGET_PATH = os.getenv("CLOSECLAW_TARGET_PATH", "/api/organizations")

DISK_WARN_MB  = 200
DISK_STOP_MB  = 50
LOG_ROTATE_MB = 50

_lock = Lock()
_seq  = 0

# ── SSE 解析 ─────────────────────────────────────────────────────────────────

def _parse_sse_stream(raw: bytes) -> str:
    text_chunks = []
    thinking_chunks = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            continue
        delta = obj.get("delta", {})
        dtype = delta.get("type", "")
        if dtype == "text_delta":
            text_chunks.append(delta.get("text", ""))
        elif dtype == "thinking_delta":
            thinking_chunks.append(delta.get("thinking", ""))
    result = "".join(text_chunks)
    if thinking_chunks:
        result = f"<think>{''.join(thinking_chunks)}</think>\n{result}"
    return result.strip()

# ── 磁盘检查 ─────────────────────────────────────────────────────────────────

def _check_disk(path: Path) -> bool:
    free = shutil.disk_usage(path).free / (1024 * 1024)
    if free < DISK_STOP_MB:
        try:
            ALERT_FILE.write_text(
                f"DISK FULL: {free:.0f}MB free. brainstem/{SOURCE} stopped.\n"
                f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            )
        except Exception:
            pass
        print(f"[brainstem/{SOURCE}] DISK STOP ({free:.0f}MB) — dropping")
        return False
    if free < DISK_WARN_MB:
        try:
            ALERT_FILE.write_text(f"DISK WARN: {free:.0f}MB free.\n")
        except Exception:
            pass
        print(f"[brainstem/{SOURCE}] DISK WARN ({free:.0f}MB)")
    else:
        try:
            ALERT_FILE.unlink(missing_ok=True)
        except Exception:
            pass
    return True

# ── 日志轮转 ──────────────────────────────────────────────────────────────────

def _rotate_log_if_needed(log_file: Path) -> None:
    if not log_file.exists():
        return
    if log_file.stat().st_size / (1024 * 1024) < LOG_ROTATE_MB:
        return
    log_file.rename(log_file.with_suffix(".jsonl.1"))
    print(f"[brainstem/{SOURCE}] log rotated")

# ── 原子写 ────────────────────────────────────────────────────────────────────

def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.rename(path)

def _read_last_prompt() -> str:
    try:
        return PROMPT_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""

# ── 核心落盘 ─────────────────────────────────────────────────────────────────

def _write_response(text: str, url: str) -> None:
    global _seq
    NS_DIR.mkdir(parents=True, exist_ok=True)
    if not _check_disk(NS_DIR):
        return
    with _lock:
        _seq += 1
        seq = _seq
        ts  = time.time()
        last_prompt = _read_last_prompt()

        _atomic_write(LATEST_FILE, f"seq:{seq}\n{text}")

        _rotate_log_if_needed(LOG_FILE)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "seq": seq, "ts": ts, "source": SOURCE,
                "url": url, "prompt": last_prompt, "text": text,
            }, ensure_ascii=False) + "\n")

        try:
            import distill_hook
            distill_hook.record(
                prompt=last_prompt, response=text,
                source=SOURCE, seq=seq,
            )
        except ImportError:
            pass

# ── mitmproxy addon ───────────────────────────────────────────────────────────

class BrainStem:
    def response(self, flow: http.HTTPFlow) -> None:
        req  = flow.request
        resp = flow.response
        if req.pretty_host != TARGET_HOST:
            return
        if TARGET_PATH not in req.path:
            return
        if req.method != "POST" or resp is None:
            return
        if "text/event-stream" not in resp.headers.get("content-type", ""):
            return
        raw = resp.content
        if not raw:
            return
        text = _parse_sse_stream(raw)
        if not text:
            return
        _write_response(text, url=req.pretty_url)
        print(f"[brainstem/{SOURCE}] seq={_seq} len={len(text)}")

def load(l):  # noqa: E741
    NS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[brainstem] source={SOURCE} ns={NS_DIR} target={TARGET_HOST}")
    return BrainStem()
