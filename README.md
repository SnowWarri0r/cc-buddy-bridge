# cc-buddy-bridge

[![test](https://github.com/SnowWarri0r/cc-buddy-bridge/actions/workflows/test.yml/badge.svg)](https://github.com/SnowWarri0r/cc-buddy-bridge/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)
[![Python: 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
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

### Auto-start on login (macOS)

Instead of running `cc-buddy-bridge daemon` manually, install it as a
user-level launchd agent so it starts at login and restarts on crashes:

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

`cc-buddy-bridge status` reports both hook and service status.

Linux (systemd user unit) is tracked in
[issue #4](https://github.com/SnowWarri0r/cc-buddy-bridge/issues/4); help
wanted.

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

Sample output:

```
🐾 🔋 96% 🔒              # healthy, encrypted link
🐾 🔋 12% 🔒 2run         # low battery, sessions running
🐾 ⚠ approve: Bash        # permission prompt waiting on the stick
🐾 ∅                      # stick disconnected (but daemon is alive)
🐾 off                    # daemon not running
```

## Requirements

* macOS 12+ / Linux with BlueZ (Windows untested)
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

### 1. Non-ASCII bytes crash the BLE stack

The 5×7 Adafruit GFX bitmap font table is ASCII-only; any byte in
`0x80`–`0xFF` (i.e. every UTF-8 continuation byte and emoji leading
byte) indexes past the glyph table and, in enough code paths, hard-
resets the radio task within ~1 s of the heartbeat write.

**Workaround:** `sanitize_for_stick()` in `protocol.py` rewrites
everything outside `0x20`–`0x7E` (and tab) to `?` before sending. CJK
users will see rows of `?` on the stick, which is lossy but stable.

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

Daily-driver complete. The author runs it on every Claude Code session.

**Core protocol**

* Heartbeat — sessions / tokens / entries / pending prompt
* Permission round-trip — stick A/B buttons decide; signed back into Claude Code
* Folder push — upload GIF character packs over BLE with flow control
* Fresh BLE pairing — MITM + bonding + DisplayOnly passkey, end-to-end tested

**Workflow**

* Smart matcher — auto-allow trivial Bash (`ls`/`cat`/`grep`/...), always-ask risky (`rm`/`curl`/`git push`/...), defer the rest
* Live assistant text — JSONL tailer fires entry-emit within ~500 ms of the message hitting disk; no Stop-hook lag
* Turn-end notification — macOS Notification Center banner + Glass sound + stick `celebrate` animation, fires the moment a turn finishes
* Status-line `hud` subcommand — emoji bar with battery progress, encryption, pending prompts; composes alongside claude-hud

**Operations**

* macOS launchd service — auto-start on login, restart on crash
* Stick status polling — battery %, link encryption, fs free
* Exponential reconnect backoff + multi-daemon guard + resilient logging

**Backlog**

* Linux systemd user unit — see [issue #4](https://github.com/SnowWarri0r/cc-buddy-bridge/issues/4); help wanted
* Anything else — open an issue

## License

MIT. See [LICENSE](LICENSE).
