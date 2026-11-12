from __future__ import annotations

import os
import sys
from pathlib import Path

from xaa.runtime.mcp_appcontainer import AppContainerAccess

journal_directory = Path(sys.argv[1])
data_directory = Path(sys.argv[2])
AppContainerAccess(
    sys.executable,
    [],
    None,
    [],
    [str(data_directory)],
    journal_directory,
)
# Simulate an engine crash after ACL application and skip Python cleanup hooks.
os._exit(0)
