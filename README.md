# brainstem — 脑干模块

反射弧的中继站：mitmproxy 拦截 SSE，文件系统做 IPC。

```
claude.ai ──SSE──► mitmproxy:8080
                        │
                   brainstem.py
                        │
              /shared/response_latest.txt
                        │
                   motor_nerve.py ──► xdotool / AutoKey
                        
     bash | sensory_nerve.py ──► /shared/command_output.txt
```

---

## 依赖

```bash
pip install mitmproxy
```

---

## 首次配置（一次性）

```bash
# 1. 启动 mitmproxy，触发 CA 证书生成
mitmdump --listen-port 8080 &

# 2. 信任 CA（Debian）
cp ~/.mitmproxy/mitmproxy-ca-cert.pem /usr/local/share/ca-certificates/mitmproxy.crt
update-ca-certificates

# 3. Chrome 启动时加代理
google-chrome --proxy-server="http://127.0.0.1:8080" &

# 或者设系统代理：
# export http_proxy=http://127.0.0.1:8080
# export https_proxy=http://127.0.0.1:8080
```

---

## 运行

```bash
mkdir -p /shared

# 脑干（后台）
mitmdump -s brainstem/brainstem.py --listen-port 8080 &

# 运动神经（调试用，看到回复说明反射弧通了）
python brainstem/motor_nerve.py &

# 感觉神经（管道模式）
git status | python brainstem/sensory_nerve.py
# 或执行模式
python brainstem/sensory_nerve.py -- python my_script.py
```

---

## 验证脑干是否工作

```bash
# 发一条消息给 claude.ai，然后：
cat /shared/response_latest.txt
# 应该看到 seq:1 加上回复内容

# 实时监控（inotifywait 是 inotify-tools 包里的）
inotifywait -m /shared/response_latest.txt -e close_write | \
    while read; do echo "--- new ---"; cat /shared/response_latest.txt; done
```

---

## 调整 TARGET_PATH

如果 brainstem.py 没有捕获到流量，在 mitmweb 里看实际 URL：

```bash
mitmweb -s brainstem/brainstem.py --listen-port 8080
# 浏览器打开 http://127.0.0.1:8081，过滤 claude.ai，找 POST 请求
```

把实际路径前缀更新到 brainstem.py 的 `TARGET_PATH`。

---

## 文件格式

`/shared/response_latest.txt`
```
seq:42
回复正文从这里开始...
```

`/shared/command_output.txt`
```
seq:7
source:git status
On branch main
nothing to commit...
```

seq 是单调递增整数，运动神经只需比较 seq 是否变化，无需哈希或时间戳。
# CloseClaw
# CloseClaw
