from __future__ import annotations

import os
import sys
from pathlib import Path

from xaa.runtime.mcp_appcontainer import AppContainerAccess

journal_directory = Path(sys.argv[1])
data_directory = Path(sys.argv[2])
if len(sys.argv) > 3 and sys.argv[3] == "before-acl":

    def crash_before_acl(access: AppContainerAccess, path: str, mask: int, flags: int) -> None:
        access._grants.append((path, mask, flags))
        access._write_journal()
        os._exit(0)

    AppContainerAccess._add_ace = crash_before_acl

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
