# CloseClaw

**不走 API，盯着屏幕抄。**

无头 AI 工作站。文件系统做 IPC，每条神经只干一件事。
老板喝凤凰单丛，AI 在机房敲键盘。

---

## 架构

```
[大脑]  claude.ai 网页端（黑箱）
   |
   | SSE 流
   ↓
[脑干]  mitmproxy + brainstem.py
        → /shared/response_latest.txt  (seq:N + 正文)
        → /shared/response_log.jsonl   (追加历史)
   |
   | 文件系统（"体液"）
   |
   ├── [运动神经]   motor_nerve.py     → xdotool 执行 AI 指令
   |
   ├── [感觉神经]   sensory_nerve.py   → /shared/command_output.txt
   |                                     /shared/screen_output.jpg
   |
   └── [反馈神经]   feedback_nerve.py  → 上传图片 + 注入文字 → AI 看到
                     mouse_lock.py      fcntl.flock 跨进程互斥
```

**安全性从架构上保证：**
- 无 API 密钥（走网页端）
- 无公网监听（所有端口绑 127.0.0.1）
- 无插件市场
- 工作设备物理隔离

---

## 快速开始

### 前置条件
- 一台装了 Docker 的机器（老电脑也行，无头运行）
- SSH 访问权限

### 1. 克隆 & 构建

```bash
git clone <repo>
cd closeclaw
docker compose build
```

### 2. 首次启动（需要手动登录 Claude）

```bash
# 开 VNC 方便第一次登录
ENABLE_VNC=1 docker compose up -d

# SSH tunnel 到 VNC
ssh -L 5900:localhost:5900 user@your-old-pc

# 用 VNC viewer 连 localhost:5900，手动登录 claude.ai
# 登录后 Chrome profile 持久化到 volume，之后不需要再登录
```

### 3. 正式运行（无头）

```bash
ENABLE_VNC=0 docker compose up -d

# 看日志
tail -f logs/brainstem.log
tail -f logs/motor_nerve.log

# 看 AI 最新回复
cat /var/lib/docker/volumes/closeclaw_shared_data/_data/response_latest.txt

# 或者开面板
docker compose --profile panel up -d
ssh -L 8888:localhost:8888 user@your-old-pc
# 浏览器打开 http://localhost:8888
```

### 4. 手动干预（往/shared/丢指令）

```bash
# SSH 进去直接操作
docker exec -it closeclaw bash

# 或者从宿主机写文件触发执行
echo "seq:999\nsource:manual\ngit status" > /path/to/shared/command_output.txt
```

---

## 文件格式

**`/shared/response_latest.txt`**
```
seq:42
AI 回复正文从这里开始...
```

**`/shared/command_output.txt`**
```
seq:7
source:git status
On branch main
nothing to commit
```

`seq` 是单调递增整数。所有神经通过比较 seq 判断"是否有新内容"，不用时间戳。

---

## 模块说明

| 文件 | 职责 |
|------|------|
| `brainstem/brainstem.py` | mitmproxy addon，拦截 SSE，落盘 |
| `brainstem/motor_nerve.py` | 轮询 response_latest.txt，执行指令 |
| `brainstem/sensory_nerve.py` | 捕获命令输出，写 command_output.txt |
| `brainstem/feedback_nerve.py` | 把结果上传回对话框，闭合反射弧 |
| `closeclaw/control/mouse_lock.py` | fcntl.flock 跨进程鼠标互斥锁 |
| `closeclaw/control/upload_locator.py` | opencv 模板匹配定位上传按钮（一次性校准） |
| `closeclaw/bridge.py` | Playwright 路径（DOM 直读，MVP 主路径） |
| `closeclaw/poller.py` | 自适应轮询（200ms/1000ms，游戏外挂精华） |
| `closeclaw/cache.py` | ResponseCache，LRU，避免重复过 UI |

---

## 调试

```bash
# 实时监控共享文件变化
docker exec closeclaw inotifywait -m /shared -e close_write

# 手动测试脑干 SSE 解析（不需要 mitmproxy）
python3 -c "
from brainstem.brainstem import _parse_sse_stream
import json
events = [{'type':'content_block_delta','delta':{'type':'text_delta','text':'hello'}}]
raw = b'\n'.join(('data: '+json.dumps(e)).encode() for e in events)
print(_parse_sse_stream(raw))
"

# 重新校准上传按钮坐标
docker exec closeclaw python3 -m closeclaw.control.upload_locator --recalibrate
```
对，而且 git 在这里不只是版本控制——它天然就是**异步多智能体协调协议**：

```
/workspace/          ← git repo，所有"员工"的公共工作区
  TASKS.md           ← 任务看板（领导写，员工认领）
  MEMORY.md          ← 共享长期记忆
  agents/
    worker-a/        ← A 的工作目录，A 的 branch
    worker-b/        ← B 的工作目录，B 的 branch
  shared/
    response_log.jsonl
```

**"领导开会"就是一条 prompt + git commit：**

```
你是项目协调员。当前任务板：[TASKS.md]
Worker-A 完成了：[git log worker-a --oneline -5]
Worker-B 正在做：[git log worker-b --oneline -3]
请分配下一轮任务，输出更新后的 TASKS.md。
```

脑干拿到回复 → `sensory_nerve` 跑 `git commit -m "meeting: round 3"` → 各 worker 的 `motor_nerve` 检测到 TASKS.md 更新 → 各自认领。

**冲突解决也是 git 的事**，`--reject` 留 `.rej` 文件，领导下一轮开会时看到冲突再裁决。

整个拓扑：

```
                [领导容器]
               /          \
        git push          git push
           /                  \
    [worker-a]            [worker-b]
    跑爬虫/数据            跑代码生成
    自己的 branch          自己的 branch
           \                  /
            \                /
             git merge → main
```

"搞网吧的成熟轮子"在这一层也成立——这个模式和网吧的"母机 + 子机批量管理"几乎同构，只是把 Ghost 镜像换成了 Docker image，把游戏存档同步换成了 git。

等电脑装好 Linux，worker 数量就是 `docker compose scale worker=N` 一条命令的事。
---

## 硬件配置

最低配置（个人使用）：
- CPU: 任意 x86_64，双核以上
- RAM: 4GB（Chrome 吃内存）
- 存储: 20GB
- 网络: 有线更稳，WiFi 也行

推荐：把老手机/平板也接进来跑多账号轮换（`closeclaw/session/account.py` 预留了接口）。
