#!/usr/bin/env python3
"""
macro_nerve.py — 宏序列执行神经

AI 提前规划动作序列，macro_nerve 以机器速度执行。
适用场景：音游背板、连点抢红包、帧级精确操作。

与 motor_nerve 的区别：
  motor_nerve    AI 实时决策，每步等 AI 回复，~300ms 延迟
  macro_nerve    AI 一次性输出完整序列，机器执行，<1ms 精度

宏序列格式（AI 输出或手动编写）：
  [MACRO]
  click 960 540
  click 960 540 delay=0
  key space
  key ctrl+c
  wait 100
  wait_image perfect.png timeout=5000
  scroll up 3
  drag 100 200 800 600 duration=500
  type 你好世界
  repeat 50
    click 960 540 delay=16
  end
  [/MACRO]

使用方式：
  # 轮询模式（持续运行，监听 macro.txt）
  python3 macro_nerve.py

  # 直接执行宏文件
  python3 macro_nerve.py --file my_macro.txt

  # 执行内联宏
  python3 macro_nerve.py --inline "click 960 540\nkey space\nwait 100"

  # 从 AI 回复里提取并执行
  python3 macro_nerve.py --from-response /shared/claude/response_latest.txt

环境变量：
  CLOSECLAW_SHARED      /shared
  CLOSECLAW_SOURCE      default
  MACRO_POLL_INTERVAL   0.2   秒
  MACRO_DEFAULT_DELAY   50    两个动作之间的默认延迟（ms）
  MACRO_DRY_RUN         0     设为1则只打印不执行（调试用）
"""

import os
import re
import sys
import time
import subprocess
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

# ── 配置 ─────────────────────────────────────────────────────────────────────

SHARED_DIR     = Path(os.getenv("CLOSECLAW_SHARED", "/shared"))
SOURCE         = os.getenv("CLOSECLAW_SOURCE", "default")
NS_DIR         = SHARED_DIR / SOURCE

MACRO_FILE     = NS_DIR / "macro.txt"        # AI 写入，macro_nerve 执行
MACRO_DONE     = NS_DIR / ".macro_done"      # 执行完成标记
MACRO_RESULT   = NS_DIR / "macro_result.txt" # 执行结果回写

POLL_INTERVAL  = float(os.getenv("MACRO_POLL_INTERVAL", "0.2"))
DEFAULT_DELAY  = int(os.getenv("MACRO_DEFAULT_DELAY", "50"))
DRY_RUN        = os.getenv("MACRO_DRY_RUN", "0") == "1"


# ── 宏指令数据结构 ─────────────────────────────────────────────────────────────

@dataclass
class MacroCmd:
    op:     str
    args:   list = field(default_factory=list)
    kwargs: dict = field(default_factory=dict)


# ── 解析器 ────────────────────────────────────────────────────────────────────

def parse_macro(text: str) -> list[MacroCmd]:
    """
    解析宏文本，支持：
      click x y [delay=ms]
      key keyname [delay=ms]
      wait ms
      wait_image template.png [timeout=ms] [threshold=0.8]
      scroll up|down [n]
      drag x1 y1 x2 y2 [duration=ms]
      type text
      repeat n ... end
    """
    # 提取 [MACRO]...[/MACRO] 块（如果有）
    m = re.search(r'\[MACRO\](.*?)\[/MACRO\]', text, re.DOTALL | re.IGNORECASE)
    if m:
        text = m.group(1)

    lines = [l.strip() for l in text.strip().splitlines() if l.strip() and not l.strip().startswith('#')]
    cmds = []
    i = 0

    while i < len(lines):
        line = lines[i]
        parts = line.split()
        op = parts[0].lower()

        # 解析 key=value 参数
        kwargs = {}
        plain_args = []
        for p in parts[1:]:
            if '=' in p:
                k, v = p.split('=', 1)
                kwargs[k] = v
            else:
                plain_args.append(p)

        if op == "repeat":
            count = int(plain_args[0]) if plain_args else 1
            # 找对应的 end
            body_lines = []
            depth = 1
            i += 1
            while i < len(lines) and depth > 0:
                if lines[i].lower().startswith("repeat"):
                    depth += 1
                elif lines[i].lower() == "end":
                    depth -= 1
                    if depth == 0:
                        break
                if depth > 0:
                    body_lines.append(lines[i])
                i += 1
            body_cmds = parse_macro("\n".join(body_lines))
            cmds.append(MacroCmd("repeat", [count, body_cmds], kwargs))
        else:
            cmds.append(MacroCmd(op, plain_args, kwargs))

        i += 1

    return cmds


