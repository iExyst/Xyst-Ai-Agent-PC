"""Create only the command backend named by a persisted owner grant."""

from __future__ import annotations

from xaa.runtime.command_contracts import CommandGrant, CommandIntent
from xaa.runtime.command_transport import CommandTransport
from xaa.runtime.docker_local_transport import DockerLocalTransport
from xaa.runtime.ssh_transport import SSH_BACKEND, SSHTransport
from xaa.runtime.windows_local_transport import WindowsLocalTransport
from xaa.runtime.wsl_transport import WSLTransport


async def discover_command_transport(
    authority: CommandGrant | CommandIntent, *, deadline: float
) -> CommandTransport:
    if authority.backend == "windows-appcontainer-v1":
        return await WindowsLocalTransport.discover(deadline=deadline)
    if authority.backend == "wsl-bubblewrap-v1":
        return await WSLTransport.discover(authority.distribution, deadline=deadline)
    if authority.backend == "docker-local-v1":
        return await DockerLocalTransport.discover(authority, deadline=deadline)
    if authority.backend == SSH_BACKEND:
        if authority.ssh_identity_file is None:
            raise ValueError("The saved SSH grant omits its selected identity file.")
        return await SSHTransport.discover(
            authority.distribution, authority.ssh_identity_file, deadline=deadline
        )
    raise ValueError("The saved command grant names an unsupported backend.")
