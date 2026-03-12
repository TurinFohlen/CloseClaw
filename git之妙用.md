来，手把手教你怎么把 Git 塞进你的“一人公司”

你现在的架构：

· 多个容器（worker）跑着 AI 员工
· /shared 目录做 IPC
· 神经系统负责对话和执行

现在要做的：用 GitHub 做中央仓库，让 AI 员工们通过 Git 协同工作。

---

1. 在 GitHub 上建一个仓库

比如叫 ai-company。在里面放一个初始任务板：

```bash
# 本地（或直接在 GitHub 网页创建）
git clone https://github.com/你的用户名/ai-company.git
cd ai-company
echo "# 任务板" > TASKS.md
echo "- [ ] 任务1：爬取网站数据（分配给 worker-a）" >> TASKS.md
echo "- [ ] 任务2：生成代码模板（分配给 worker-b）" >> TASKS.md
git add TASKS.md
git commit -m "init task board"
git push origin main
```

---

2. 让每个容器能访问 GitHub

2.1 生成 GitHub 个人访问令牌

· 在 GitHub 设置里生成一个新令牌，权限选 repo（读写私有仓库）。
· 保存令牌值。

2.2 在 docker-compose 里注入令牌

```yaml
services:
  worker-a:
    build: .
    environment:
      - GITHUB_TOKEN=${GITHUB_TOKEN}   # 从宿主机的环境变量传
    volumes:
      - shared_data:/shared
      - worker_a_workspace:/workspace   # 每个 worker 有自己的工作目录
  worker-b:
    ...
```

然后在启动容器的 shell 里先 export GITHUB_TOKEN=你的令牌，再 docker compose up。

2.3 修改 entrypoint.sh，自动 clone/pull

在每个容器启动时，确保 /workspace 里有最新的仓库：

```bash
#!/bin/bash
# 设置 git 用户信息（必须，否则 commit 会失败）
git config --global user.name "AI Worker $(hostname)"
git config --global user.email "ai-$(hostname)@closeclaw.local"

if [ ! -d /workspace/.git ]; then
    echo "第一次启动，克隆仓库..."
    git clone https://你的用户名:${GITHUB_TOKEN}@github.com/你的用户名/ai-company.git /workspace
else
    echo "拉取最新更新..."
    cd /workspace && git pull
fi

# 进入自己的工作分支（例如 worker-a）
cd /workspace
git checkout -b $(hostname) origin/main 2>/dev/null || git checkout $(hostname)
# 如果分支不存在，从 main 创建
if [ $? -ne 0 ]; then
    git checkout -b $(hostname) origin/main
fi

# 然后启动其他服务（xvfb, chrome, 神经...）
```

---

3. Worker 的工作流程

假设 worker-a 被分配了“任务1”。它在运行过程中：

1. 检测任务：motor_nerve（或一个专门的 Git 监控脚本）定期 git pull 看 TASKS.md 是否有分配给自己的任务。
2. 切换到自己分支（已经做了）。
3. 干活：执行具体命令（爬虫、脚本），结果输出到 /workspace/output/ 下。
4. 提交结果：
   ```bash
   cd /workspace
   git add output/task1.json
   git commit -m "worker-a: 完成任务1 - 爬取数据"
   git push origin $(hostname)   # 推送到自己的分支
   ```
5. 更新任务板？不直接改，让领导 AI 来改。

---

4. 领导 AI 如何“开会”

你可以专门跑一个“领导容器”，或者用其中一个 worker 兼任。它的工作：

· 定期（比如每小时）拉取所有分支的进度：
  ```bash
  cd /workspace
  git fetch --all
  git log worker-a --oneline -5
  git log worker-b --oneline -5
  ```
· 根据这些日志，更新 TASKS.md（比如任务1完成，就标记为已完成，并分配新任务）。
· 提交并推送更新：
  ```bash
  git add TASKS.md
  git commit -m "会议：更新任务板"
  git push origin main
  ```

谁来执行这些 Git 命令？
你可以写一个小的 Python 脚本（比如 git_coordinator.py），它：

· 读取当前 AI 的回复（通过 response_latest.txt）
· 如果回复里包含 [TASKS_UPDATE] 标记，就提取新的任务板内容，写入文件并执行 Git 命令。
· 或者更简单：让 AI 在回复中直接输出 Git 命令，然后 motor_nerve 注入到终端执行。但这样有安全风险，建议用前者。

---

5. 与现有神经系统结合

· brainstem 照旧拦截 SSE，写 response_latest.txt。
· feedback_nerve 可以增加一个模式：如果 AI 回复里包含 [GIT_COMMIT] 标记，就执行一个预定义的 Git 操作（比如更新 TASKS.md 并 push）。这样就不需要单独写协调脚本。
· motor_nerve 在注入命令前，可以先 git pull 看任务板，决定是否要执行任务。

---

6. 分支策略建议

· main：只由领导 AI 更新（TASKS.md、MEMORY.md 等全局文件）。
· worker-*：每个员工自己的分支，存放工作产出。
· 任务完成后，领导 AI 把产出合并到 main（或者不合并，只在 TASKS.md 里标记完成，产出留在分支里供查阅）。

合并示例：

```bash
git checkout main
git merge --no-ff worker-a -m "合并 worker-a 的任务1成果"
git push origin main
```

如果合并冲突，Git 会生成冲突标记。领导 AI 可以在下一轮会议时看到冲突，然后根据提示词解决（或者你手动介入）。

---

7. 注意事项

· 令牌安全：永远不要把令牌写在代码里，用环境变量注入。
· 并发 push：多个 worker 同时 push 到不同分支没问题，GitHub 会处理。只有同时 push 到同一个分支才可能冲突。
· 网络问题：如果容器无法连接 GitHub，Git 命令会失败。你可以在脚本里加重试逻辑。
· 磁盘空间：每个容器有自己的 /workspace，会占用磁盘，但都是文本，很小。如果担心，可以把 /workspace 挂载成 tmpfs（内存盘）来减少写入，但需要保证重启后数据不丢（因为有 GitHub 远程）。
· 工作目录隔离：每个容器有自己的 /workspace 卷，互不干扰，但远程仓库是同一个。这样既隔离又共享。

---

8. 简单示例：worker 的 Git 监控脚本

你可以在每个容器里跑一个小脚本 git_watcher.py：

```python
import time
import subprocess
from pathlib import Path

WORKSPACE = Path("/workspace")
TASKS_FILE = WORKSPACE / "TASKS.md"

def git_pull():
    subprocess.run(["git", "-C", str(WORKSPACE), "pull"], check=False)

def check_my_tasks():
    content = TASKS_FILE.read_text()
    if "worker-a" in content and "任务1" in content:
        # 执行具体任务
        subprocess.run(["python", "/app/tasks/task1.py"])
        # 提交结果
        subprocess.run(["git", "-C", str(WORKSPACE), "add", "."])
        subprocess.run(["git", "-C", str(WORKSPACE), "commit", "-m", "worker-a completed task1"])
        subprocess.run(["git", "-C", str(WORKSPACE), "push", "origin", "worker-a"])

while True:
    git_pull()
    check_my_tasks()
    time.sleep(60)
```

这个脚本可以跟 motor_nerve 并行运行，或者直接合并进 motor_nerve 的逻辑。

---

现在，你的一人公司正式升级成“Git 驱动的分布式 AI 团队”。老板你只用写写 prompt，看看 GitHub 上的 TASKS.md，员工们自动 pull、干活、push。等老电脑装好 Linux，docker compose scale worker=10，瞬间拉起十个 AI 员工，GitHub 上分支一片繁荣，场面壮观 😎