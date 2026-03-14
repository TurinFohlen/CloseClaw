#!/usr/bin/env python3
"""
ws_nerve.py — WebSocket 实时通信神经

实现 neuro-sdk 兼容协议，让任何支持 neuro-sdk 的游戏/应用
直接接入 CloseClaw 的 AI 决策层，无需宏。

协议：
  游戏 → ws_nerve：startup / context / actions/register / actions/force / action/result
  ws_nerve → 游戏：action（AI 选择的动作）

流程：
  1. 游戏连接 WebSocket（默认 ws://localhost:8000）
  2. 游戏发送 startup，ws_nerve 初始化
  3. 游戏注册可用动作（actions/register）
  4. 游戏发送当前状态（actions/force），要求 AI 做决策
  5. ws_nerve 把状态转成 prompt 发给 AI（写 prompt.txt，带 HMAC 签名）
  6. ws_nerve 等待 AI 回复（轮询 response_latest.txt）
  7. ws_nerve 解析 AI 回复，选择一个合法动作，发回游戏
  8. 游戏执行动作，发送 action/result
  9. 循环

环境变量：
  CLOSECLAW_SHARED        /shared
  CLOSECLAW_SOURCE        default
  CLOSECLAW_PROMPT_SECRET 签名密钥
  WS_NERVE_HOST           0.0.0.0
  WS_NERVE_PORT           8000
  WS_NERVE_TIMEOUT        30     AI 决策超时秒数
"""

import asyncio
import hashlib
import hmac
import json
import os
import time
import uuid
from pathlib import Path

try:
    import websockets
    from websockets.server import WebSocketServerProtocol
except ImportError:
    print("请安装依赖：pip install websockets --break-system-packages")
    raise

# ── 配置 ─────────────────────────────────────────────────────────────────────

SHARED_DIR     = Path(os.getenv("CLOSECLAW_SHARED", "/shared"))
SOURCE         = os.getenv("CLOSECLAW_SOURCE", "default")
NS_DIR         = SHARED_DIR / SOURCE

LATEST_FILE    = NS_DIR / "response_latest.txt"
PROMPT_FILE    = NS_DIR / "prompt.txt"
PROMPT_SECRET  = os.getenv("CLOSECLAW_PROMPT_SECRET", "")

WS_HOST        = os.getenv("WS_NERVE_HOST", "0.0.0.0")
WS_PORT        = int(os.getenv("WS_NERVE_PORT", "8000"))
AI_TIMEOUT     = int(os.getenv("WS_NERVE_TIMEOUT", "30"))

# ── 签名 ──────────────────────────────────────────────────────────────────────

def _sign_prompt(text: str) -> str:
    if not PROMPT_SECRET:
        return text
    sig = hmac.new(PROMPT_SECRET.encode(), text.encode(), hashlib.sha256).hexdigest()
    return f"sig:{sig}\n{text}"


# ── 读取 AI 最新回复 ───────────────────────────────────────────────────────────

def _read_seq() -> tuple[int, str]:
    try:
        content = LATEST_FILE.read_text(encoding="utf-8")
        lines = content.split("\n", 1)
        if lines[0].startswith("seq:"):
            return int(lines[0][4:]), lines[1] if len(lines) > 1 else ""
    except (FileNotFoundError, ValueError):
        pass
    return -1, ""


async def _wait_ai_response(prev_seq: int) -> str:
    """等待 AI 产生新回复，返回回复文字。超时返回空字符串。"""
    deadline = time.time() + AI_TIMEOUT
    while time.time() < deadline:
        seq, text = _read_seq()
        if seq > prev_seq:
            return text
        await asyncio.sleep(0.1)
    return ""


# ── 动作选择：从 AI 回复里解析选择了哪个动作 ─────────────────────────────────

def _parse_action_choice(ai_text: str, registered_actions: dict) -> tuple[str, dict]:
    """
    从 AI 回复里提取动作选择。
    优先找 [ACTION: name] 标记，其次找动作名出现在文本里，最后随机选一个。
    返回 (action_name, params_dict)
    """
    import re

    # 1. 找 [ACTION: name] 或 [ACTION: name {"key": "val"}]
    m = re.search(r'\[ACTION:\s*(\w+)\s*(\{[^]]*\})?\]', ai_text)
    if m:
        name   = m.group(1)
        params = json.loads(m.group(2)) if m.group(2) else {}
        if name in registered_actions:
            return name, params

    # 2. 找 ```json 块里的 {"action": "name", ...}
    m = re.search(r'```(?:json)?\s*(\{[^`]+\})\s*```', ai_text, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(1))
            name = obj.get("action") or obj.get("name") or obj.get("command")
            if name and name in registered_actions:
                params = {k: v for k, v in obj.items() if k not in ("action","name","command")}
                return name, params
        except json.JSONDecodeError:
            pass

    # 3. 动作名直接出现在文本里
    for name in registered_actions:
        if name.lower() in ai_text.lower():
            return name, {}

    # 4. fallback：选第一个注册的动作
    if registered_actions:
        name = next(iter(registered_actions))
        return name, {}

    return "", {}


# ── 构造发给 AI 的 prompt ─────────────────────────────────────────────────────

