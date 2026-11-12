from __future__ import annotations

import os
import sys
from pathlib import Path

# Emit one deliberately oversized newline-delimited frame before waiting for
# input. The transport must reject it and close the process tree.
sys.stdout.write('{"jsonrpc":"2.0","id":1,"result":{"padding":"')
sys.stdout.write("x" * (1024 * 1024 + 32))
sys.stdout.write('"}}\n')
sys.stdout.flush()
sys.stdin.buffer.read()
marker = os.environ.get("MCP_OVERSIZED_CLEANUP_MARKER")
if marker:
    Path(marker).write_text("stdin closed", encoding="utf-8")
