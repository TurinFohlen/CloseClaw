"""
brainstem.py — mitmproxy addon

拦截 claude.ai 的 SSE 响应流，把完整 assistant 回复落盘到共享文件。

使用：
    mitmproxy -s brainstem.py --listen-port 8080
    或
    mitmdump -s brainstem.py --listen-port 8080  # 无 UI，适合后台运行

浏览器配置：
    代理 → 127.0.0.1:8080
    首次使用需信任 mitmproxy CA：
    curl -x http://127.0.0.1:8080 http://mitm.it/cert/pem -o mitmproxy-ca.pem
    # Debian: cp mitmproxy-ca.pem /usr/local/share/ca-certificates/mitmproxy.crt && update-ca-certificates
    # Chrome: Settings → Security → Manage Certificates → Import

输出文件（原子写，不会读到半截内容）：
    /shared/response_latest.txt   — 最新一条完整回复
    /shared/response_log.jsonl    — 追加历史（每行一个 JSON）

文件格式（response_latest.txt）：
    第一行：seq:<序号>  （运动神经用这个判断是否有新内容，轮询比较 seq 即可）
    第二行起：回复正文
"""

import json
import os
import re
import time
from pathlib import Path
from threading import Lock

from mitmproxy import http

# ── 配置 ────────────────────────────────────────────────────────────────────

SHARED_DIR   = Path(os.getenv("CLOSECLAW_SHARED", "/shared"))
LATEST_FILE  = SHARED_DIR / "response_latest.txt"
LOG_FILE     = SHARED_DIR / "response_log.jsonl"

# claude.ai 的 completions SSE endpoint（路径前缀匹配）
TARGET_HOST  = "claude.ai"
TARGET_PATH  = "/api/organizations"   # 实际路径形如 /api/organizations/<id>/chat_conversations/<id>/completion

# ── 内部状态 ─────────────────────────────────────────────────────────────────

_lock = Lock()
_seq  = 0   # 单调递增，让轮询脚本可以用 seq 号判断"是否有新消息"

# ── SSE 解析 ─────────────────────────────────────────────────────────────────

def _parse_sse_stream(raw: bytes) -> str:
    """
    从原始 SSE bytes 中提取 assistant 文字内容。
    claude.ai SSE 格式（2025-03 实测）：

        data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"你好"}}
        data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"，"}}
        data: {"type":"message_stop"}

    只取 text_delta，忽略 thinking_delta / tool_use / ping 等。
    """
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

    # 如果有 thinking block，包起来（parser.py 那边能识别）
    if thinking_chunks:
        thinking = "".join(thinking_chunks)
        result = f"<think>{thinking}</think>\n{result}"

    return result.strip()


# ── 原子写文件 ────────────────────────────────────────────────────────────────

def _atomic_write(path: Path, content: str) -> None:
    """
    写临时文件再 rename，保证读方永远拿到完整内容。
    rename 在同一文件系统上是原子操作（POSIX 保证）。
    """
    tmp = path.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.rename(path)


def _write_response(text: str, url: str) -> None:
    global _seq
    SHARED_DIR.mkdir(parents=True, exist_ok=True)

    with _lock:
        _seq += 1
        seq = _seq
        ts  = time.time()

        # response_latest.txt
        payload = f"seq:{seq}\n{text}"
        _atomic_write(LATEST_FILE, payload)

        # response_log.jsonl（追加）
        record = json.dumps({
            "seq": seq,
            "ts":  ts,
            "url": url,
            "text": text,
        }, ensure_ascii=False)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(record + "\n")


# ── mitmproxy addon ───────────────────────────────────────────────────────────

class BrainStem:
    def response(self, flow: http.HTTPFlow) -> None:
        req  = flow.request
        resp = flow.response

        # 只处理 claude.ai 的 completion endpoint
        if req.pretty_host != TARGET_HOST:
            return
        if TARGET_PATH not in req.path:
            return
        if req.method != "POST":
            return
        if resp is None:
            return

        content_type = resp.headers.get("content-type", "")
        if "text/event-stream" not in content_type:
            # 非 SSE（可能是其他 API 调用），跳过
            return

        raw = resp.content
        if not raw:
            return

        text = _parse_sse_stream(raw)
        if not text:
            return   # 空响应（ping / tool_use only），不落盘

        _write_response(text, url=req.pretty_url)
        print(f"[brainstem] seq={_seq} len={len(text)} → {LATEST_FILE}")


def load(l):  # noqa: E741 — mitmproxy 约定入口
    return BrainStem()
