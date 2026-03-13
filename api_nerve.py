#!/usr/bin/env python3
"""
api_nerve.py — API 模式神经中枢

替代 brainstem（mitmproxy）的角色，直接调用 OpenAI 兼容接口。
输出格式与 brainstem 完全一致：/shared/{source}/response_latest.txt
上层神经系统（motor/feedback/distill）感知不到差异。

支持：
  OpenRouter   https://openrouter.ai/api/v1
  DeepSeek API https://api.deepseek.com
  Groq API     https://api.groq.com/openai/v1
  任何 OpenAI 兼容接口

上下文管理：memory tree 模式
  不传全量历史，只传 /memory/ 目录 tree 结构
  AI 声明需要哪个文件 → 读取注入 → 继续
  delta 更新：AI 输出 <DIFF> 块 → git apply → 文件更新
  O(log n) 复杂度，上下文窗口永远是小常数

用法：
  python3 api_nerve.py                     # 持续运行，轮询 prompt.txt
  python3 api_nerve.py --once "你好"        # 单次调用
  python3 api_nerve.py --interactive        # 交互模式（调试用）

环境变量：
  CLOSECLAW_SOURCE       default     命名空间
  CLOSECLAW_SHARED       /shared     共享目录
  CLOSECLAW_API_BASE     https://openrouter.ai/api/v1
  CLOSECLAW_API_KEY      (必填)
  CLOSECLAW_MODEL        mistralai/mixtral-8x7b-instruct
  CLOSECLAW_MAX_TOKENS   4096
  CLOSECLAW_MEMORY_DIR   /workspace/memory   memory tree 根目录
  CLOSECLAW_WORKSPACE    /workspace
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from threading import Lock

# ── 配置 ────────────────────────────────────────────────────────────────────

SHARED_DIR   = Path(os.getenv("CLOSECLAW_SHARED", "/shared"))
SOURCE       = os.getenv("CLOSECLAW_SOURCE", "default")
NS_DIR       = SHARED_DIR / SOURCE

LATEST_FILE  = NS_DIR / "response_latest.txt"
LOG_FILE     = NS_DIR / "response_log.jsonl"
PROMPT_FILE  = NS_DIR / "prompt.txt"
PROMPT_SENT  = NS_DIR / ".prompt_sent"
ALERT_FILE   = NS_DIR / "ALERT_disk_full.txt"

API_BASE     = os.getenv("CLOSECLAW_API_BASE", "https://openrouter.ai/api/v1")
API_KEY      = os.getenv("CLOSECLAW_API_KEY", "")
MODEL        = os.getenv("CLOSECLAW_MODEL", "mistralai/mixtral-8x7b-instruct")
MAX_TOKENS   = int(os.getenv("CLOSECLAW_MAX_TOKENS", "4096"))

MEMORY_DIR   = Path(os.getenv("CLOSECLAW_MEMORY_DIR", "/workspace/memory"))
WORKSPACE    = Path(os.getenv("CLOSECLAW_WORKSPACE", "/workspace"))

POLL_INTERVAL = 0.5
DISK_STOP_MB  = 50
LOG_ROTATE_MB = 50

_lock = Lock()
_seq  = 0

# ── 对话历史（session内保留，重启清空）──────────────────────────────────────
# API 模式无法像网页端那样天然保留上下文，
# 用 memory tree 弥补：重启后 AI 通过读取文件重建状态。
_history: list[dict] = []


# ── Memory Tree ───────────────────────────────────────────────────────────────

def _build_tree(root: Path, indent: int = 0) -> str:
    """
    生成 memory 目录的 tree 结构字符串。
    只展示文件名和第一行（摘要行），不展示内容。
    O(n) 生成，AI 导航是 O(log n)。
    """
    if not root.exists():
        return ""
    lines = []
    prefix = "  " * indent
    for item in sorted(root.iterdir()):
        if item.name.startswith("."):
            continue
        if item.is_dir():
            lines.append(f"{prefix}📁 {item.name}/")
            lines.append(_build_tree(item, indent + 1))
        else:
            # 读第一行作为摘要
            try:
                first_line = item.read_text(encoding="utf-8").split("\n")[0].strip()
                summary = first_line[:60] + ("…" if len(first_line) > 60 else "")
            except Exception:
                summary = ""
            lines.append(f"{prefix}📄 {item.name}  {summary}")
    return "\n".join(l for l in lines if l)


def _read_file(rel_path: str) -> str:
    """AI 请求读取某个文件时调用。rel_path 相对于 MEMORY_DIR。"""
    target = (MEMORY_DIR / rel_path).resolve()
    # 安全检查：不允许读 memory 目录外的文件
    if not str(target).startswith(str(MEMORY_DIR.resolve())):
        return f"[ERROR] 路径越界：{rel_path}"
    try:
        return target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return f"[ERROR] 文件不存在：{rel_path}"
    except Exception as e:
        return f"[ERROR] 读取失败：{e}"


def _apply_diff(diff_text: str) -> str:
    """
    把 AI 输出的 diff 应用到 workspace。
    git apply 保证原子性和无损性。
    """
    if not WORKSPACE.exists():
        return "[ERROR] workspace 不存在"
    tmp = WORKSPACE / ".ai_patch.diff"
    try:
        tmp.write_text(diff_text, encoding="utf-8")
        result = subprocess.run(
            ["git", "apply", "--whitespace=fix", str(tmp)],
            cwd=str(WORKSPACE),
            capture_output=True,
            text=True,
        )
        tmp.unlink(missing_ok=True)
        if result.returncode == 0:
            return "[OK] diff applied"
        else:
            return f"[ERROR] git apply failed:\n{result.stderr}"
    except Exception as e:
        tmp.unlink(missing_ok=True)
        return f"[ERROR] {e}"


# ── System Prompt ─────────────────────────────────────────────────────────────

def _build_system_prompt() -> str:
    tree = _build_tree(MEMORY_DIR)
    tree_section = f"""
