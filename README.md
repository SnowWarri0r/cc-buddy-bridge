# cc-buddy-bridge

[![test](https://github.com/SnowWarri0r/cc-buddy-bridge/actions/workflows/test.yml/badge.svg)](https://github.com/SnowWarri0r/cc-buddy-bridge/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)
[![Python: 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![Status: MVP](https://img.shields.io/badge/status-MVP-orange.svg)](#status)
[![PRs: Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/SnowWarri0r/cc-buddy-bridge/issues)

Bridge [Claude Code](https://claude.com/claude-code) CLI sessions to the
[claude-desktop-buddy](https://github.com/anthropics/claude-desktop-buddy) BLE
hardware — without going through the Claude desktop app.

The buddy firmware officially pairs with Claude for macOS/Windows. This project
lets you drive the same hardware from a plain terminal running the `claude` CLI,
so your desk pet reacts to CLI sessions: sleeps when idle, gets busy when a
tool call runs, blinks when a permission prompt needs your attention, and lets
you approve or deny right from the stick's buttons.

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

Then start any `claude` session. The daemon scans for a BLE device advertising
a name starting with `Claude`, connects, and begins pushing state.

To remove the hooks:

```bash
.venv/bin/cc-buddy-bridge uninstall
```

## Requirements

* macOS 12+ / Linux with BlueZ (Windows untested)
* Python 3.11+
* A flashed claude-desktop-buddy device (M5StickC Plus)
* Claude Code CLI

## Signal mapping

| Buddy field       | Source                                    |
| ----------------- | ----------------------------------------- |
| `total`           | `SessionStart` / `SessionEnd` hooks       |
| `running`         | `UserPromptSubmit` → `Stop` hooks         |
| `waiting`         | `PreToolUse` hook (while decision pending)|
| `prompt`          | `PreToolUse` hook payload                 |
| `msg`             | Derived summary of current state          |
| `entries`         | Last N lines tailed from transcript JSONL |
| `tokens`/`today`  | Sum of `usage.output_tokens` in JSONL     |
| `turn` event      | `Stop` hook + last assistant message      |

## What the stick can (and can't) display

The claude-desktop-buddy firmware uses the M5StickC Plus's default Adafruit GFX
5×7 bitmap font, which only ships with an ASCII glyph table. That has two
consequences:

| Codepoints | Behavior on stick | What we do |
| --- | --- | --- |
| ASCII printable (`0x20`–`0x7E`) | ✅ Renders fine | Forward as-is |
| BMP symbols / CJK (`0x80`–`0xFFFF`) | Renders as a blank box — no crash | Forward as-is (you see *something* happened) |
| Supplementary plane (`>= 0x10000`), mainly emojis | ❌ **Hard-resets the firmware** | Strip to `?` before sending |
| C0 / C1 control chars (except tab) | Undefined; `\n` would break the line protocol | Strip to `?` |

If you need readable Chinese / Japanese on the stick, you'd need a firmware
change (load a CJK font pack into flash). This project stays on the CLI side —
we just make sure nothing we send will crash the device.

## Status

Early. MVP covers heartbeat + permission round-trip. Folder push (streaming GIF
character packs from the CLI) is not implemented yet.

## License

MIT. See [LICENSE](LICENSE).