# ── 执行器 ────────────────────────────────────────────────────────────────────

def _xdo(*args) -> bool:
    """执行 xdotool 命令，返回是否成功。"""
    if DRY_RUN:
        print(f"  [DRY] xdotool {' '.join(str(a) for a in args)}")
        return True
    result = subprocess.run(["xdotool"] + [str(a) for a in args],
                            capture_output=True)
    return result.returncode == 0


def _delay(ms: int) -> None:
    if ms > 0:
        time.sleep(ms / 1000.0)


def _wait_image(template: str, timeout_ms: int = 5000, threshold: float = 0.8) -> bool:
    """等待屏幕出现模板图片，返回是否找到。"""
    try:
        import mss
        import cv2
        import numpy as np
    except ImportError:
        print("  [macro] wait_image 需要：pip install mss opencv-python --break-system-packages")
        return False

    tmpl_path = Path(template)
    if not tmpl_path.exists():
        tmpl_path = NS_DIR / template
    if not tmpl_path.exists():
        print(f"  [macro] 模板文件不存在：{template}")
        return False

    tmpl = cv2.imread(str(tmpl_path), cv2.IMREAD_GRAYSCALE)
    deadline = time.time() + timeout_ms / 1000.0

    with mss.mss() as sct:
        while time.time() < deadline:
            screen = np.array(sct.grab(sct.monitors[0]))
            gray   = cv2.cvtColor(screen, cv2.COLOR_BGRA2GRAY)
            res    = cv2.matchTemplate(gray, tmpl, cv2.TM_CCOEFF_NORMED)
            if res.max() >= threshold:
                return True
            time.sleep(0.05)

    return False


def execute_cmd(cmd: MacroCmd) -> bool:
    """执行单条宏指令，返回是否成功。"""
    op     = cmd.op
    args   = cmd.args
    kwargs = cmd.kwargs
    delay  = int(kwargs.get("delay", DEFAULT_DELAY))

    if op == "click":
        x, y = int(args[0]), int(args[1])
        ok = _xdo("mousemove", x, y) and _xdo("click", 1)
        _delay(delay)
        return ok

    elif op == "doubleclick":
        x, y = int(args[0]), int(args[1])
        ok = _xdo("mousemove", x, y) and _xdo("click", "--repeat", 2, "--delay", 50, 1)
        _delay(delay)
        return ok

    elif op == "rightclick":
        x, y = int(args[0]), int(args[1])
        ok = _xdo("mousemove", x, y) and _xdo("click", 3)
        _delay(delay)
        return ok

    elif op == "key":
        keyname = args[0] if args else ""
        ok = _xdo("key", keyname)
        _delay(delay)
        return ok

    elif op == "keydown":
        ok = _xdo("keydown", args[0])
        _delay(delay)
        return ok

    elif op == "keyup":
        ok = _xdo("keyup", args[0])
        _delay(delay)
        return ok

    elif op == "type":
        text = " ".join(args)
        ok = _xdo("type", "--clearmodifiers", "--", text)
        _delay(delay)
        return ok

    elif op == "wait":
        ms = int(args[0]) if args else DEFAULT_DELAY
        _delay(ms)
        return True

    elif op == "wait_image":
        template  = args[0] if args else ""
        timeout   = int(kwargs.get("timeout", 5000))
        threshold = float(kwargs.get("threshold", 0.8))
        found = _wait_image(template, timeout, threshold)
        if not found:
            print(f"  [macro] wait_image timeout: {template}")
        return found

    elif op == "scroll":
        direction = args[0].lower() if args else "down"
        n         = int(args[1]) if len(args) > 1 else 3
        btn       = 4 if direction == "up" else 5
        for _ in range(n):
            _xdo("click", btn)
            _delay(16)
        return True

    elif op == "drag":
        x1, y1, x2, y2 = int(args[0]), int(args[1]), int(args[2]), int(args[3])
        duration = int(kwargs.get("duration", 300))
        ok = (
            _xdo("mousemove", x1, y1) and
            _xdo("mousedown", 1) and
            _xdo("mousemove", "--sync", x2, y2) and
            _xdo("mouseup", 1)
        )
        _delay(duration)
        return ok

    elif op == "move":
        x, y = int(args[0]), int(args[1])
        ok = _xdo("mousemove", x, y)
        _delay(delay)
        return ok

    elif op == "screenshot":
        path = args[0] if args else str(NS_DIR / "screenshot.png")
        try:
            import mss
            with mss.mss() as sct:
                sct.shot(output=path)
            return True
        except ImportError:
            subprocess.run(["scrot", path])
            return True

    elif op == "repeat":
        count     = int(args[0])
        body_cmds = args[1]
        for i in range(count):
            for sub_cmd in body_cmds:
                if not execute_cmd(sub_cmd):
                    print(f"  [macro] repeat body failed at iteration {i}")
                    return False
        return True

    elif op in ("noop", "comment", "//"):
        return True

    else:
        print(f"  [macro] unknown op: {op}")
        return False


