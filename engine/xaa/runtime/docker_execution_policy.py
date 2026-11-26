"""Fixed Docker isolation policy for the local command backend."""

from __future__ import annotations

import re

from xaa.runtime.command_contracts import CommandIntent

_IMAGE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}@sha256:[a-f0-9]{64}$")
_EXECUTION_ID = re.compile(r"^[a-f0-9]{32}$")


def validate_image_reference(value: str) -> str:
    """Require an already selected immutable repository digest."""
    if not _IMAGE_REF.fullmatch(value):
        raise ValueError("Docker execution requires an image pinned by SHA-256 digest.")
    return value


def validate_local_engine_endpoint(value: str, *, windows: bool) -> str:
    """Reject remote Docker contexts; a Docker daemon controls its host as root."""
    allowed = {"npipe:////./pipe/docker_engine"} if windows else {"unix:///var/run/docker.sock"}
    if value not in allowed:
        raise PermissionError("Docker execution is restricted to the local engine endpoint.")
    return value


def docker_container_name(execution_id: str) -> str:
    if not _EXECUTION_ID.fullmatch(execution_id):
        raise ValueError("Invalid Docker execution identity.")
    return f"xaa-{execution_id}"


def docker_create_argv(
    intent: CommandIntent,
    image_reference: str,
    *,
    endpoint: str,
    windows: bool,
) -> tuple[str, ...]:
    """Create an idle private container with size-bounded scratch mounts."""
    validate_local_engine_endpoint(endpoint, windows=windows)
    validate_image_reference(image_reference)
    if intent.backend != "docker-local-v1" or intent.image_reference != image_reference:
        raise PermissionError("Docker launch must match the immutable saved backend grant.")
    argv = (
        "docker",
        "--host",
        endpoint,
        "create",
        "--pull=never",
        "--name",
        docker_container_name(intent.execution_id),
        "--label",
        f"com.xyst.xaa.execution={intent.execution_id}",
        "--network",
        "bridge" if intent.docker_network_access else "none",
        "--ipc",
        "private",
        "--shm-size",
        "33554432",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges=true",
        "--pids-limit",
        "32",
        "--memory",
        "134217728",
        "--memory-swap",
        "134217728",
        "--cpus",
        "0.5",
        "--user",
        "65534:65534",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev,size=33554432",
        "--tmpfs",
        "/workspace:rw,noexec,nosuid,nodev,size=33554432,mode=1777",
        "--env",
        "HOME=/tmp",
        "--env",
        "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "--entrypoint",
        "/bin/sh",
        image_reference,
        "-c",
        "while :; do sleep 3600; done",
    )
    return argv


def docker_copy_argv(
    intent: CommandIntent,
    *,
    endpoint: str,
    windows: bool,
) -> tuple[str, ...]:
    """Extract only the bounded snapshot tar into the private tmpfs workspace."""
    validate_local_engine_endpoint(endpoint, windows=windows)
    return (
        "docker",
        "--host",
        endpoint,
        "exec",
        "--interactive",
        "--user",
        "65534:65534",
        "--workdir",
        "/workspace",
        docker_container_name(intent.execution_id),
        "tar",
        "-xf",
        "-",
        "-C",
        "/workspace",
    )


def docker_exec_argv(
    intent: CommandIntent,
    *,
    endpoint: str,
    windows: bool,
) -> tuple[str, ...]:
    validate_local_engine_endpoint(endpoint, windows=windows)
    return (
        "docker",
        "--host",
        endpoint,
        "exec",
        "--user",
        "65534:65534",
        "--workdir",
        "/workspace",
        docker_container_name(intent.execution_id),
        *intent.argv,
    )
