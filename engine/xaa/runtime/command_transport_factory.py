"""Create only the command backend named by a persisted owner grant."""

from __future__ import annotations

from xaa.runtime.command_contracts import CommandGrant, CommandIntent
from xaa.runtime.command_transport import CommandTransport
from xaa.runtime.windows_local_transport import WindowsLocalTransport
from xaa.runtime.wsl_transport import WSLTransport


async def discover_command_transport(
    authority: CommandGrant | CommandIntent, *, deadline: float
) -> CommandTransport:
    if authority.backend == "windows-appcontainer-v1":
        return await WindowsLocalTransport.discover(deadline=deadline)
    if authority.backend == "wsl-bubblewrap-v1":
        return await WSLTransport.discover(authority.distribution, deadline=deadline)
    raise ValueError("The saved command grant names an unsupported backend.")