def execute_macro(text: str) -> dict:
    """执行宏文本，返回结果统计。"""
    cmds = parse_macro(text)
    total   = len(cmds)
    success = 0
    failed  = []

    print(f"[macro_nerve] executing {total} commands")
    start = time.time()

    for i, cmd in enumerate(cmds):
        if DRY_RUN:
            print(f"  [{i+1}/{total}] {cmd.op} {cmd.args} {cmd.kwargs}")
        ok = execute_cmd(cmd)
        if ok:
            success += 1
        else:
            failed.append(f"{i+1}: {cmd.op} {cmd.args}")
            print(f"  [macro_nerve] FAILED: {cmd.op} {cmd.args}")

    elapsed = time.time() - start
    result = {
        "total":   total,
        "success": success,
        "failed":  failed,
        "elapsed_ms": int(elapsed * 1000),
    }
    print(f"[macro_nerve] done: {success}/{total} in {elapsed:.2f}s")
    return result


# ── 从 AI 回复里提取宏序列 ────────────────────────────────────────────────────

def extract_macro_from_response(response_file: Path) -> Optional[str]:
    try:
        content = response_file.read_text(encoding="utf-8")
        # 跳过第一行 seq:N
        lines = content.split("\n", 1)
        text  = lines[1] if len(lines) > 1 else lines[0]

        # 找 [MACRO]...[/MACRO] 块
        m = re.search(r'\[MACRO\](.*?)\[/MACRO\]', text, re.DOTALL | re.IGNORECASE)
        if m:
            return text  # 返回包含完整标记的文本，由 parse_macro 处理
        return None
    except FileNotFoundError:
        return None


# ── 主循环 ────────────────────────────────────────────────────────────────────

def run_loop() -> None:
    NS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[macro_nerve] watching {MACRO_FILE}")
    print(f"[macro_nerve] dry_run={DRY_RUN}  default_delay={DEFAULT_DELAY}ms")

    last_mtime = 0.0

    while True:
        try:
            if MACRO_FILE.exists():
                mtime = MACRO_FILE.stat().st_mtime
                done_mtime = MACRO_DONE.stat().st_mtime if MACRO_DONE.exists() else 0
                if mtime > last_mtime and mtime > done_mtime:
                    last_mtime = mtime
                    MACRO_DONE.touch()  # 乐观锁

                    macro_text = MACRO_FILE.read_text(encoding="utf-8").strip()
                    if macro_text:
                        result = execute_macro(macro_text)
                        import json as _json
                        MACRO_RESULT.write_text(
                            _json.dumps(result, ensure_ascii=False, indent=2)
                        )
        except KeyboardInterrupt:
            print("\n[macro_nerve] stopped")
            break
        except Exception as e:
            print(f"[macro_nerve] error: {e}")

        time.sleep(POLL_INTERVAL)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import json as _json

    parser = argparse.ArgumentParser(description="CloseClaw 宏执行神经")
    parser.add_argument("--file",          help="执行宏文件")
    parser.add_argument("--inline",        help="执行内联宏（\\n分隔）")
    parser.add_argument("--from-response", help="从 AI 回复文件提取并执行宏")
    parser.add_argument("--dry-run", action="store_true", help="只打印不执行")
    args = parser.parse_args()

    if args.dry_run:
        os.environ["MACRO_DRY_RUN"] = "1"
        DRY_RUN = True

    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
        result = execute_macro(text)
        print(_json.dumps(result, ensure_ascii=False, indent=2))

    elif args.inline:
        text = args.inline.replace("\\n", "\n")
        result = execute_macro(text)
        print(_json.dumps(result, ensure_ascii=False, indent=2))

    elif args.from_response:
        macro_text = extract_macro_from_response(Path(args.from_response))
        if macro_text:
            result = execute_macro(macro_text)
            print(_json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("[macro_nerve] AI 回复里没有找到 [MACRO] 块")
            sys.exit(1)

    else:
        run_loop()
