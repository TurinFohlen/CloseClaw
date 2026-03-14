# MacroGuide.md — 宏接入指南

> 植物大战僵尸都有全自动宏，CloseClaw 当然也可以。
> 本指南面向「完全不会编程」的用户，五分钟上手。

---

## 你需要做的只有一件事

把 prompt 发进 AI 输入框，然后按回车。
宏就是帮你自动做这件事的工具。

```
你手动做的：          宏帮你做的：
点输入框               ✓ 自动
粘贴文字               ✓ 自动
按回车                 ✓ 自动
等回复                 ✓ 自动（CloseClaw 的 motor_nerve 负责）
```

---

## 方案一：AutoHotkey（Windows，推荐）

### 安装

1. 打开 https://www.autohotkey.com
2. 点 Download → AutoHotkey v2 → 安装
3. 完成，不需要任何配置

### 最简单的宏（30秒复制粘贴）

新建一个文本文件，改名为 `closeclaw.ahk`，内容：

```autohotkey
; CloseClaw 一键发送宏
; 用法：把 prompt 写在 MY_PROMPT 里，按 F9 发送

MY_PROMPT := "帮我检查 /workspace 里今天修改过的文件，输出摘要"

F9:: {
    ; 点击 AI 输入框（需要先手动把窗口放好）
    Click 960, 900          ; 输入框坐标，根据你的屏幕调整
    Sleep 200

    ; 粘贴 prompt
    A_Clipboard := MY_PROMPT
    Send "^v"
    Sleep 300

    ; 按回车发送
    Send "{Enter}"
}
```

双击 `closeclaw.ahk` 运行，按 F9 发送。

### 循环发送版（定时让 AI 汇报进度）

```autohotkey
; 每 10 分钟问一次进度
STATUS_PROMPT := "现在状态怎么样？有什么需要我决策的吗？"

F10:: {
    SetTimer SendStatus, 600000   ; 600000ms = 10分钟
    MsgBox "定时汇报已启动，每10分钟发送一次"
}

F11:: {
    SetTimer SendStatus, 0
    MsgBox "定时汇报已停止"
}

SendStatus() {
    Click 960, 900
    Sleep 200
    A_Clipboard := STATUS_PROMPT
    Send "^v"
    Sleep 300
    Send "{Enter}"
}
```

### 怎么找输入框坐标

AutoHotkey 自带坐标工具：

```autohotkey
; 新建一个 findcoord.ahk，内容如下
; 把鼠标放到 AI 输入框上，按 F1 显示坐标

F1:: {
    MouseGetPos &x, &y
    MsgBox "X=" x "  Y=" y
}
```

---

## 方案二：Tampermonkey（浏览器插件，零安装）

适合：不想装软件，直接在浏览器里搞定。

### 安装

1. Chrome 扩展商店搜「Tampermonkey」安装
2. 点 Tampermonkey 图标 → 添加新脚本
3. 粘贴以下代码保存

### 脚本

```javascript
// ==UserScript==
// @name         CloseClaw 快速发送
// @match        https://claude.ai/*
// @match        https://chat.deepseek.com/*
// @match        https://chat.qwen.ai/*
// @grant        none
// ==/UserScript==

(function() {
    // 在页面右下角加一个发送按钮
    const btn = document.createElement('button');
    btn.textContent = '⚡ 发送任务';
    btn.style.cssText = `
        position: fixed; bottom: 80px; right: 20px;
        z-index: 9999; padding: 10px 16px;
        background: #1a1a1a; color: #00ff41;
        border: 1px solid #00ff41; border-radius: 4px;
        font-family: monospace; cursor: pointer;
        font-size: 13px;
    `;

    btn.onclick = () => {
        const prompt = window.prompt('输入任务：');
        if (!prompt) return;

        // 找输入框（各家 AI 网站的 selector）
        const selectors = [
            'div[contenteditable="true"]',   // Claude
            'textarea#chat-input',            // DeepSeek
            'textarea[placeholder]',          // 通用 fallback
        ];

        let inputEl = null;
        for (const sel of selectors) {
            inputEl = document.querySelector(sel);
            if (inputEl) break;
        }

        if (!inputEl) { alert('找不到输入框，请手动更新 selector'); return; }

        // 输入文字
        inputEl.focus();
        document.execCommand('insertText', false, prompt);

        // 触发发送（模拟回车）
        setTimeout(() => {
            inputEl.dispatchEvent(new KeyboardEvent('keydown', {
                key: 'Enter', code: 'Enter', bubbles: true
            }));
        }, 300);
    };

    document.body.appendChild(btn);
})();
```

保存后刷新 AI 网页，右下角出现「⚡ 发送任务」按钮，点击输入 prompt 发送。

---

## 方案三：直接写文件（最简单，不需要任何工具）

CloseClaw 的 motor_nerve 一直在监听 `/shared/{source}/prompt.txt`。
你只需要往这个文件里写内容，AI 就会收到。

```bash
# 命令行一行搞定
echo "帮我检查今天的进度" > /shared/claude/prompt.txt

# 或者用 Python（任何平台）
python3 -c "open('/shared/claude/prompt.txt','w').write('帮我检查今天的进度')"
```

Windows 用户用 WSL 或者 Docker Desktop 的终端执行。

---

## 方案四：Telegram（人在外面，手机遥控）

见 OperatingInstructions.html 第12章。
装好 telegram_bridge 之后，手机发消息 = 发 prompt，是最省事的方案。

```
你在外面喝茶
  → 手机发 Telegram 消息
  → AI 收到任务开始干活
  → 干完发回结果
  → 你继续喝茶
```

---

## 坐标不对怎么办

AutoHotkey 方案依赖屏幕坐标，不同分辨率坐标不同。

**一劳永逸的解法：** 用 `WinActivate` 直接找窗口，不靠坐标：

```autohotkey
F9:: {
    ; 激活 Chrome 窗口
    WinActivate "ahk_exe chrome.exe"
    Sleep 500

    ; 用 Tab 键导航到输入框（不需要坐标）
    Send "^l"          ; 先点地址栏
    Sleep 200
    Send "{Escape}"    ; 退出地址栏
    Sleep 200

    ; 直接发送文字（当前焦点在输入框时）
    A_Clipboard := MY_PROMPT
    Send "^v"
    Sleep 300
    Send "{Enter}"
}
```

---

## 常见问题

| 问题 | 原因 | 解决 |
|------|------|------|
| 宏没反应 | AHK 没有运行 | 双击 .ahk 文件 |
| 文字发到了别的地方 | 焦点不在输入框 | 先手动点一下 AI 输入框 |
| 发出去是乱码 | 编码问题 | .ahk 文件用 UTF-8 with BOM 保存 |
| Tampermonkey 找不到输入框 | 网站更新了 DOM | 运行 debug_selectors() 更新 selector |
| 想定时自动发 | - | 用循环发送版，或 Telegram bridge |

---

## 进阶：让 AI 自己写宏

如果你想要更复杂的宏逻辑，直接让 CloseClaw 里的 AI 帮你写：

```
帮我写一个 AutoHotkey v2 脚本，功能：
每隔 30 分钟检查 /shared/claude/response_latest.txt 是否有更新，
如果有更新就用 Windows 通知弹出最新回复的前100个字。
```

AI 写完你直接用，不需要自己理解代码。
