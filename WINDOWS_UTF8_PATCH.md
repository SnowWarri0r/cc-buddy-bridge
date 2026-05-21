# PATCH: Add Windows UTF-8 Support to cc-buddy-bridge

## File 1: src/cc_buddy_bridge/cli.py

**Location**: Add after line 8 (after imports)

\\\python
# Add this import
import os
\\\

**Location**: Add at the start of _run_daemon() function (after line 95)

\\\python
def _run_daemon(args: argparse.Namespace) -> int:
    # Force UTF-8 mode on Windows (PEP 540)
    # This ensures daemon and all spawned processes use UTF-8 for I/O
    if sys.platform == "win32":
        os.environ.setdefault("PYTHONIOENCODING", "utf-8:replace")
        os.environ.setdefault("PYTHONUTF8", "1")
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass  # Ignore if stdout/stderr are not reconfigurable (e.g., redirected)
    
    log_file = setup_logging(args.log_level)
    # ... rest of function ...
\\\

## File 2: src/cc_buddy_bridge/hooks/__init__.py

**Location**: Add at the top of the file (after docstring if present)

\\\python
import sys

# Force UTF-8 mode on Windows (PEP 540)
# This ensures hook scripts use UTF-8 for I/O when invoked by Claude Code
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass  # Ignore if stdout/stderr are not reconfigurable
\\\

## File 3: docs/windows-11-manual-validation.md

**Location**: Add a new section at the end

\\\markdown
## UTF-8 Logging on Windows

Windows defaults to code page 936 (GBK/GB2312), which can cause Chinese characters to garble in logs.

### Automatic (Recommended)

The daemon automatically sets UTF-8 mode on Windows via environment variables:
- \PYTHONIOENCODING=utf-8:replace\
- \PYTHONUTF8=1\

No manual configuration needed.

### Manual (If Needed)

If you're running the daemon from PowerShell and see garbled Chinese characters:

\\\powershell
# Set PowerShell's output encoding to UTF-8
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(\False)

# Then run the daemon
.venv\Scripts\cc-buddy-bridge daemon
\\\

### Verification

To verify UTF-8 logging is working:

\\\powershell
# Check the log file encoding (should be UTF-8)
Get-Content .\logs\cc-buddy-bridge.log -Encoding UTF8 | Select-Object -Last 5
\\\

If you see Chinese characters correctly, UTF-8 logging is working.
\\\

## Summary of Changes

| File | Change | Why |
|------|--------|-----|
| cli.py | Add UTF-8 reconfiguration in _run_daemon() | Ensures daemon uses UTF-8 for all I/O |
| hooks/__init__.py | Add UTF-8 reconfiguration at module level | Ensures hook scripts use UTF-8 when invoked by Claude Code |
| windows-11-manual-validation.md | Add UTF-8 logging section | Documents the automatic UTF-8 setup and manual fallback |

## Testing

After applying patches:

\\\powershell
# 1. Reinstall the daemon
.venv\Scripts\cc-buddy-bridge install --service

# 2. Restart the daemon
.venv\Scripts\cc-buddy-bridge daemon

# 3. Trigger a hook with Chinese text (e.g., in Claude Code)
# 4. Verify log file contains correct UTF-8
Get-Content .\logs\cc-buddy-bridge.log -Encoding UTF8 | Select-Object -Last 10
\\\

## References

- **WINDOWS_UTF8_LOGGING_PATTERNS.md** - Comprehensive guide with all patterns and evidence
- **PEP 540** - Python UTF-8 Mode (PYTHONUTF8=1)
- **Windows Code Pages** - CP936 (GBK) is the default on Chinese Windows