## 你的记忆目录

以下是你的 memory tree。每次对话你只会收到这个结构，不会收到全量历史。
需要什么文件，直接声明，我会把内容给你。

```
{tree if tree else "(空，尚无记忆文件)"}
```

读取文件：在回复中写 `[READ: path/to/file.md]`
更新文件：在回复中写 diff 块（git diff 格式），用 `<DIFF>` 和 `</DIFF>` 包裹
""" if MEMORY_DIR.exists() else ""

    return f"""你是 CloseClaw 系统的 AI worker（source={SOURCE}，model={MODEL}）。

{tree_section}

## 输出规范

执行类任务：
- 代码块用 ```语言 包裹
- 操作指令用 [ACTION: xxx] 标记
- 每次只给一步，等执行结果再继续

状态报告（任务结束时必须输出）：
STATUS: done | running | blocked
如果 blocked：BLOCKED_REASON: {{原因}}
如果 done 且有文件变更：在回复中附上 <DIFF>...</DIFF> 块

## 行为准则
- 不做任务范围外的操作
- 不读取 memory 目录外的文件
- 失败超过3次报 blocked，不无限重试
"""


# ── API 调用 ─────────────────────────────────────────────────────────────────

def _call_api(messages: list[dict]) -> str:
    """
    调用 OpenAI 兼容接口，返回 assistant 文字内容。
    支持流式和非流式，自动检测。
    """
    if not API_KEY:
        raise ValueError("CLOSECLAW_API_KEY 未设置")

    payload = json.dumps({
        "model":       MODEL,
        "messages":    messages,
        "max_tokens":  MAX_TOKENS,
        "stream":      False,
    }).encode("utf-8")

    headers = {
        "Content-Type":  "application/json",
        "Authorization": f"Bearer {API_KEY}",
        # OpenRouter 需要这两个 header
        "HTTP-Referer":  "https://github.com/TurinFohlen/CloseClaw",
        "X-Title":       "CloseClaw",
    }

    req = urllib.request.Request(
        f"{API_BASE.rstrip('/')}/chat/completions",
        data=payload,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"API HTTP {e.code}: {body[:400]}")


# ── 回复后处理：提取 READ 请求和 DIFF 块 ────────────────────────────────────

def _process_response(response: str) -> str:
    """
    处理 AI 回复中的特殊指令：
    1. [READ: path] → 读取文件，追加到对话，让 AI 继续
    2. <DIFF>...</DIFF> → 应用 patch
    返回最终呈现给用户的文字。
    """
    # 处理文件读取请求
    read_requests = re.findall(r'\[READ:\s*([^\]]+)\]', response)
    if read_requests:
        file_contents = []
        for path in read_requests:
            content = _read_file(path.strip())
            file_contents.append(f"### {path.strip()}\n```\n{content}\n```")

        # 把文件内容追加到历史，让 AI 继续
        _history.append({"role": "assistant", "content": response})
        _history.append({
            "role": "user",
            "content": "[FILE_CONTENTS]\n" + "\n\n".join(file_contents) + "\n[/FILE_CONTENTS]\n请继续。"
        })

        # 递归调用，获取 AI 读完文件后的回复
        next_response = _call_api(
            [{"role": "system", "content": _build_system_prompt()}] + _history
        )
        return _process_response(next_response)

    # 处理 diff 块
    diff_blocks = re.findall(r'<DIFF>(.*?)</DIFF>', response, re.DOTALL)
    for diff in diff_blocks:
        result = _apply_diff(diff.strip())
        print(f"[api_nerve/{SOURCE}] diff: {result}")

    return response


# ── 磁盘检查 ─────────────────────────────────────────────────────────────────

def _check_disk() -> bool:
    free = shutil.disk_usage(NS_DIR).free / (1024 * 1024)
    if free < DISK_STOP_MB:
        try:
            ALERT_FILE.write_text(f"DISK FULL: {free:.0f}MB. api_nerve/{SOURCE} stopped.\n")
        except Exception:
            pass
        return False
    return True


# ── 落盘（与 brainstem 完全一致的格式）──────────────────────────────────────

def _write_response(text: str) -> None:
    global _seq
    NS_DIR.mkdir(parents=True, exist_ok=True)
    if not _check_disk():
        return

    with _lock:
        _seq += 1
        seq = _seq
        ts  = time.time()

        # response_latest.txt
        tmp = LATEST_FILE.with_suffix(".tmp")
        tmp.write_text(f"seq:{seq}\n{text}", encoding="utf-8")
        tmp.rename(LATEST_FILE)

        # response_log.jsonl
        if LOG_FILE.exists() and LOG_FILE.stat().st_size / (1024*1024) > LOG_ROTATE_MB:
            LOG_FILE.rename(LOG_FILE.with_suffix(".jsonl.1"))
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "seq": seq, "ts": ts, "source": SOURCE,
                "model": MODEL, "text": text,
            }, ensure_ascii=False) + "\n")

        # distill_hook
        try:
            import distill_hook
            last_prompt = _history[-2]["content"] if len(_history) >= 2 else ""
            distill_hook.record(
                prompt=last_prompt, response=text,
                source=SOURCE, seq=seq,
            )
        except ImportError:
            pass

    print(f"[api_nerve/{SOURCE}] seq={seq} model={MODEL} len={len(text)}")


# ── 单次查询 ─────────────────────────────────────────────────────────────────

def query(prompt: str) -> str:
    """
    发送 prompt，返回 AI 回复。
    维护 session 内对话历史。
    """
    _history.append({"role": "user", "content": prompt})

    messages = [
        {"role": "system", "content": _build_system_prompt()}
    ] + _history

    response = _call_api(messages)
    response = _process_response(response)

    _history.append({"role": "assistant", "content": response})
    _write_response(response)
    return response


# ── 主循环：轮询 prompt.txt ───────────────────────────────────────────────────

def run_loop() -> None:
    NS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[api_nerve/{SOURCE}] model={MODEL} base={API_BASE}")
    print(f"[api_nerve/{SOURCE}] watching {PROMPT_FILE}")

    while True:
        try:
            if PROMPT_FILE.exists():
                sent_mtime = PROMPT_SENT.stat().st_mtime if PROMPT_SENT.exists() else 0
                if PROMPT_FILE.stat().st_mtime > sent_mtime:
                    PROMPT_SENT.touch()  # 乐观锁
                    prompt = PROMPT_FILE.read_text(encoding="utf-8").strip()
                    if prompt:
                        print(f"[api_nerve/{SOURCE}] prompt: {prompt[:80]}")
                        response = query(prompt)
                        print(f"[api_nerve/{SOURCE}] done ({len(response)} chars)")
        except KeyboardInterrupt:
            print("\n[api_nerve] stopped")
            break
        except Exception as e:
            print(f"[api_nerve/{SOURCE}] error: {e}")
            # 写 ALERT，不崩溃
            try:
                (NS_DIR / "ALERT_api_error.txt").write_text(
                    f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {e}\n"
                )
            except Exception:
                pass

        time.sleep(POLL_INTERVAL)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not API_KEY:
        print("ERROR: CLOSECLAW_API_KEY 未设置", file=sys.stderr)
        sys.exit(1)

    if "--once" in sys.argv:
        idx = sys.argv.index("--once")
        prompt = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "你好"
        print(query(prompt))

    elif "--interactive" in sys.argv:
        print(f"[api_nerve] interactive mode  model={MODEL}")
        print("输入 quit 退出\n")
        while True:
            try:
                prompt = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if prompt.lower() in ("quit", "exit", "q"):
                break
            if not prompt:
                continue
            response = query(prompt)
            print(f"\nai> {response}\n")

    else:
        run_loop()