def _build_prompt(game: str, state: str, query: str, actions: dict) -> str:
    action_list = "\n".join(
        f"  - {name}: {info.get('description','')}"
        + (f"\n    参数: {json.dumps(info.get('schema',{}), ensure_ascii=False)}"
           if info.get('schema') else "")
        for name, info in actions.items()
    )
    return f"""[GAME: {game}]

当前状态：
{state}

任务：
{query}

可选动作：
{action_list}

请选择一个动作执行，用以下格式回复：
[ACTION: 动作名 {{"参数名": "参数值"}}]

如果动作不需要参数：
[ACTION: 动作名]
"""


# ── WebSocket 连接处理 ────────────────────────────────────────────────────────

class GameSession:
    def __init__(self, ws: "WebSocketServerProtocol"):
        self.ws              = ws
        self.game            = "unknown"
        self.registered      : dict[str, dict] = {}   # name → {description, schema}
        self.pending_force   : asyncio.Task | None = None

    async def send(self, msg: dict) -> None:
        await self.ws.send(json.dumps(msg, ensure_ascii=False))
        print(f"[ws_nerve → {self.game}] {msg['command']}")

    async def handle(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            print(f"[ws_nerve] invalid JSON: {raw[:100]}")
            return

        cmd  = msg.get("command", "")
        data = msg.get("data", {}) or {}
        game = msg.get("game", self.game)
        self.game = game

        print(f"[ws_nerve ← {game}] {cmd}")

        if cmd == "startup":
            self.registered.clear()
            if self.pending_force:
                self.pending_force.cancel()
            print(f"[ws_nerve] {game} connected, session initialized")

        elif cmd == "context":
            message = data.get("message", "")
            silent  = data.get("silent", True)
            if not silent:
                # 非静默 context：发给 AI 作为信息
                prev_seq, _ = _read_seq()
                prompt = f"[GAME: {game}] 信息通知：{message}"
                PROMPT_FILE.write_text(_sign_prompt(prompt), encoding="utf-8")

        elif cmd == "actions/register":
            for action in data.get("actions", []):
                name = action.get("name")
                if name:
                    self.registered[name] = {
                        "description": action.get("description", ""),
                        "schema":      action.get("schema", {}),
                    }
            print(f"[ws_nerve] {game} registered actions: {list(self.registered)}")

        elif cmd == "actions/unregister":
            for name in data.get("action_names", []):
                self.registered.pop(name, None)

        elif cmd == "actions/force":
            # 取消上一个未完成的决策
            if self.pending_force and not self.pending_force.done():
                self.pending_force.cancel()
            self.pending_force = asyncio.create_task(
                self._handle_force(data)
            )

        elif cmd == "action/result":
            action_id = data.get("id")
            success   = data.get("success", True)
            message   = data.get("message", "")
            status = "✓" if success else "✗"
            print(f"[ws_nerve] action {action_id} result: {status} {message}")

    async def _handle_force(self, data: dict) -> None:
        state         = data.get("state", "")
        query         = data.get("query", "")
        action_names  = data.get("action_names", list(self.registered.keys()))
        # 只允许 force 里指定的动作
        allowed = {k: v for k, v in self.registered.items() if k in action_names}
        if not allowed:
            allowed = self.registered

        # 记录当前 seq，等 AI 产生新回复
        prev_seq, _ = _read_seq()

        # 写 prompt
        prompt = _build_prompt(self.game, state, query, allowed)
        NS_DIR.mkdir(parents=True, exist_ok=True)
        PROMPT_FILE.write_text(_sign_prompt(prompt), encoding="utf-8")

        # 等待 AI 回复
        ai_text = await _wait_ai_response(prev_seq)
        if not ai_text:
            print(f"[ws_nerve] AI timeout after {AI_TIMEOUT}s")
            # 超时：选第一个可用动作
            ai_text = f"[ACTION: {next(iter(allowed))}]" if allowed else ""

        # 解析动作选择
        action_name, params = _parse_action_choice(ai_text, allowed)
        if not action_name:
            print("[ws_nerve] could not parse action from AI response")
            return

        # 发送动作给游戏
        action_id = str(uuid.uuid4())[:8]
        await self.send({
            "command": "action",
            "data": {
                "id":   action_id,
                "name": action_name,
                "data": json.dumps(params, ensure_ascii=False) if params else None,
            }
        })


async def handle_connection(ws: "WebSocketServerProtocol") -> None:
    session = GameSession(ws)
    print(f"[ws_nerve] new connection from {ws.remote_address}")
    try:
        async for message in ws:
            await session.handle(message)
    except websockets.exceptions.ConnectionClosed:
        print(f"[ws_nerve] connection closed: {session.game}")


# ── 主入口 ────────────────────────────────────────────────────────────────────

async def main():
    NS_DIR.mkdir(parents=True, exist_ok=True)
    if not PROMPT_SECRET:
        print("[ws_nerve] WARNING: CLOSECLAW_PROMPT_SECRET not set")

    print(f"[ws_nerve] listening on ws://{WS_HOST}:{WS_PORT}")
    print(f"[ws_nerve] source={SOURCE}  ai_timeout={AI_TIMEOUT}s")

    async with websockets.serve(handle_connection, WS_HOST, WS_PORT):
        await asyncio.Future()  # 永久运行


if __name__ == "__main__":
    asyncio.run(main())
