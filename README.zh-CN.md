# cc-buddy-bridge

[English](README.md) | **简体中文** | [日本語](README.ja.md)

[![test](https://github.com/SnowWarri0r/cc-buddy-bridge/actions/workflows/test.yml/badge.svg)](https://github.com/SnowWarri0r/cc-buddy-bridge/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)
[![Python: 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![Platforms](https://img.shields.io/badge/platforms-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey.svg)](#系统要求)
[![Status: daily-driven](https://img.shields.io/badge/status-daily--driven-brightgreen.svg)](#项目状态)
[![PRs: Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/SnowWarri0r/cc-buddy-bridge/issues)

把 [Claude Code](https://claude.com/claude-code) CLI 会话桥接到
[claude-desktop-buddy](https://github.com/anthropics/claude-desktop-buddy)
BLE 硬件——无需经过 Claude 桌面客户端。

buddy 固件官方只跟 Claude for macOS/Windows 桌面端配对。本项目让你在普通终端跑
`claude` CLI 也能驱动同一个硬件——你的桌面宠物会跟着 CLI 会话作出反应：闲置时睡觉，
工具调用时变忙，权限提示需要你确认时闪烁，并且能直接用 stick 上的物理按键批准/拒绝。

## 主要特性

- **关键操作的物理 2FA** —— 全局设 `defaultMode: bypassPermissions`，把真正在意的几个工具丢进 `permissions.ask`。这些操作的 allow/deny 由桌面 buddy 上的 A/B 按键决定。
- **智能匹配器** —— 平凡的 Bash（`ls`/`cat`/`grep`/...）自动放行；危险的（`rm`/`curl`/`git push`/...）总是询问；其余转给 stick 决策。可通过 TOML 覆盖默认规则。
- **实时 stick HUD** —— 助手回复经 JSONL tailer 在 ~500 ms 内镜像到 stick（绕过 Stop hook 落盘竞态）。
- **状态栏组件** —— `cc-buddy-bridge hud` 在终端 prompt 渲染电量 / 加密状态 / **当日 token 数** / **当日预估 USD 花销** / 待处理权限提示；可与 [claude-hud](https://github.com/jarrodwatts/claude-hud) 组合使用。
- **一行命令安装 + 开机自启** —— `cc-buddy-bridge install --service` 自动选对每个 OS 的后端：macOS 用 launchd、Linux 用 systemd 用户级 unit、Windows 用任务计划程序。
- **自定义 GIF 角色** —— `cc-buddy-bridge push-character ./pack/` 通过 BLE 上传一整个动画包，自带分块流控。
- **新版本提示 + 自更新** —— daemon 每天后台轮询一次 GitHub releases；有新版时 hud 多一段 `↑ vX.Y.Z`。`cc-buddy-bridge check-update` 显式查询，`cc-buddy-bridge update` 一键拉新代码 + 重装 + 重启 daemon。轮询用 `CC_BUDDY_BRIDGE_NO_UPDATE_CHECK=1` 关闭。
- **stick 上显示中文（可选，**强烈推荐**）** —— 配套 fork 固件 [SnowWarri0r/claude-desktop-buddy `feat/cjk-display-zh-cn`](https://github.com/SnowWarri0r/claude-desktop-buddy/tree/feat/cjk-display-zh-cn) 把 stock 固件那个 ASCII-only 字体换成 [Fusion Pixel Font](https://github.com/TakWolf/fusion-pixel-font)（OFL）12×12 字形，简体中文、日文直接渲染（繁中仍在规划中）。设 `CC_BUDDY_CJK_TARGET=zh-CN`（或 `ja`）后桥端自动切对应 wire 编码。详见 [stick 上显示中文](#stick-上显示中文可选强烈推荐)。

## 工作原理

```
claude CLI ──PreToolUse/Stop/etc hooks──▶ Unix socket ──▶ daemon ──BLE NUS──▶ stick
                                                           ▲
                                                           └── 跟踪 ~/.claude/projects/*.jsonl
                                                               提取 token 数与最近消息
```

* **Hooks**（在 `~/.claude/settings.json` 配置）在会话生命周期事件、工具调用、权限请求、回合边界处触发。
* 每个 hook 是一个短小的 Python 脚本，通过 Unix socket 把事件 payload 转发给本地 **daemon**。
* daemon 聚合每个会话的状态（`total` / `running` / `waiting` / `tokens` / `entries`），通过 BLE Nordic UART Service 把心跳快照推送给 stick，使用与桌面端完全一致的 JSON 线协议。
* 对权限提示，hook **阻塞** 等 stick 按键裁决，再把 `allow` / `deny` 返回给 Claude Code。

完整线协议见
[buddy 固件仓库的 REFERENCE.md](https://github.com/anthropics/claude-desktop-buddy/blob/main/REFERENCE.md)。

## 安装

```bash
git clone https://github.com/SnowWarri0r/cc-buddy-bridge
cd cc-buddy-bridge
python3.12 -m venv .venv
.venv/bin/pip install -e .

# 把 hooks 注册到 ~/.claude/settings.json（会先做 .backup 备份）：
.venv/bin/cc-buddy-bridge install

# 在另一个终端启动 daemon：
.venv/bin/cc-buddy-bridge daemon
```

**Windows 用户：** 把上面命令里的 `.venv/bin/` 全部替换为 `.venv\Scripts\`。

接着启动任意 `claude` 会话。daemon 会扫描名字以 `Claude` 开头的 BLE 设备，连上之后开始推送状态。

卸载 hooks：

```bash
.venv/bin/cc-buddy-bridge uninstall
```

### 开机自启

不想每次手动开 `cc-buddy-bridge daemon`？把它装成系统服务，登录时自动启动、崩溃时自动重启。

#### macOS（launchd）

装成用户级 launchd agent：

```bash
.venv/bin/cc-buddy-bridge install --service
```

会写入 `~/Library/LaunchAgents/com.github.cc-buddy-bridge.daemon.plist`，
指向你刚刚装包用的 venv Python，立即用 `launchctl load` 拉起，并把 stdout/stderr
重定向到 `~/Library/Logs/cc-buddy-bridge.log`。

卸载：

```bash
.venv/bin/cc-buddy-bridge uninstall --service
```

#### Windows（任务计划程序）

装成任务计划程序里的任务：

```bash
.venv/Scripts/cc-buddy-bridge install --service
```

会创建一个名为 `cc-buddy-bridge-daemon` 的任务，登录时触发。
日志写到 `%LOCALAPPDATA%\cc-buddy-bridge\daemon.log`。

卸载：

```bash
.venv/Scripts/cc-buddy-bridge uninstall --service
```

#### Linux（systemd）

同一个 `--service` 标志在 Linux 上会装成用户级 systemd unit：

```bash
.venv/bin/cc-buddy-bridge install --service
```

会写入 `~/.config/systemd/user/cc-buddy-bridge.service`，指向你刚刚装包用的
venv Python，再依次执行 `systemctl --user daemon-reload` 和
`systemctl --user enable --now cc-buddy-bridge.service`，让 daemon 立即启动并在
每次登录时自启。查看日志：

```bash
journalctl --user -u cc-buddy-bridge.service -f
```

卸载：

```bash
.venv/bin/cc-buddy-bridge uninstall --service
```

Linux 特有的几个小坑：

* **BLE 依赖 BlueZ。** 确认 `bluetooth` 服务在跑（`systemctl status bluetooth`），且当前用户在 `bluetooth` 组里（`sudo usermod -aG bluetooth $USER`，然后注销重登）。否则 journal 里会看到 `org.freedesktop.DBus.Error.ServiceUnknown ... org.bluez`。
* **登出仍存活 / 开机自启。** 默认情况下 user manager 会跟随你最后一个会话退出，daemon 也就跟着停。想让 unit 开机就跑、登出后仍活着，跑一次 `loginctl enable-linger $USER`。

在 Ubuntu 22.04 LTS 上验证过。任何带 systemd user manager 的发行版（Fedora 39+、Debian 12+、Arch 等）都该能跑——遇到需要适配的发行版欢迎开 issue。

---

`cc-buddy-bridge status` 可以一次性查看 hooks 与服务两者的安装状态。

### 自定义 IPC 传输

daemon 跟 hook 脚本走的是本地 IPC 通道。默认配置覆盖 99% 场景，剩下 1% 留了两个开关：

| 系统          | 默认传输                                   | 覆盖方式 `--socket` 或 `CC_BUDDY_BRIDGE_SOCK` |
| ------------- | ----------------------------------------- | ---------------------------------------------- |
| macOS / Linux | Unix socket `/tmp/cc-buddy-bridge.sock`   | 改成别的路径，比如 `~/cc-buddy.sock`            |
| Windows       | TCP loopback `127.0.0.1:48765`            | 改端口，比如 `:49000` 或 `127.0.0.1:49000`      |

Windows 上端口 48765 跟别的进程冲突时：跑 `cc-buddy-bridge daemon --socket :49000`，
hud 调用也带同样的 `--socket`（或者 `export CC_BUDDY_BRIDGE_SOCK=:49000`
一次性给所有 hook 脚本设好）。

### 把 stick 状态显示在 Claude Code 状态栏

`cc-buddy-bridge hud` 输出一行紧凑摘要（电量、加密状态、待处理权限）。把它接到
`~/.claude/settings.json`：

```json
{
  "statusLine": {
    "type": "command",
    "command": "/path/to/.venv/bin/cc-buddy-bridge hud"
  }
}
```

只支持 ASCII 的终端：`cc-buddy-bridge hud --ascii`。

已经在用 [claude-hud](https://github.com/jarrodwatts/claude-hud) 或别的状态栏插件？两者可以共存——写个小 shell 脚本拼接两边的输出即可，statusLine 接受多行响应。

实拍 iTerm2——爪印、电量进度条、加密锁、当日 token 数、当日花销、运行中的会话数：

<p align="center"><img src="docs/img/statusline.png" alt="cc-buddy-bridge hud — 爪印、电量条 98%、锁、101K tokens、$106.02、1 个会话在跑" width="580"></p>

同一行还会经过的其它状态：

```
🐾 🔋 96% 🔒                          # 链路加密、电量充足
🐾 🔋 96% 🔒 12.3K $0.42              # 当日 token（≥ 1K）与花销（≥ $0.01）
🐾 🔋 12% 🔒 1.2M $8.50 2run          # 低电量、大用量、有会话在跑
🐾 ⚠ approve: Bash                    # stick 上有待处理权限提示
🐾 ∅                                  # stick 已断连（但 daemon 还活着）
🐾 off                                # daemon 没在跑
```

Token 数对当日 `~/.claude/projects/*.jsonl` 里的 `usage.output_tokens` 求和。
花销基于同一批记录估算（`input + output + 缓存读写 × 各模型费率`），费率表
写死在 [`pricing.py`](src/cc_buddy_bridge/pricing.py)，要改/加模型直接编辑即可。
这不是账单真相，只作为辅助提示。

## 与 Claude Code `permissions` 配置的配合

Claude Code 自己 `~/.claude/settings.json` 里的 `permissions` 块
（`allow` / `ask` / `deny` 列表，加上 `defaultMode`）和本桥的智能匹配器
**两套规则**共同决定一次工具调用走哪条路径。规则定义清楚，但还是写出来便于选对组合。

每次 `PreToolUse` 事件：

```
matcher classify_command(hint)
 ├─ "allow"  → bridge 直接返回 permissionDecision=allow（短路）
 ├─ "ask"    → bridge 等 stick 按键 → 返回按键结果
 └─ "default"→ bridge 不表态 → 由 Claude Code 的 settings.json + defaultMode 接管
```

**推荐组合**

| Claude Code `defaultMode` | Matcher `strict` | 行为                                                                                                  |
| ------------------------- | ---------------- | ----------------------------------------------------------------------------------------------------- |
| `ask`（默认）              | `false`          | 平凡 bash 由 matcher 自动放行；危险 bash 走 stick；其它走 Claude Code 终端提示。                       |
| `bypassPermissions`       | **`true`**       | **stick 是唯一的人工确认入口。** 平凡 bash 自动放行；其它一切（匹配/未匹配）都走 stick。终端不弹提示。 |
| `bypassPermissions`       | `false`          | ⚠ 只有 matcher 的 `always_ask` 模式会走 stick；其它一切静默自动放行。daemon 启动时检测到这组合会打 WARNING。 |
| `auto`                    | `false`          | 实质等同 `ask`——未匹配命令落到 Claude Code 的流程。                                                    |

`strict` 写在 `~/.config/cc-buddy-bridge/matchers.toml`：

```toml
strict = true
```

daemon 启动时会用一行日志总结两边配置，方便你一眼看出有没有踩组合错配：

```
INFO cc_buddy_bridge.daemon: matcher: strict=False auto_allow=46 always_ask=53
INFO cc_buddy_bridge.daemon: settings.json: permissions.defaultMode='auto' ask=0
```

## 审计日志

每条 `PreToolUse` 决策都会以 JSONL 形式追加到本地审计文件，便于事后回顾哪些被放行、被拒绝、被忽略——尤其在 `bypassPermissions` 模式下，大多数决策不会经过你眼前。

默认位置：

| 系统    | 路径                                                              |
| ------- | ----------------------------------------------------------------- |
| macOS   | `~/Library/Logs/cc-buddy-bridge-audit.jsonl`                      |
| Linux   | `$XDG_DATA_HOME/cc-buddy-bridge/audit.jsonl`（或 `~/.local/share/...`）|
| Windows | `%LOCALAPPDATA%\cc-buddy-bridge\audit.jsonl`                      |

可以用环境变量 `CC_BUDDY_BRIDGE_AUDIT` 覆盖路径。

每条一行，字段：

```json
{"ts":"2026-05-16T00:15:12.690+08:00","session":"c461b71c","tool":"Bash",
 "hint":"git status -s","matcher":"allow","decision":"allow","source":"auto_allow"}
```

| 字段        | 含义                                                                       |
| ----------- | -------------------------------------------------------------------------- |
| `ts`        | ISO-8601 本地带时区时间戳                                                   |
| `session`   | Claude Code 会话 id 前 8 位                                                 |
| `tool`      | 工具名（`Bash`、`Edit` 等）                                                  |
| `hint`      | 命令/路径短摘要（截断到 200 字符）                                            |
| `matcher`   | 匹配器分类：`allow` / `ask` / `default`                                      |
| `decision`  | 桥实际返回：`allow` / `deny` / `null`（未表态）                              |
| `source`    | `auto_allow` / `stick` / `timeout` / `defer` / `ble_disconnected`           |
| `elapsed_s` | 与 stick 往返耗时（秒），仅在 stick 参与时存在                                |

### 查看

`cc-buddy-bridge audit` 是友好的查看器——彩色、对齐，自带 tail / filter / follow：

```bash
cc-buddy-bridge audit                       # 最近 20 条
cc-buddy-bridge audit -n 100                # 最近 100 条
cc-buddy-bridge audit -f                    # 持续追踪新条目（Ctrl+C 退出）
cc-buddy-bridge audit --decision deny       # 只看你拒掉的
cc-buddy-bridge audit --source stick        # 只看 stick 按键决策的
cc-buddy-bridge audit --tool Edit -n 50     # 最近 50 次 Edit 工具调用
cc-buddy-bridge audit --path                # 打印审计文件路径并退出
cc-buddy-bridge audit --ascii               # 不带颜色（管道 / 哑终端友好）
```

示例输出：

```
# audit log: /Users/snow/Library/Logs/cc-buddy-bridge-audit.jsonl
00:21:09.029 Bash     —     defer       sleep 8 && gh run list --repo ...
00:30:10.212 Bash     allow auto_allow  cat >> tests/test_audit.py <<'EOF' ...
00:34:55.871 Bash     deny  stick       git push origin main --force
```

颜色：`allow` 绿、`deny` 红、`—`（未表态/转交）暗灰。source 列里 `stick`（你按了按键）黄、`timeout` 红，其余暗灰。

### 原生 jq 配方

不想用子命令、直接 jq 也行：

```bash
# 今天我在 stick 上拒了哪些
jq 'select(.decision=="deny")' ~/Library/Logs/cc-buddy-bridge-audit.jsonl

# 本周自动放行频次 top N
jq -r 'select(.source=="auto_allow") | .hint' ~/Library/Logs/cc-buddy-bridge-audit.jsonl \
  | awk '{print $1}' | sort | uniq -c | sort -rn | head
```

## 新版本提示

daemon 每天后台轮询一次
`https://api.github.com/repos/SnowWarri0r/cc-buddy-bridge/releases/latest`，
缓存结果并在两个地方提示：

* 启动 daemon log 里写一行（有新版时）
* `cc-buddy-bridge hud` 在 statusline 末尾追加 `↑ vX.Y.Z`（`--ascii` 模式
  下是 `up vX.Y.Z`），黄色低调，不会挤掉电量/花销等重要段

显式查询：

```bash
cc-buddy-bridge check-update
# Installed:   0.1.0
# Latest:      v0.1.2
#
# Update available: 0.1.0 → v0.1.2
# Pull with:        git pull && pip install -e .
# Then restart:     cc-buddy-bridge install --service  (or kickstart the daemon)
```

有新版时退出码为 `1`，否则 `0`——便于脚本检测。

### 一键升级

```bash
cc-buddy-bridge update            # 提示 y/N 后执行
cc-buddy-bridge update -y         # 跳过提示（脚本 / CI 友好）
```

等价于在 repo 根 `git pull && pip install -e .`，然后通过你安装服务时选择的
后端（launchd / systemd user unit / Task Scheduler）重启 daemon。会在以下
情形提前安全停手：

- 不是 git checkout（你装的是 wheel）—— 让你回到 pip 自己升级
- 有未提交本地改动 —— 先 stash 或 commit，绝不会把你的工作丢掉
- 不是 tty 且没传 `-y` —— 拒绝盲提示

如果你没装服务（手动跑 `cc-buddy-bridge daemon`），install 步骤照样执行，
最后会提醒你"自己重启 daemon"。

### 隐私

每天一次 HTTPS 请求到 api.github.com。完全关掉：

```bash
export CC_BUDDY_BRIDGE_NO_UPDATE_CHECK=1
```

缓存路径：macOS 在 `~/Library/Caches/cc-buddy-bridge/update_check.json`、
Linux 在 `$XDG_CACHE_HOME/cc-buddy-bridge/...`、
Windows 在 `%LOCALAPPDATA%\cc-buddy-bridge\update_check.json`。

固件版本检测暂不在范围内——stick 的 status ack 不带固件版本字段，
上游 [anthropics/claude-desktop-buddy](https://github.com/anthropics/claude-desktop-buddy)
也没有 release / tag 可对比。

## stick 上显示中文（可选，**强烈推荐**）

官方固件用的是 5×7 ASCII 字体，中文 / 日文 / 韩文内容在 stick 上显示成一排 `?`（[quirk #1](#1-utf-8-多字节序列在-ble-链路上被剪在字符中间)）。
上游 CONTRIBUTING 明确说不接收新功能，所以"加 CJK 字体并合并回主线"这条路不可行。
所以我们做了个**fork-only 固件变体**：
**[github.com/SnowWarri0r/claude-desktop-buddy `feat/cjk-display-zh-cn`](https://github.com/SnowWarri0r/claude-desktop-buddy/tree/feat/cjk-display-zh-cn)**
—— 你自己刷一下就能看到中文。stock 固件用户不受影响：桥端默认仍然把 CJK 替换为 `?`，
直到你显式开启 CJK 模式才会发原文。

### 当前状态

| 目标 | 固件 build | 桥端 codec | 状态 |
| --- | --- | --- | --- |
| **简体中文 (zh-CN)** | `m5stickc-plus-cjk-zh-cn` | `gbk` | ✅ **可用** —— GB2312 zones 1-55（符号 + Level 1 汉字），约 4300 字形 |
| 繁体中文 (zh-TW) | `m5stickc-plus-cjk-zh-tw` | `big5` | 🚧 规划中——桥端 codec 已就位；固件 build 还用简体字形 |
| **日文 (ja)** | `m5stickc-plus-cjk-ja` | `shift_jis` | ✅ **可用** —— JIS X 0208 行 1-47（假名 + Level 1 汉字），约 4400 字形；半角片假名回退到 ASCII |

### 启用步骤（简体中文 / 日文）

下面以简体中文为例。**日文**请把 `feat/cjk-display-ja` 分支、`m5stickc-plus-cjk-ja`
build、`CC_BUDDY_CJK_TARGET=ja`（wire codec `shift_jis`）对应替换，其余步骤完全一样。

1. **刷 fork 的 CJK 固件变体**。把
   [SnowWarri0r/claude-desktop-buddy](https://github.com/SnowWarri0r/claude-desktop-buddy)
   clone 下来，切到 `feat/cjk-display-zh-cn` 分支，跑
   `pio run -e m5stickc-plus-cjk-zh-cn -t upload --upload-port /dev/cu.usbserial-...`
   （把串口路径换成你 stick 的）。CJK 变体增加约 120 KB 二进制；
   GIF 角色包用的 spiffs 分区**不受影响**。

2. **告诉桥端发 GBK 字节**。给 daemon 加环境变量 `CC_BUDDY_CJK_TARGET=zh-CN`。

   macOS:

   ```bash
   plutil -insert EnvironmentVariables.CC_BUDDY_CJK_TARGET -string zh-CN \
       ~/Library/LaunchAgents/com.github.cc-buddy-bridge.daemon.plist
   launchctl bootout gui/$(id -u)/com.github.cc-buddy-bridge.daemon
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.github.cc-buddy-bridge.daemon.plist
   ```

   Linux：编辑 `~/.config/systemd/user/cc-buddy-bridge.service`，在
   `[Service]` 下加 `Environment=CC_BUDDY_CJK_TARGET=zh-CN`，然后
   `systemctl --user daemon-reload && systemctl --user restart cc-buddy-bridge.service`。

3. **验证**。daemon 启动日志会写 `cjk firmware target=zh-CN, wire codec=gbk`。
   下次助手有中文回复时，stick 屏幕会显示真正的汉字而不是 `?` 串。

想退回原版：去掉这个 env、再刷回默认 `m5stickc-plus` build 即可。

### 幕后改了什么

- **桥端 wire 格式**：`sanitize_for_stick` 从"仅 ASCII"切到"GBK 可编码"；
  `encode()` 手写心跳 JSON，字符串值用 GBK 字节直接放进双引号里
  （key、数字、JSON 结构符号还是 ASCII）。stick 上的 ArduinoJson 7 接收这种 JSON
  —— 它不对 string 值的 UTF-8 做校验。
- **固件渲染**：`cjk_render.cpp` 里 sprite-aware 的混合脚本渲染器逐字节走：
  ASCII (< 0x80) 用 `Fonts/ASC12.h` 画 6×12，GBK 字节对（两个字节都在 0xA1-0xFE）
  用 `Fonts/GB2312_L1.h` 画 12×12。`drawHUD` 把每行先经过一个像素感知 + GBK 感知的
  wrap 函数，长内容自然换行而不是在右边被切掉。

### 字体来源

CJK 固件变体用的字体是 [Fusion Pixel Font](https://github.com/TakWolf/fusion-pixel-font)
12px monospaced 子集（zh\_hans + Latin），按 **SIL Open Font License 1.1** 授权。
OFL 全文 + 上游 fonts（Ark Pixel、Cubic 11、Galmuri）的各自署名都打包在
固件仓库的 `src/Fonts/` 下。感谢 @TakWolf 把这个字体做出来。

### 注意事项

- emoji（补充平面 codepoint）在 GBK / Big5 / SJIS 三个双字节编码里都不存在；
  sanitizer 在 CJK 模式下仍把 emoji 替换成 `?`
- Level-2 汉字（GB2312 zones 56-87，更生僻的字）没打包进来以省 flash；
  渲染时 fallback 成 `??`。日常普通话用字 ~99% 都在 Level 1
- fork 固件没有自动更新机制——上游有新改动时需要你手动 pull 重刷

## 系统要求

* macOS 12+ / Windows 10+ / 装了 BlueZ 的 Linux
* Python 3.11+
* 一台已刷固件的 claude-desktop-buddy（M5StickC Plus）
* Claude Code CLI

## 信号映射

| Buddy 字段        | 来源                                                  |
| ---------------- | ----------------------------------------------------- |
| `total`          | `SessionStart` / `SessionEnd` hooks                   |
| `running`        | `UserPromptSubmit` / 延迟触发的 `Stop` hooks            |
| `waiting`        | `PreToolUse` hook（决策未定时）                          |
| `prompt`         | `PreToolUse` hook payload                             |
| `msg`            | 由当前状态派生的摘要                                      |
| `entries`        | 实时 JSONL tailer（用户提问 / 工具调用 / 助手文本）         |
| `tokens`/`today` | JSONL 中所有 `usage.output_tokens` 之和                  |

## 我们踩过的固件坑（以及绕过办法）

参考固件有几处线协议没说明的尖角。在这里记一笔，省得你重新 debug 一遍，
也让代码里那些绕过逻辑的存在理由可见。

### 1. UTF-8 多字节序列在 BLE 链路上被剪在字符中间

携带 CJK（或任何 UTF-8 多字节内容）的心跳很容易超过默认的 ATT
Write-Without-Response 单包上限（`MTU − 3` 字节，默认 20）。
[`bleak`](https://github.com/hbldh/bleak) 的 `write_gatt_char()`
对 write-without-response **不会自动分包**——溢出部分被静默丢弃。
固件收到的 JSON 在某个 UTF-8 多字节序列**中途**截断（比如"你"的
`0xE4 0xBD 0xA0` 只剩头一个 `0xE4`）。ArduinoJson 解析失败；
TFT_eSPI 的 `decodeUTF8()` 状态机一直等永不到来的续位字节，
后续合法字节也被错误吃掉。渲染/BLE 任务进入异常状态，~1 秒后
看到链路断开。

**之前我们吃过两次错诊，写在这里省得你重新踩：**

- 第一版怪到固件 5×7 GFX 字体只有 ASCII（`96740fd`）。如果整段
  CJK 字节序列**完整**到达固件，这判断本来没错——但根本就没完整到达。
- 第二版发现 `M5StickCPlus` 库内置了 1.7 MB 的 HZK16 GB2312 字体 +
  `loadHzk16(InternalHzk16)` 启用 API（`2099de1`）。事实对，但与症状
  无关——被截断的字节根本进不到字体查找那一步。

真正的根因是 [@omengye](https://github.com/omengye) 在他们的 fork
里诊断出来的，credit 归他们。

**修法已落地于 [`182bfed`](https://github.com/SnowWarri0r/cc-buddy-bridge/commit/182bfed)**
（PR [#14](https://github.com/SnowWarri0r/cc-buddy-bridge/pull/14) 来自
[@omengye](https://github.com/omengye)）：`BuddyBLE.send()` 现在按 `mtu_size − 3`
分包发送、并主动避开在 codepoint 中间切（`_utf8_safe_chunks`）；
`sanitize_for_stick()` 放行所有 BMP 字符，只把补充平面（emoji 主力）、
代理项和 C0/C1 控制字符替换为 `?`。Hook 端 stdin 强制按 UTF-8 解码，
Windows `cp936` 控制台用户再也不会看到 CJK 乱码。

### 2. `entries` 在线上的顺序是从旧到新，不是从新到旧

固件的 `drawHUD` 把 `lines[nLines-1]` 当成最新（也只有那一条会拿到高亮色 +
窗口底部位置）。如果按"从新到旧"发，最新条目反而落到换行缓冲的顶部，
被剪出可见的 3 行窗口外。

**绕过：** daemon 内部把 `state.entries` 维护成"最新在前"（便宜 prepend），
但序列化心跳时 `reversed()` 反向遍历。

### 3. `evt:"turn"` 事件被静默丢弃

REFERENCE.md 定义了 `turn` 事件格式，但固件的 `_applyJson` 只解析心跳字段
（`time`、`total`、`running`、`waiting`、`tokens`、`tokens_today`、`msg`、
`entries`、`prompt`）。任何 `evt` payload 都会被解析然后丢掉——不报错，
也不显示。

**绕过：** 我们把助手的第一段文本以伪造的 `@ <text>` 行形式塞进心跳的
`entries` 列表。固件本来就会渲染 `entries`，所以无需扩展协议。

### 4. Stop hook 比助手记录落盘还早

从 Stop hook 读 transcript JSONL 拿到的是**上一个**回合的内容——Claude Code
写盘是异步的。直白用 Stop 会让每条 `@`-entry 都晚一回合。

**绕过：** Stop 完全不参与内容提取。JSONL tailer 通过 `watchfiles` 监听
transcript 文件，新助手记录一落盘（通常 <500 ms）就触发 `on_assistant_text`
回调。回调立即把条目加进去，stick 通常在你滑动终端往上看之前就已经显示出回复了。

### 5. 时钟模式会在回合结束瞬间盖掉 transcript HUD

固件一旦满足 `running==0 && waiting==0 && on_USB_power` 就直接进表盘模式，
完全跳过 `drawHUD`。我们旧的 `turn_end` 在 Claude 一结束就把 `running` 清零——
导致刚 emit 的 `@` 条目在同一帧就被盖掉。

**绕过：** `turn_end` 用 `asyncio.Task` 调度 15 秒延时再清 `running`。
新的 `turn_begin` 会取消挂起的任务。stick 会保留 HUD 足够久看完回复，
然后在真正空闲时才进表盘。

### 6. LittleFS 不会自动格式化——`push-character` 直到出厂复位前都失败

新固件调用 `LittleFS.begin(false)`（挂载失败不格式化），未初始化的分区会以
0/0 字节挂上。仅有的调用 `LittleFS.format()` 的代码路径是设备菜单里的
**factory reset**（长按 **A** → settings → reset → factory reset → 连按两次）。

`cc-buddy-bridge push-character` 会通过状态 ack 检测到这种情况，并以 `ERROR`
级别打印修复提示。出厂复位是有破坏性的（清掉设置、统计、配对），但每个 stick
只用做一次。

### 7. `blueutil --unpair` 在新版 macOS 上不可靠

干净的 BLE 配对测试需要清掉两侧的绑定。`blueutil` 把 `--unpair` 标记为
`EXPERIMENTAL`；在 macOS Sonoma+ 上它会成功返回但实际并没移掉缓存的 LTK，
后续重连会失败并报 `CBErrorDomain Code=14 "Peer removed pairing information"`。

**绕过：** `cc-buddy-bridge unpair` 经加密通道清掉 stick 这一侧，
但 macOS 那侧需要你手动打开 **系统设置 → 蓝牙 → Claude-5C66 → ⓘ → 忘记此设备**。
之后下次重连会触发新一轮 6 位 passkey 配对。

## 项目状态

可日用——作者每个 Claude Code 会话都在跑它。

**经过实战的基础设施**

* 全新 BLE 配对——MITM + 绑定 + DisplayOnly passkey，端到端验证
* 重连——指数退避 + 多 daemon 防抢（如果 socket 被另一个实例占着就拒绝启动）
* 文件夹推送——分块流控、单包上限 1.8 MB、每块 ack
* stick 状态轮询——每 60 秒拉一次电量 / 加密状态 / fs 剩余空间
* 日志——文件轮转、按组件分级、结构化的权限往返追踪

**测试与 CI**

* 212 个单元测试，覆盖 state、protocol、installer、hud、matchers、JSONL tailer、文件夹推送、各服务后端、BLE 射频恢复
* GitHub Actions 跨 Python 3.11 / 3.12 / 3.13 三档运行

**Backlog**

* 开 issue——任何粗糙边缘、踩到的坑、想要的功能、行为异常的平台

## 贡献

PR、bug 报告、"我在 $某怪发行版 上跑炸了" 故事都欢迎。比小修复大的改动，先开一个 issue 讨论一下设计再动手。

### 开发环境

```bash
git clone https://github.com/SnowWarri0r/cc-buddy-bridge
cd cc-buddy-bridge
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

`[dev]` extra 拉 `pytest` + `ruff`（仅有的开发依赖）。

### 测试与 lint

```bash
.venv/bin/pytest -q                  # ~210 个测试，<1s 跑完
.venv/bin/ruff check src/ tests/     # lint（PR CI 必跑）
```

CI 跨 **macOS / Linux / Windows × Python 3.11 / 3.12 / 3.13** 跑测试。PR 全绿才能合——动到文件系统/路径相关的代码，Windows 通常第一个翻车（NTFS 忽略 POSIX mode bits、反斜杠 vs 正斜杠等）。

### 动线协议之前

固件那边有 [7 个有记录的尖角](#我们踩过的固件坑以及绕过办法)。怀疑 BLE 行为诡异时先扫一眼那一节再翻 `bleak`——多数"链路一直 flap"问题最后查出来是坑 #1（非 ASCII 字节让 BLE 栈崩）或坑 #5（时钟模式抢 HUD）。

### 提交信息

主题行短、小写、≤ 70 字符；之后一段说明"为什么"。`git log --oneline` 翻翻就能感受到风格。别贴 emoji——sanitizer 反正会从 stick 上剥掉。

### 翻译

README 三语版本互为镜像：[English](README.md) / [简体中文](README.zh-CN.md) / [日本語](README.ja.md)。改动用户可见的散文时，尽量三个一起同步；做不到也行，在 PR 描述里标注"另两个还需要翻译"，可以作为 follow-up。

### 固件 PR

buddy 固件在 [anthropics/claude-desktop-buddy](https://github.com/anthropics/claude-desktop-buddy)。那边的改动需要烧到 M5StickC Plus 上验证——bridge 侧的 mock 抓不到线协议错配。PR 描述里明确写清"实测过的"和"还在推理的"——光看 diff 评审者分不清楚。

## Star 趋势

<a href="https://star-history.com/#SnowWarri0r/cc-buddy-bridge&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=SnowWarri0r/cc-buddy-bridge&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=SnowWarri0r/cc-buddy-bridge&type=Date" />
    <img alt="SnowWarri0r/cc-buddy-bridge 的 star 历史曲线" src="https://api.star-history.com/svg?repos=SnowWarri0r/cc-buddy-bridge&type=Date" />
  </picture>
</a>

## 许可证

MIT。详见 [LICENSE](LICENSE)。
