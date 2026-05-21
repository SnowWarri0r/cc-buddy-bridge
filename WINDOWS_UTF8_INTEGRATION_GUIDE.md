# Integration Guide: Windows UTF-8/CJK Logging for cc-buddy-bridge

## Current State

Your project is **already well-positioned** for UTF-8 logging:

✅ **logging_setup.py (line 86)**: File handler uses encoding="utf-8"
✅ **_client.py (line 34)**: Hook input cleaning uses encode("utf-8", errors="replace")
✅ **_client.py (line 63)**: Hook output uses encode("utf-8")
✅ **installer.py (lines 54, 60)**: Settings JSON uses encoding="utf-8"

## Remaining Gaps (Windows-Specific)

### Gap 1: Python Subprocess Stdout Encoding (If Needed in Future)

**Current**: No subprocess spawns detected in codebase.

**If you add subprocess calls** (e.g., to invoke external tools), use this pattern:

\\\python
import subprocess
import sys

# For spawning Python scripts or external tools on Windows
result = subprocess.run(
    [sys.executable, "script.py"],
    env={
        **os.environ,
        "PYTHONIOENCODING": "utf-8:replace",  # Force Python UTF-8 output
        "PYTHONUTF8": "1",                     # Enable PEP 540 UTF-8 Mode
    },
    capture_output=True,
    text=True,  # Decode as UTF-8
    encoding="utf-8",  # Explicit UTF-8 decoding
)
\\\

### Gap 2: Hook Execution Environment (Windows Task Scheduler)

**Current**: service_windows.py uses schtasks to register daemon.

**Issue**: When Task Scheduler runs the daemon, it inherits the system's **CP936 (GBK)** code page, not UTF-8.

**Solution**: Add environment variable setup in the daemon startup.

**File**: \src/cc_buddy_bridge/daemon.py\

Add this at the very top of the main() function:

\\\python
import os
import sys

def main():
    # Force UTF-8 mode on Windows (PEP 540)
    if sys.platform == "win32":
        os.environ.setdefault("PYTHONIOENCODING", "utf-8:replace")
        os.environ.setdefault("PYTHONUTF8", "1")
        # Reconfigure stdout/stderr to UTF-8
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    
    # ... rest of main() ...
\\\

### Gap 3: Hook Script Execution (Windows)

**Current**: Hooks are invoked by Claude Code CLI as Python modules.

**Issue**: When Claude Code spawns hook scripts on Windows, they inherit CP936.

**Solution**: Add UTF-8 reconfiguration to each hook's entry point.

**File**: \src/cc_buddy_bridge/hooks/__init__.py\ (or each hook module)

Add this pattern to every hook that reads/writes text:

\\\python
import sys

# At module level, before any I/O
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
\\\

### Gap 4: PowerShell Redirection (If Users Pipe Logs)

**Current**: Logs are written to file (UTF-8, correct).

**Issue**: If users pipe logs in PowerShell, they may see garbling.

**Solution**: Document in README or docs/windows-11-manual-validation.md:

\\\powershell
# Before running cc-buddy-bridge commands in PowerShell:
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(\False)

# Then run:
.venv\Scripts\cc-buddy-bridge daemon
\\\

## Verification Checklist

- [ ] **Daemon startup**: Add UTF-8 reconfiguration to daemon.py main()
- [ ] **Hook startup**: Add UTF-8 reconfiguration to hooks/__init__.py
- [ ] **Test on Windows**: Run daemon, trigger hooks, verify log file is UTF-8 without garbling
- [ ] **Documentation**: Add PowerShell encoding note to README or windows-11-manual-validation.md
- [ ] **Round-trip test**: Write Chinese characters in hook → read from log file → verify no garbling

## Files to Modify

1. **src/cc_buddy_bridge/daemon.py** - Add UTF-8 reconfiguration to main()
2. **src/cc_buddy_bridge/hooks/__init__.py** - Add UTF-8 reconfiguration at module level
3. **docs/windows-11-manual-validation.md** - Add PowerShell encoding note
4. **README.md** (optional) - Add Windows UTF-8 troubleshooting section

## Testing

After applying changes:

\\\powershell
# Set PowerShell encoding
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(\False)

# Start daemon
.venv\Scripts\cc-buddy-bridge daemon

# In another terminal, trigger a hook with Chinese text
# Verify log file contains correct UTF-8 (no garbling)
Get-Content .\logs\cc-buddy-bridge.log -Encoding UTF8 | Select-Object -Last 10
\\\

## References

- **WINDOWS_UTF8_LOGGING_PATTERNS.md** - Comprehensive guide with all patterns
- **GitHub Issue #57780** - Node.js UTF-8 on Windows (applicable to Python too)
- **PEP 540** - Python UTF-8 Mode (PYTHONUTF8=1)
- **Windows Code Pages** - CP936 (GBK) is the default on Chinese Windows
