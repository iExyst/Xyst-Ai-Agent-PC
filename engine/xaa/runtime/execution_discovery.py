"""Bounded installed-distro discovery; listing never starts a distro or installs one."""

import os
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from xaa.runtime.owned_transport import request_process


class InstalledDistribution(BaseModel):
    name: str
    supported_by_profile: bool


class DistributionInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: Literal["available", "unsupported", "unavailable"]
    distributions: list[InstalledDistribution]


def parse_distributions(raw: bytes) -> list[InstalledDistribution]:
    if len(raw) > 16384:
        raise ValueError("Distribution inventory exceeded its limit.")
    text = (
        raw.decode("utf-16")
        if raw.startswith((b"\xff\xfe", b"\xfe\xff"))
        else (raw.decode("utf-16-le") if b"\0" in raw else raw.decode("utf-8-sig"))
    )
    names = [line.strip() for line in text.splitlines() if line.strip()]
    if (
        len(names) > 64
        or len(set(names)) != len(names)
        or any(len(name) > 128 or not name.isprintable() for name in names)
    ):
        raise ValueError("Unsupported distribution inventory format.")
    return [
        InstalledDistribution(
            name=name,
            supported_by_profile=bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", name)),
        )
        for name in sorted(names, key=str.casefold)
    ]


async def installed_distributions() -> DistributionInventory:
    if os.name != "nt":
        return DistributionInventory(state="unsupported", distributions=[])
    executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/wsl.exe"
    try:
        raw = await request_process(
            [str(executable), "--list", "--quiet"], b"", timeout=10, output_limit=16384
        )
        return DistributionInventory(state="available", distributions=parse_distributions(raw))
    except (OSError, RuntimeError, ValueError):
        return DistributionInventory(state="unavailable", distributions=[])
