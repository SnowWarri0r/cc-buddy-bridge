# cc-buddy-bridge

**English** | [简体中文](README.zh-CN.md) | [日本語](README.ja.md)

[![test](https://github.com/SnowWarri0r/cc-buddy-bridge/actions/workflows/test.yml/badge.svg)](https://github.com/SnowWarri0r/cc-buddy-bridge/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)
[![Python: 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![Platforms](https://img.shields.io/badge/platforms-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey.svg)](#requirements)
[![Status: daily-driven](https://img.shields.io/badge/status-daily--driven-brightgreen.svg)](#status)
[![PRs: Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/SnowWarri0r/cc-buddy-bridge/issues)

Bridge [Claude Code](https://claude.com/claude-code) CLI sessions to the
[claude-desktop-buddy](https://github.com/anthropics/claude-desktop-buddy) BLE
hardware — without going through the Claude desktop app.

The buddy firmware officially pairs with Claude for macOS/Windows. This project
lets you drive the same hardware from a plain terminal running the `claude` CLI,
so your desk pet reacts to CLI sessions: sleeps when idle, gets busy when a
tool call runs, blinks when a permission prompt needs your attention, and lets
you approve or deny right from the stick's buttons.

## What you get

- **Physical 2FA for risky tools** — set `defaultMode: bypassPermissions` everywhere except the desk buddy. A/B buttons on the stick decide allow/deny for the few operations you flagged on `permissions.ask`.
- **Smart matcher** — auto-allow trivial Bash (`ls`/`cat`/`grep`/...), always-ask risky (`rm`/`curl`/`git push`/...), defer the rest to the stick. TOML-overridable.
- **Live stick HUD** — assistant replies mirror to the stick within ~500 ms via a JSONL tailer (no Stop-hook flush race).
- **Statusline** — `cc-buddy-bridge hud` renders battery / encryption / **tokens today** / **estimated USD spend today** / pending prompts in your prompt bar; composes with [claude-hud](https://github.com/jarrodwatts/claude-hud).
- **One-command install + autostart** — `cc-buddy-bridge install --service` picks the right backend per OS: launchd (macOS), systemd user unit (Linux), Task Scheduler (Windows).
- **Custom GIF characters** — `cc-buddy-bridge push-character ./pack/` uploads a folder of frames over BLE with chunked flow control.
- **Release notifications + self-update** — daemon pings GitHub releases once a day; hud renders `↑ vX.Y.Z` when a newer tag exists. `cc-buddy-bridge check-update` for a one-off check, `cc-buddy-bridge update` to actually pull + reinstall + restart the daemon. Opt out of polling with `CC_BUDDY_BRIDGE_NO_UPDATE_CHECK=1`.
- **Optional CJK display on the stick** — fork-only firmware variants render Simplified Chinese and Japanese (Traditional Chinese still planned) at 12×12 px using [Fusion Pixel Font](https://github.com/TakWolf/fusion-pixel-font) (OFL). Bridge auto-switches its wire codec when you set `CC_BUDDY_CJK_TARGET=zh-CN` (or `ja`). See [CJK display on the stick](#cjk-display-on-the-stick-optional).

## How it works

```
claude CLI ──PreToolUse/Stop/etc hooks──▶ Unix socket ──▶ daemon ──BLE NUS──▶ stick
                                                           ▲
                                                           └── tails ~/.claude/projects/*.jsonl
                                                               for tokens & recent messages
```

* **Hooks** (configured in `~/.claude/settings.json`) fire on session lifecycle
  events, tool calls, permission requests, and turn boundaries.
* Each hook is a small Python script that posts the event payload to a local
  **daemon** over a Unix socket.
* The daemon aggregates per-session state (`total` / `running` / `waiting` /
  `tokens` / `entries`) and pushes heartbeat snapshots to the stick over BLE
  Nordic UART Service, speaking the same JSON wire format as the desktop app.
* For permission prompts, the hook **blocks** until the stick's buttons decide
  the outcome, then returns `allow` / `deny` to Claude Code.

See [REFERENCE.md in the buddy firmware repo](https://github.com/anthropics/claude-desktop-buddy/blob/main/REFERENCE.md)
for the full wire protocol.

## Install

```bash
git clone https://github.com/SnowWarri0r/cc-buddy-bridge
cd cc-buddy-bridge
python3.12 -m venv .venv
.venv/bin/pip install -e .

# Register hooks into ~/.claude/settings.json (makes a .backup copy first):
.venv/bin/cc-buddy-bridge install

# In another terminal, start the daemon:
.venv/bin/cc-buddy-bridge daemon
```

**Windows users:** Replace `.venv/bin/` with `.venv\Scripts\` in the commands above.

Then start any `claude` session. The daemon scans for a BLE device advertising
a name starting with `Claude`, connects, and begins pushing state.

To remove the hooks:

```bash
.venv/bin/cc-buddy-bridge uninstall
```

### Auto-start on login

Instead of running `cc-buddy-bridge daemon` manually, install it as a
system service so it starts at login and restarts on crashes.

#### macOS (launchd)

Install as a user-level launchd agent:

```bash
.venv/bin/cc-buddy-bridge install --service
```

This writes `~/Library/LaunchAgents/com.github.cc-buddy-bridge.daemon.plist`
pointed at the venv Python you just installed from, runs it immediately via
`launchctl load`, and redirects stdout/stderr to
`~/Library/Logs/cc-buddy-bridge.log`.

To remove it:

```bash
.venv/bin/cc-buddy-bridge uninstall --service
```

#### Windows (Task Scheduler)

Install as a Task Scheduler task:

```bash
.venv/Scripts/cc-buddy-bridge install --service
```

This creates a task named `cc-buddy-bridge-daemon` that runs at logon.
Logs are written to `%LOCALAPPDATA%\cc-buddy-bridge\daemon.log`.

To remove it:

```bash
.venv/Scripts/cc-buddy-bridge uninstall --service
```

#### Linux (systemd)

The same `--service` flag installs a user-level systemd unit on Linux:

```bash
.venv/bin/cc-buddy-bridge install --service
```

This writes `~/.config/systemd/user/cc-buddy-bridge.service` pointed at the
venv Python you just installed from, then runs `systemctl --user
daemon-reload` and `systemctl --user enable --now cc-buddy-bridge.service`
so the daemon starts immediately and on every login. View logs with:

```bash
journalctl --user -u cc-buddy-bridge.service -f
```

To remove it:

```bash
.venv/bin/cc-buddy-bridge uninstall --service
```

A few Linux-specific gotchas:

* **BLE needs BlueZ.** Make sure the `bluetooth` service is running
  (`systemctl status bluetooth`) and your user is in the `bluetooth`
  group (`sudo usermod -aG bluetooth $USER`, then log out and back in).
  Without that, you'll see
  `org.freedesktop.DBus.Error.ServiceUnknown ... org.bluez` in the
  journal.
* **Survive logout / start at boot.** The user manager exits with your
  last session by default, which stops the daemon. Run
  `loginctl enable-linger $USER` once if you want the unit to start at
  boot and persist after logout.

Tested on Ubuntu 22.04 LTS. Should work on any distro with a systemd user
manager (Fedora 39+, Debian 12+, Arch, etc.) — please open an issue if
your distro needs a tweak.

---

`cc-buddy-bridge status` reports both hook and service status.

### Customizing the IPC transport

The daemon and hook scripts talk over a local IPC channel. The default
fits 99% of setups, but two knobs are exposed for the rest:

| OS      | Default transport                  | Override via `--socket` or `CC_BUDDY_BRIDGE_SOCK` |
| ------- | ---------------------------------- | ------------------------------------------------- |
| macOS / Linux | Unix socket `/tmp/cc-buddy-bridge.sock` | another path, e.g. `~/cc-buddy.sock`        |
| Windows | TCP loopback `127.0.0.1:48765`     | another port, e.g. `:49000` or `127.0.0.1:49000`  |

If port 48765 is already in use on Windows, run
`cc-buddy-bridge daemon --socket :49000` and pass the same `--socket` to
`hud` invocations (or `export CC_BUDDY_BRIDGE_SOCK=:49000` to set it once
for all hook scripts).

### Show the stick's state in Claude Code's status line

`cc-buddy-bridge hud` prints a compact one-line summary (battery,
encryption, pending prompts). Plug it into your `~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "/path/to/.venv/bin/cc-buddy-bridge hud"
  }
}
```

For an ASCII-only terminal: `cc-buddy-bridge hud --ascii`.

Already using [claude-hud](https://github.com/jarrodwatts/claude-hud) or
another statusline plugin? You can compose both — wrap them in a small
shell script and concatenate outputs; statusLine accepts multi-line
responses.

Live in iTerm2 — paw, battery bar, encryption lock, today's tokens, today's cost, running session count:

<p align="center"><img src="docs/img/statusline.png" alt="cc-buddy-bridge hud — paw, full green battery bar at 98%, lock, 101K tokens, $106.02 cost, 1 running session" width="580"></p>

Other states the same line goes through:

```
🐾 🔋 96% 🔒                          # healthy, encrypted link
🐾 🔋 96% 🔒 12.3K $0.42              # tokens (≥ 1K) and cost (≥ $0.01) today
🐾 🔋 12% 🔒 1.2M $8.50 2run          # low battery, busy day, sessions running
🐾 ⚠ approve: Bash                    # permission prompt waiting on the stick
🐾 ∅                                  # stick disconnected (but daemon is alive)
🐾 off                                # daemon not running
```

The tokens segment sums today's `usage.output_tokens` across
`~/.claude/projects/*.jsonl`. The cost segment estimates USD from the
same records using `input + output + cache writes/reads × per-model
rates`. Rates table lives in [`pricing.py`](src/cc_buddy_bridge/pricing.py) —
edit to override or add models. Not a billing source of truth; treat
as a heads-up.

## Working with Claude Code's `permissions` config

Claude Code's own `~/.claude/settings.json` `permissions` block (`allow` /
`ask` / `deny` lists, plus `defaultMode`) and this bridge's smart matcher
*both* decide what happens on a tool call. The interaction is well-defined,
but worth spelling out so you can pick the right combo.

For every `PreToolUse` event:

```
matcher classify_command(hint)
 ├─ "allow"  → bridge returns permissionDecision=allow  (short-circuit)
 ├─ "ask"    → bridge waits on stick → returns the button's decision
 └─ "default"→ bridge returns no opinion → Claude Code's settings.json + defaultMode run
```

**Recommended pairings**

| Claude Code `defaultMode` | Matcher `strict` | Behaviour                                                                                                              |
| ------------------------- | ---------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `ask` (the default)       | `false`          | Trivial bash auto-approved by matcher; risky bash routes to stick; everything else gets Claude Code's terminal prompt. |
| `bypassPermissions`       | **`true`**       | **Stick is the sole human-in-the-loop.** Trivial bash auto-approved; everything else (matched OR unmatched) routes to the stick. No terminal prompts. |
| `bypassPermissions`       | `false`          | ⚠ Only matcher's `always_ask` patterns gate at the stick; everything else silently auto-approves. The daemon logs a warning at startup if it detects this combo. |
| `auto`                    | `false`          | Same as `ask` for our purposes — unmatched commands fall through to Claude Code's flow.                                |

`strict` lives in `~/.config/cc-buddy-bridge/matchers.toml`:

```toml
strict = true
```

The daemon logs a one-line summary of both configs at startup so you can
spot misalignments quickly:

```
INFO cc_buddy_bridge.daemon: matcher: strict=False auto_allow=46 always_ask=53
INFO cc_buddy_bridge.daemon: settings.json: permissions.defaultMode='auto' ask=0
```

## Audit log

Every `PreToolUse` decision is appended to a JSONL file so you can review
later what was let through, denied, or deferred — especially valuable
under `bypassPermissions` where most decisions never reach your eyes.

Default location:

| OS      | Path                                                      |
| ------- | --------------------------------------------------------- |
| macOS   | `~/Library/Logs/cc-buddy-bridge-audit.jsonl`              |
| Linux   | `$XDG_DATA_HOME/cc-buddy-bridge/audit.jsonl` (or `~/.local/share/...`) |
| Windows | `%LOCALAPPDATA%\cc-buddy-bridge\audit.jsonl`              |

Override with the `CC_BUDDY_BRIDGE_AUDIT` env var.

One line per decision; fields:

```json
{"ts":"2026-05-16T00:15:12.690+08:00","session":"c461b71c","tool":"Bash",
 "hint":"git status -s","matcher":"allow","decision":"allow","source":"auto_allow"}
```

| Field      | Meaning                                                          |
| ---------- | ---------------------------------------------------------------- |
| `ts`       | ISO-8601 local timestamp with offset                             |
| `session`  | First 8 chars of the Claude Code session id                      |
| `tool`     | Tool name (`Bash`, `Edit`, ...)                                  |
| `hint`     | Short summary of what's being run (truncated to 200 chars)       |
| `matcher`  | Matcher classification: `allow` / `ask` / `default`              |
| `decision` | What the bridge returned: `allow` / `deny` / `null` (deferred)   |
| `source`   | `auto_allow` / `stick` / `timeout` / `defer` / `ble_disconnected`|
| `elapsed_s`| Round-trip seconds (only present when the stick was involved)    |

### Viewing

`cc-buddy-bridge audit` is the friendly viewer — coloured, aligned, with
tail / filter / follow:

```bash
cc-buddy-bridge audit                       # last 20 entries
cc-buddy-bridge audit -n 100                # last 100
cc-buddy-bridge audit -f                    # follow new entries (Ctrl+C to stop)
cc-buddy-bridge audit --decision deny       # only the things you blocked
cc-buddy-bridge audit --source stick        # only stick-decided rounds
cc-buddy-bridge audit --tool Edit -n 50     # last 50 Edit calls
cc-buddy-bridge audit --path                # print the file path and exit
cc-buddy-bridge audit --ascii               # no colour (pipes / dumb terminals)
```

Sample output:

```
# audit log: /Users/snow/Library/Logs/cc-buddy-bridge-audit.jsonl
00:21:09.029 Bash     —     defer       sleep 8 && gh run list --repo ...
00:30:10.212 Bash     allow auto_allow  cat >> tests/test_audit.py <<'EOF' ...
00:34:55.871 Bash     deny  stick       git push origin main --force
```

Colours: green for `allow`, red for `deny`, dim for `—` (no decision /
deferred). Source column is yellow for `stick` (a human pressed a button),
red for `timeout`, dim otherwise.

### Raw jq recipes

If you prefer jq:

```bash
# "What did I deny on the stick today?"
jq 'select(.decision=="deny")' ~/Library/Logs/cc-buddy-bridge-audit.jsonl

# "Top auto-allowed commands this week"
jq -r 'select(.source=="auto_allow") | .hint' ~/Library/Logs/cc-buddy-bridge-audit.jsonl \
  | awk '{print $1}' | sort | uniq -c | sort -rn | head
```

## Update notifications

The daemon polls `https://api.github.com/repos/SnowWarri0r/cc-buddy-bridge/releases/latest`
once a day in the background, caches the result, and surfaces a release nudge in
two places:

* Daemon log at startup if a newer tag exists.
* `cc-buddy-bridge hud` appends `↑ vX.Y.Z` (or `up vX.Y.Z` in `--ascii`) to the
  statusline. Yellow, end of the line, so it doesn't push the battery/cost
  segments off-screen.

One-shot from the CLI:

```bash
cc-buddy-bridge check-update
# Installed:   0.1.0
# Latest:      v0.1.2
#
# Update available: 0.1.0 → v0.1.2
# Pull with:        git pull && pip install -e .
# Then restart:     cc-buddy-bridge install --service  (or kickstart the daemon)
```

Exit code is `1` when an update is available, `0` otherwise — handy in scripts.

### Applying the update

```bash
cc-buddy-bridge update            # prompts y/N, then does the thing
cc-buddy-bridge update -y         # skip the prompt (CI / scripts)
```

Equivalent to `git pull && pip install -e .` from the repo root, followed by
a service restart through whatever backend you installed (launchd / systemd
user unit / Task Scheduler). Bails early and loudly on:

- not running from a git checkout (e.g. you installed from a wheel) — fall
  back to your package manager
- uncommitted local changes — stash or commit first; we will not lose your work
- non-tty without `-y` — refuse to prompt blindly into the void

If the service backend isn't installed (you're running `cc-buddy-bridge
daemon` manually), the install step still runs and you'll get a "restart
the daemon yourself" reminder.

### Privacy

One HTTPS request per day to api.github.com. Disable entirely:

```bash
export CC_BUDDY_BRIDGE_NO_UPDATE_CHECK=1
```

Cache lives at `~/Library/Caches/cc-buddy-bridge/update_check.json` on macOS,
`$XDG_CACHE_HOME/cc-buddy-bridge/...` on Linux, and
`%LOCALAPPDATA%\cc-buddy-bridge\update_check.json` on Windows.

Firmware update detection is out of scope for now — the stick's status ack
doesn't carry a firmware version, and upstream
[anthropics/claude-desktop-buddy](https://github.com/anthropics/claude-desktop-buddy)
has no releases or tags to compare against.

## CJK display on the stick (optional)

Stock firmware ships an ASCII-only 5×7 font — Chinese/Japanese/Korean
content in prompts and responses shows up as rows of `?` on the device
([quirk #1](#1-multi-byte-utf-8-strands-get-truncated-mid-character-on-the-ble-link)).
Upstream's CONTRIBUTING explicitly declines new features, so adding a CJK
font *and* getting it merged isn't realistic. Instead, this is implemented
as a **fork-only firmware build** that you flash yourself if you want
on-device CJK rendering. Stock-firmware users are unaffected — the bridge
keeps its conservative ASCII sanitizer until you tell it otherwise.

### Status

| Target | Firmware build | Bridge codec | Status |
| --- | --- | --- | --- |
| **Simplified Chinese (zh-CN)** | `m5stickc-plus-cjk-zh-cn` | `gbk` | ✅ Ready — GB2312 zones 1-55 (symbols + Level 1 hanzi), ~4300 glyphs |
| Traditional Chinese (zh-TW) | `m5stickc-plus-cjk-zh-tw` | `big5` | 🚧 Planned — bridge codec wired; firmware build still uses Simplified glyphs |
| **Japanese (ja)** | `m5stickc-plus-cjk-ja` | `shift_jis` | ✅ Ready — JIS X 0208 rows 1-47 (kana + Level 1 kanji), ~4400 glyphs; half-width katakana falls through to ASCII |

### How to enable (Simplified Chinese / Japanese)

The worked example below is for Simplified Chinese. For **Japanese**, substitute the
`feat/cjk-display-ja` branch, the `m5stickc-plus-cjk-ja` build, and `CC_BUDDY_CJK_TARGET=ja`
(wire codec `shift_jis`) — the steps are otherwise identical.

1. **Flash the fork's CJK firmware variant.** Clone
   [SnowWarri0r/claude-desktop-buddy](https://github.com/SnowWarri0r/claude-desktop-buddy),
   check out `feat/cjk-display-zh-cn`, and run
   `pio run -e m5stickc-plus-cjk-zh-cn -t upload --upload-port /dev/cu.usbserial-...`
   (substitute your stick's serial path). The CJK build adds ~120 KB to
   the firmware binary; spiffs partition (where GIF character packs live)
   is untouched.

2. **Tell the bridge to send GBK bytes.** Add `CC_BUDDY_CJK_TARGET=zh-CN`
   to the daemon's environment. On macOS:

   ```bash
   plutil -insert EnvironmentVariables.CC_BUDDY_CJK_TARGET -string zh-CN \
       ~/Library/LaunchAgents/com.github.cc-buddy-bridge.daemon.plist
   launchctl bootout gui/$(id -u)/com.github.cc-buddy-bridge.daemon
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.github.cc-buddy-bridge.daemon.plist
   ```

   On Linux: edit `~/.config/systemd/user/cc-buddy-bridge.service`, add
   `Environment=CC_BUDDY_CJK_TARGET=zh-CN` under `[Service]`, then
   `systemctl --user daemon-reload && systemctl --user restart cc-buddy-bridge.service`.

3. **Verify.** The daemon log prints `cjk firmware target=zh-CN, wire codec=gbk`
   at startup. Your next assistant turn with Chinese content should render
   the actual characters on the stick instead of `?`s.

To go back to stock behaviour: remove the env var and re-flash the default
`m5stickc-plus` build.

### What changes behind the scenes

- **Bridge wire format.** `sanitize_for_stick` switches from ASCII-only to
  GBK-encodable; `encode()` builds the heartbeat JSON manually with string
  values encoded as GBK bytes between quotes (keys, numbers, structural
  punctuation stay ASCII). ArduinoJson 7 on the stick accepts this — it
  doesn't validate UTF-8 inside string values.
- **Firmware rendering.** A sprite-aware mixed-script renderer
  (`cjk_render.cpp`) walks each byte: ASCII (< 0x80) draws a 6×12 glyph
  from `Fonts/ASC12.h`; GBK pairs (both bytes 0xA1-0xFE) draw a 12×12
  glyph from `Fonts/GB2312_L1.h`. `drawHUD` runs the line through a
  pixel- and GBK-aware wrap so long entries break cleanly instead of
  clipping at the right edge.

### Font credit

The CJK firmware variants use [Fusion Pixel Font](https://github.com/TakWolf/fusion-pixel-font)
12px monospaced (zh\_hans + Latin), released under the **SIL Open Font
License 1.1**. The OFL text plus per-source attributions for Ark Pixel,
Cubic 11, and Galmuri (which Fusion Pixel merges from) ship in the
firmware fork under `src/Fonts/`. Thank you @TakWolf for making this
available.

### Caveats

- Emoji (supplementary-plane codepoints) are not in any of the three
  byte-pair encodings; the sanitizer replaces them with `?` even in CJK
  mode.
- Level-2 hanzi (GB2312 zones 56-87, rarer characters) aren't shipped to
  save flash; they fall back to `??` at render time. ~99% of daily
  Mandarin use is in Level 1.
- The firmware variant is a downstream fork — there's no auto-update
  story. Re-build & re-flash manually when you pull new firmware changes.

## Requirements

* macOS 12+ / Windows 10+ / Linux with BlueZ
* Python 3.11+
* A flashed claude-desktop-buddy device (M5StickC Plus)
* Claude Code CLI

## Signal mapping

| Buddy field       | Source                                        |
| ----------------- | --------------------------------------------- |
| `total`           | `SessionStart` / `SessionEnd` hooks           |
| `running`         | `UserPromptSubmit` / deferred `Stop` hooks    |
| `waiting`         | `PreToolUse` hook (while decision pending)    |
| `prompt`          | `PreToolUse` hook payload                     |
| `msg`             | Derived summary of current state              |
| `entries`         | Live JSONL tailer (user prompts / tool calls / assistant text) |
| `tokens`/`today`  | Sum of `usage.output_tokens` in JSONL         |

## Firmware quirks we hit (and how we work around them)

The reference firmware has several sharp edges the wire protocol doesn't
warn you about. Documenting them here so you don't re-debug them, and so
the workarounds baked into this codebase have a visible rationale.

### 1. Multi-byte UTF-8 strands get truncated mid-character on the BLE link

A heartbeat carrying CJK (or any UTF-8 multi-byte content) can easily
exceed the default 20-byte ATT Write-Without-Response payload (`MTU − 3`
bytes per packet). [`bleak`](https://github.com/hbldh/bleak)'s
`write_gatt_char()` does not auto-chunk write-without-response — the
overflow is silently dropped. The firmware then sees a JSON message
ending mid-UTF-8 sequence (e.g. a trailing `0xE4` with the two
continuation bytes gone). ArduinoJson rejects the malformed JSON;
TFT_eSPI's `decodeUTF8()` state machine gets stuck waiting for the
continuation bytes that never arrive, corrupting subsequent reads. The
render or BLE task wedges and the link visibly resets ~1 s later.

**Two earlier misdiagnoses we ate, so you don't have to:**

- We first blamed the firmware's ASCII-only 5×7 GFX bitmap font
  (`96740fd`). That would have been right *if* whole CJK byte
  sequences ever reached the firmware — but they didn't.
- We then noticed the `M5StickCPlus` library ships an unused 1.7 MB
  HZK16 GB2312 font with a `loadHzk16(InternalHzk16)` API (`2099de1`).
  True but moot — the corrupted bytes never reach the font lookup
  either way.

The real root cause was diagnosed by
[@omengye](https://github.com/omengye) in their fork; full credit there.

**Fix landed in [`182bfed`](https://github.com/SnowWarri0r/cc-buddy-bridge/commit/182bfed)** (PR [#14](https://github.com/SnowWarri0r/cc-buddy-bridge/pull/14) by [@omengye](https://github.com/omengye)):
`BuddyBLE.send()` now chunks writes at `mtu_size − 3` *and* refuses to end
a chunk mid-codepoint (`_utf8_safe_chunks`). `sanitize_for_stick()` passes
all BMP codepoints through; only supplementary-plane (most emoji),
surrogates, and C0/C1 control chars are still replaced. Hook stdin is
decoded as UTF-8 regardless of the OS console codepage, so Windows
`cp936` users no longer see CJK mojibake.

### 2. `entries` wire order is oldest-first, not newest-first

Firmware's `drawHUD` treats `lines[nLines-1]` as the newest (and only
that one gets the highlight colour + bottom-of-window position).
Sending newest-first makes the latest entry land at the top of the
wrapped buffer and clip out of the visible 3-row window.

**Workaround:** the daemon keeps `state.entries` newest-first
internally (cheap prepend) but `reversed()`-iterates when serializing
the heartbeat.

### 3. `evt:"turn"` events are silently discarded

REFERENCE.md defines a `turn` event format, but the firmware's
`_applyJson` only parses heartbeat fields (`time`, `total`, `running`,
`waiting`, `tokens`, `tokens_today`, `msg`, `entries`, `prompt`). Any
`evt` payload is parsed and dropped — no error, no display.

**Workaround:** we mirror the assistant's first text block into the
heartbeat's `entries` list as a synthetic `@ <text>` row. The firmware
already renders `entries`, so no protocol extension is needed.

### 4. Stop hook fires before the assistant record is flushed to disk

Reading the transcript JSONL from the Stop hook returns the PREVIOUS
turn's content — Claude Code's write to disk is async. Naively this
causes every `@`-entry to be one turn behind.

**Workaround:** we ignore Stop for content extraction entirely. The
JSONL tailer already watches transcript files via `watchfiles`; it
fires an `on_assistant_text` callback the moment a new assistant
record lands (typically <500 ms). The callback adds the entry
immediately, so the stick shows the reply before the user even
scrolls up in the terminal.

### 5. Clock mode hides the transcript HUD on turn end

The firmware enters clock-face mode the instant
`running==0 && waiting==0 && on_USB_power`, bypassing `drawHUD`
entirely. Our old `turn_end` handler flipped `running` to 0 the
moment Claude finished — which made the freshly-emitted `@` entry
invisible within the same frame.

**Workaround:** `turn_end` schedules an `asyncio.Task` that sleeps
15 seconds before flipping `running` to 0. A new `turn_begin` cancels
the pending task. The stick stays on HUD long enough to read the
reply, then goes to clock on genuine idle.

### 6. LittleFS is not auto-formatted — `push-character` fails until factory reset

Fresh firmware calls `LittleFS.begin(false)` (no format-on-fail), so an
uninitialised partition mounts as 0/0 bytes. The only code path that
calls `LittleFS.format()` is the on-device **factory reset** menu
(hold **A** → settings → reset → factory reset → tap twice).

`cc-buddy-bridge push-character` detects this via the status ack and
logs an `ERROR` with the remediation hint. Factory reset is destructive
(wipes settings, stats, bonds) but needed once per stick.

### 7. `blueutil --unpair` is unreliable on modern macOS

For a clean BLE pairing test you need to clear both sides' bonds.
`blueutil` advertises `--unpair` as `EXPERIMENTAL`; on macOS Sonoma+
it returns success without actually removing the cached LTK, and a
subsequent reconnect fails with `CBErrorDomain Code=14 "Peer removed
pairing information"`.

**Workaround:** `cc-buddy-bridge unpair` clears the stick side over
the encrypted channel, but the user has to manually open
**System Settings → Bluetooth → Claude-5C66 → ⓘ → Forget This
Device** on the macOS side. After that, the next reconnect triggers
a fresh 6-digit passkey pairing.

## Status

Daily-driver complete — the author runs it on every Claude Code session.

**Battle-tested infra**

* Fresh BLE pairing — MITM + bonding + DisplayOnly passkey, end-to-end
* Reconnection — exponential backoff + multi-daemon guard (refuse to start if another instance owns the socket)
* Folder push — chunked flow control, 1.8 MB pack cap, per-chunk acks
* Stick status polling — battery / encryption / fs free every 60 s
* Logging — rotating file, per-component levels, structured permission round-trip traces

**Tests + CI**

* 212 unit tests covering state, protocol, installer, hud, matchers, JSONL tailer, folder push, service backends, BLE radio recovery
* GitHub Actions matrix across Python 3.11 / 3.12 / 3.13

**Backlog**

* Open an issue — any rough edge, a quirk you hit, a feature you want, a platform that misbehaves

## Contributing

PRs, bug reports, and "I tried it on $WEIRD_LINUX_DISTRO and it broke" stories
all welcome. For anything larger than a small bug fix, open an issue first so
we can talk through the design.

### Dev setup

```bash
git clone https://github.com/SnowWarri0r/cc-buddy-bridge
cd cc-buddy-bridge
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

The `[dev]` extra pulls in `pytest` + `ruff` (the only dev deps).

### Test & lint

```bash
.venv/bin/pytest -q                  # ~210 tests, finishes in <1s
.venv/bin/ruff check src/ tests/     # lint (CI runs this on every PR)
```

CI runs the test suite across **macOS / Linux / Windows × Python 3.11 / 3.12 / 3.13**.
A PR turns green only when every cell is happy — if you touch anything
filesystem-y or path-y, expect Windows to surface the quirks first (NTFS
ignores POSIX mode bits, backslash vs forward-slash, etc).

### Before touching the wire protocol

The stick firmware has [7 documented sharp edges](#firmware-quirks-we-hit-and-how-we-work-around-them).
Scan that section before chasing weird BLE behaviour through `bleak`. Most
"the link keeps flapping" issues turn out to be quirk #1 (non-ASCII bytes
crash the BLE stack) or quirk #5 (clock mode racing the HUD).

### Commit messages

Short subject line, lowercase, ≤ 70 chars; then a paragraph explaining **why**.
Browse `git log --oneline` for the register. Don't paste emoji — the
sanitizer would strip them from the stick anyway.

### Translations

The README mirrors across [English](README.md), [简体中文](README.zh-CN.md),
and [日本語](README.ja.md). If you touch user-facing prose, please mirror
across all three when you can; otherwise note in the PR that the other two
need a translation pass — happy to take that as a follow-up.

### Firmware patches

The buddy firmware lives at [anthropics/claude-desktop-buddy](https://github.com/anthropics/claude-desktop-buddy).
Changes there need a flashed M5StickC Plus to verify — bridge-side mocks
won't catch wire-protocol misalignments. Be explicit in the PR description
about what you've tested vs. what's still theory; reviewers can't tell from
the diff alone.

## Star history

<a href="https://star-history.com/#SnowWarri0r/cc-buddy-bridge&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=SnowWarri0r/cc-buddy-bridge&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=SnowWarri0r/cc-buddy-bridge&type=Date" />
    <img alt="Star history chart for SnowWarri0r/cc-buddy-bridge" src="https://api.star-history.com/svg?repos=SnowWarri0r/cc-buddy-bridge&type=Date" />
  </picture>
</a>

## License

MIT. See [LICENSE](LICENSE).
