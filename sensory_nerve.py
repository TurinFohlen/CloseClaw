#!/usr/bin/env python3
"""
sensory_nerve.py — 感觉神经

把命令执行结果写到 /shared/command_output.txt，
供大脑下一轮对话读取（"上一步执行结果是什么"）。

两种用法：

1. 管道模式（推荐）：
    python my_script.py | python sensory_nerve.py

2. 直接执行模式：
    python sensory_nerve.py -- bash -c "ls -la && git status"

写完后打印 seq 号到 stderr，方便确认落盘成功。
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

SHARED_DIR   = Path(os.getenv("CLOSECLAW_SHARED", "/shared"))
OUTPUT_FILE  = SHARED_DIR / "command_output.txt"
LOG_FILE     = SHARED_DIR / "command_log.jsonl"
MAX_BYTES    = 64 * 1024   # 超过 64KB 截断，防止大模型 context 爆掉

_seq = 0


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.rename(path)


def write_output(text: str, source: str = "stdin") -> int:
    global _seq
    SHARED_DIR.mkdir(parents=True, exist_ok=True)

    if len(text.encode()) > MAX_BYTES:
        text = text[:MAX_BYTES] + f"\n[truncated at {MAX_BYTES} bytes]"

    _seq += 1
    ts = time.time()

    payload = f"seq:{_seq}\nsource:{source}\n{text}"
    _atomic_write(OUTPUT_FILE, payload)

    record = json.dumps({"seq": _seq, "ts": ts, "source": source, "text": text},
                        ensure_ascii=False)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(record + "\n")

    print(f"[sensory_nerve] seq={_seq} source={source} len={len(text)} → {OUTPUT_FILE}",
          file=sys.stderr)
    return _seq


def run_command(args: list[str]) -> str:
    """执行命令，捕获 stdout+stderr，返回合并输出。"""
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=120,
        )
        out = result.stdout
        if result.stderr:
            out += f"\n[stderr]\n{result.stderr}"
        if result.returncode != 0:
            out += f"\n[exit code: {result.returncode}]"
        return out
    except subprocess.TimeoutExpired:
        return "[command timed out after 120s]"
    except Exception as e:
        return f"[error running command: {e}]"


def main():
    # 模式判断：有 -- 则执行命令，否则读 stdin
    if "--" in sys.argv:
        idx = sys.argv.index("--")
        cmd = sys.argv[idx + 1:]
        if not cmd:
            print("Usage: sensory_nerve.py -- <command> [args...]", file=sys.stderr)
            sys.exit(1)
        print(f"[sensory_nerve] running: {' '.join(cmd)}", file=sys.stderr)
        output = run_command(cmd)
        write_output(output, source=" ".join(cmd))
    else:
        # 管道模式：读 stdin 直到 EOF
        text = sys.stdin.read()
        write_output(text, source="stdin")


if __name__ == "__main__":
    main()
