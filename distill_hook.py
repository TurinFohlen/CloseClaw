#!/usr/bin/env python3
"""
distill_hook.py — 蒸馏数据采集钩子

brainstem 每次落盘时顺手调用，
把 (prompt, response, metadata) 存成 HuggingFace datasets 兼容格式。

现在：只收集数据，不训练。
以后：datasets.load_from_disk() 直接接 transformers/trl/unsloth。

输出格式（JSON Lines，HuggingFace datasets 原生支持）：
    /shared/distill/train.jsonl   — 训练集
    /shared/distill/meta.json     — 数据集统计

字段设计参考 Alpaca / ShareGPT 格式，两者 HuggingFace 上都有大量现成脚本：
    {
        "id": "cc-{seq}-{ts}",
        "source": "claude|deepseek|grok|...",
        "conversation": [
            {"role": "user", "content": "..."},
            {"role": "assistant", "content": "..."}
        ],
        "execution_result": "...",   # 感觉神经回传的结果（天然标注）
        "success": true/false,       # 任务成功与否（来自 git commit 或 BLOCKED）
        "task_context": "...",       # 来自 TASKS.md 的任务上下文
        "timestamp": 1234567890,
        "tokens_estimate": 512       # 粗估，用于统计
    }

为什么这个格式好：
    - conversation 字段兼容 ShareGPT，直接喂 trl/SFTTrainer
    - execution_result + success 是天然的 RLHF 信号，不需要人工标注
    - source 字段支持多模型混合蒸馏（每个模型的强项不同）
"""

import json
import os
import time
from pathlib import Path
from threading import Lock

SHARED_DIR    = Path(os.getenv("CLOSECLAW_SHARED", "/shared"))
DISTILL_DIR   = SHARED_DIR / "distill"
TRAIN_FILE    = DISTILL_DIR / "train.jsonl"
META_FILE     = DISTILL_DIR / "meta.json"

# 超过这个长度的 response 不收集（防止异常输出污染数据集）
MAX_RESPONSE_CHARS = 32_000

_lock = Lock()


# ── 核心接口（brainstem 调用这一个函数就够了）────────────────────────────────

def record(
    prompt: str,
    response: str,
    *,
    source: str = "unknown",          # "claude" / "deepseek" / "grok" 等
    execution_result: str = "",        # command_output.txt 的内容
    success: bool | None = None,       # True/False/None(未知)
    task_context: str = "",            # TASKS.md 里的任务描述
    seq: int = 0,
) -> None:
    """
    记录一条训练样本。线程安全。

    brainstem._write_response() 里加一行：
        distill_hook.record(
            prompt=last_user_message,  # 需要 brainstem 记录上行消息
            response=text,
            source="claude",
            seq=seq,
        )
    """
    if len(response) > MAX_RESPONSE_CHARS:
        return  # 异常长输出，跳过

    DISTILL_DIR.mkdir(parents=True, exist_ok=True)

    entry = {
        "id": f"cc-{seq}-{int(time.time())}",
        "source": source,
        "conversation": [
            {"role": "user",      "content": prompt},
            {"role": "assistant", "content": response},
        ],
        "execution_result": execution_result,
        "success": success,
        "task_context": task_context,
        "timestamp": time.time(),
        "tokens_estimate": (len(prompt) + len(response)) // 4,  # 粗估
    }

    with _lock:
        with TRAIN_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        _update_meta()


def _update_meta() -> None:
    """更新数据集统计（每次写入后刷新）。"""
    count = 0
    sources: dict[str, int] = {}
    success_count = 0
    token_total = 0

    try:
        for line in TRAIN_FILE.read_text(encoding="utf-8").splitlines():
            e = json.loads(line)
            count += 1
            src = e.get("source", "unknown")
            sources[src] = sources.get(src, 0) + 1
            if e.get("success") is True:
                success_count += 1
            token_total += e.get("tokens_estimate", 0)
    except FileNotFoundError:
        pass

    meta = {
        "total_samples": count,
        "by_source": sources,
        "success_rate": success_count / count if count else 0,
        "estimated_tokens": token_total,
        "last_updated": time.time(),
        # HuggingFace datasets 加载提示
        "load_hint": (
            "from datasets import load_dataset; "
            "ds = load_dataset('json', data_files='train.jsonl')"
        ),
    }
    META_FILE.write_text(json.dumps(meta, indent=2, ensure_ascii=False))


# ── 数据质量过滤（可选，训练前跑一遍）──────────────────────────────────────

def filter_dataset(
    min_response_chars: int = 50,
    require_success: bool = False,
    sources: list[str] | None = None,
) -> list[dict]:
    """
    过滤出高质量样本。

    用法：
        good = distill_hook.filter_dataset(
            min_response_chars=100,
            require_success=True,
            sources=["claude", "deepseek"]
        )
        # 写成新文件喂给训练脚本
        with open("filtered.jsonl", "w") as f:
            for e in good:
                f.write(json.dumps(e) + "\n")
    """
    results = []
    try:
        lines = TRAIN_FILE.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []

    for line in lines:
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue

        response = e.get("conversation", [{}])[-1].get("content", "")
        if len(response) < min_response_chars:
            continue
        if require_success and e.get("success") is not True:
            continue
        if sources and e.get("source") not in sources:
            continue

        results.append(e)

    return results


# ── CLI：查看数据集状态 ────────────────────────────────────────────────────

if __name__ == "__main__":
    if META_FILE.exists():
        print(META_FILE.read_text())
    else:
        print("No data collected yet.")
    print(f"\nRaw data: {TRAIN_FILE}")
    print(f"Samples: {sum(1 for _ in TRAIN_FILE.open()) if TRAIN_FILE.exists() else 0}")
