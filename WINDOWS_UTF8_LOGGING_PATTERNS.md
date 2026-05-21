# Windows UTF-8/CJK Logging & Subprocess Pitfalls: Node.js + Python Integration

**System State**: Windows 10/11 with **GB2312 (CP936)** default encoding — this is the root cause of all garbling.

## PHASE 1: DIAGNOSIS

### Current System Encoding
- Active code page: 936 (GB2312/GBK)
- .NET Default Encoding: gb2312, CodePage: 936

**Problem**: Windows defaults to **CP936 (GBK/GB2312)**, not UTF-8. When Node.js spawns Python or writes to files, the encoding mismatch causes:
- **Console garbling**: Chinese characters appear as mojibake
- **File corruption**: UTF-8 bytes misinterpreted as GBK
- **PowerShell redirection**: Double-encoding (UTF-8 → GBK → UTF-8)

## PHASE 2: ROOT CAUSES BY LAYER

### Layer 1: Node.js child_process on Windows

**Issue**: spawn() and exec() inherit the system's **OEM code page (CP936)**, not UTF-8.

**Reality on Windows**: The encoding option only affects how Node.js **interprets** the buffer, not what encoding the child process **outputs**. If the child (Python, PowerShell) outputs GBK, Node.js decoding it as UTF-8 produces garbled text.

### Layer 2: Python sys.stdout Encoding on Windows

**Issue**: When Python is spawned from Node.js (not attached to a console), it defaults to the system's **ANSI code page (CP1252 on US Windows, CP936 on Chinese Windows)**, not UTF-8.

**Why**: Python uses locale.getpreferredencoding(False) which returns the system's ANSI code page when stdout is not a console.

## PHASE 3: CONCRETE PATTERNS

### Pattern 1: Node.js → Python Subprocess (Capture Output)

**Problem**: Python outputs GBK, Node.js decodes as UTF-8 → garbled.

**Solution A: Force Python to UTF-8 Mode** (Recommended)

const { spawn } = require('child_process');

const python = spawn('python', ['-u', 'script.py'], {
  env: {
    ...process.env,
    PYTHONIOENCODING: 'utf-8:replace',  // Force UTF-8 output
    PYTHONUTF8: '1',                     // Python 3.7+: UTF-8 mode
  },
  encoding: 'utf-8',  // Node.js decodes as UTF-8
});

python.stdout.on('data', (data) => {
  console.log(data);  // Now correctly decoded
});

python.stderr.on('data', (data) => {
  console.error(data);
});

**Why this works**:
- PYTHONIOENCODING=utf-8:replace: Forces Python to encode stdout as UTF-8
- PYTHONUTF8=1: Enables Python UTF-8 Mode (PEP 540)
- encoding: 'utf-8': Tells Node.js to decode the output as UTF-8
- -u: Unbuffered output (important for real-time logging)

### Pattern 2: Node.js → PowerShell (UTF-8 Code Page)

**Problem**: PowerShell defaults to OEM code page (CP936), so it outputs GBK.

**Solution: Prepend chcp 65001 to Switch to UTF-8**

const { exec } = require('child_process');

const command = process.platform === 'win32'
  ? chcp 65001 >nul && powershell -Command "Get-ChildItem"
  : powershell -Command "Get-ChildItem";

exec(command, { encoding: 'utf-8' }, (err, stdout, stderr) => {
  console.log(stdout);  // Now UTF-8 encoded
});

**Why this works**:
- chcp 65001: Sets console code page to UTF-8 (CP65001)
- >nul: Suppresses the "Active code page: 65001" message
- &&: Ensures PowerShell runs after code page is set

### Pattern 3: Python File Logging (UTF-8 Without Corruption)

**Problem**: Python writes to file in CP936, Node.js reads as UTF-8 → garbled.

**Solution: Force Python UTF-8 Mode + Explicit Encoding**

import sys
import logging

# Force UTF-8 mode (Python 3.7+)
sys.stdout.reconfigure(encoding='utf-8')

# Configure logging with explicit UTF-8
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('app.log', encoding='utf-8'),  # Explicit UTF-8
        logging.StreamHandler(sys.stdout),
    ]
)

logger = logging.getLogger(__name__)
logger.info('你好')  # Now correctly written as UTF-8

### Pattern 4: Node.js File Append (UTF-8 Without BOM)

**Problem**: Excel/editors misinterpret UTF-8 files without BOM as ANSI.

**Solution: Explicit UTF-8, No BOM**

const fs = require('fs');

// Append UTF-8 without BOM (Node.js default)
fs.appendFileSync('log.txt', '你好\n', 'utf-8');

// If you need BOM (for Excel compatibility):
const BOM = '\ufeff';
fs.appendFileSync('log.txt', BOM + '你好\n', 'utf-8');

## KEY REFERENCES

1. Node.js child_process: https://nodejs.org/api/child_process.html
2. Python PYTHONIOENCODING: https://docs.python.org/3/using/cmdline.html#envvar-PYTHONIOENCODING
3. PEP 540 (Python UTF-8 Mode): https://peps.python.org/pep-0540/
4. Windows Code Pages: https://learn.microsoft.com/en-us/windows/win32/intl/code-page-identifiers
5. GitHub Issue #57780 (Node.js UTF-8 on Windows): https://github.com/nodejs/node/issues/57780
6. GitHub Issue #50519 (OpenClaw exec encoding): https://github.com/openclaw/openclaw/issues/50519
7. StackOverflow: PowerShell UTF-8: https://stackoverflow.com/questions/77459245/
8. StackOverflow: Python subprocess encoding: https://stackoverflow.com/questions/68244035/
