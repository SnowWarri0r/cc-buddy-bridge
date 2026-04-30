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
- **状态栏组件** —— `cc-buddy-bridge hud` 在终端 prompt 渲染电量 / 加密状态 / 待处理权限提示；可与 [claude-hud](https://github.com/jarrodwatts/claude-hud) 组合使用。
- **一行命令安装 + 开机自启** —— `cc-buddy-bridge install --service` 自动选对每个 OS 的后端：macOS 用 launchd、Linux 用 systemd 用户级 unit、Windows 用任务计划程序。
- **自定义 GIF 角色** —— `cc-buddy-bridge push-character ./pack/` 通过 BLE 上传一整个动画包，自带分块流控。

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

实拍 iTerm2——爪印、电量进度条、加密锁、运行中的会话数：

<p align="center"><img src="docs/img/statusline.png" alt="cc-buddy-bridge hud — 爪印、满格绿色电量条、100%、锁、1run" width="436"></p>

同一行还会经过的其它状态：

```
🐾 🔋 96% 🔒              # 链路加密、电量充足
🐾 🔋 12% 🔒 2run         # 低电量、有会话在跑
🐾 ⚠ approve: Bash        # stick 上有待处理权限提示
🐾 ∅                      # stick 已断连（但 daemon 还活着）
🐾 off                    # daemon 没在跑
```

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

### 1. 非 ASCII 字节会让 BLE 栈崩

5×7 的 Adafruit GFX 位图字体表只覆盖 ASCII；任何 `0x80`–`0xFF`
范围的字节（也就是所有 UTF-8 续位字节和 emoji 起始字节）会越过字体表索引，
在足够多的代码路径里都能在心跳写入后 ~1 秒内把 radio 任务硬重置。

**绕过：** `protocol.py` 里的 `sanitize_for_stick()` 在发送前把 `0x20`–`0x7E`
（外加 tab）以外的字节全部改写成 `?`。CJK 用户在 stick 上会看到一排排
`?`——有损但稳定。

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

* 98 个单元测试，覆盖 state、protocol、installer、hud、matchers、JSONL tailer、文件夹推送、各服务后端
* GitHub Actions 跨 Python 3.11 / 3.12 / 3.13 三档运行

**Backlog**

* 开 issue——任何粗糙边缘、踩到的坑、想要的功能、行为异常的平台

## 许可证

MIT。详见 [LICENSE](LICENSE)。
