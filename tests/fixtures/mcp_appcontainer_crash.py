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
elif len(sys.argv) > 3 and sys.argv[3] == "temp-journal":

    def crash_during_initial_journal(access: AppContainerAccess) -> None:
        temporary_path = access._journal_directory / (
            f".{access.profile_name}-{os.getpid()}-{access._owner_creation_time}-orphaned.tmp"
        )
        temporary_path.write_text("{", encoding="utf-8")
        os._exit(0)

    AppContainerAccess._write_journal = crash_during_initial_journal
elif len(sys.argv) > 3 and sys.argv[3] == "after-profile":

    def crash_after_profile(_access: AppContainerAccess, _path: str, _mask: int) -> None:
        os._exit(0)

    AppContainerAccess._grant_tree = crash_after_profile

AppContainerAccess(
    sys.executable,
    [],
    None,
    [],
    [str(data_directory)],
    journal_directory,
)
# Simulate an engine crash after ACL application and skip Python cleanup hooks.
os._exit(259 if len(sys.argv) > 3 and sys.argv[3] == "exit-259" else 0)
