"""Optional, non-loading LM Studio model memory estimates."""

from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

EstimateStatus = Literal["estimated", "unavailable", "failed"]
GuardrailStatus = Literal["may_load", "warning", "unknown"]

_MEMORY_LINE = re.compile(
    r"Estimated\s+(GPU|Total)\s+Memory:\s*([0-9]+(?:\.[0-9]+)?)\s*"
    r"(B|KB|MB|GB|TB|KiB|MiB|GiB|TiB)\b",
    re.IGNORECASE,
)
_DECIMAL_UNITS = {"B": 1, "KB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4}
_BINARY_UNITS = {
    "KIB": 1024,
    "MIB": 1024**2,
    "GIB": 1024**3,
    "TIB": 1024**4,
}


@dataclass(frozen=True, slots=True)
class ModelResourceEstimate:
    """A bounded LM Studio estimate. It is advisory and never loads a model."""

    model: str
    context_length: int | None
    status: EstimateStatus
    estimated_gpu_memory_bytes: int | None = None
    estimated_total_memory_bytes: int | None = None
    guardrail: GuardrailStatus = "unknown"
    message: str = ""


def _memory_bytes(value: str, unit: str) -> int:
    multiplier = _DECIMAL_UNITS.get(unit.upper(), _BINARY_UNITS.get(unit.upper()))
    if multiplier is None:
        raise ValueError("Unsupported memory unit.")
    return round(float(value) * multiplier)


def _parse_estimate(
    model: str, context_length: int | None, output: str
) -> ModelResourceEstimate | None:
    memory: dict[str, int] = {}
    for label, value, unit in _MEMORY_LINE.findall(output[:16_384]):
        memory[label.lower()] = _memory_bytes(value, unit)
    if not memory:
        return None
    normalized = output.casefold()
    if "may be loaded based on your resource guardrails" in normalized:
        guardrail: GuardrailStatus = "may_load"
    elif "may not be loaded" in normalized or "likely to run out of memory" in normalized:
        guardrail = "warning"
    else:
        guardrail = "unknown"
    return ModelResourceEstimate(
        model=model,
        context_length=context_length,
        status="estimated",
        estimated_gpu_memory_bytes=memory.get("gpu"),
        estimated_total_memory_bytes=memory.get("total"),
        guardrail=guardrail,
        message="Estimate only; no model was loaded or downloaded.",
    )


def _find_lms_executable() -> str | None:
    candidates = ("lms.exe", "lms") if os.name == "nt" else ("lms",)
    for candidate in candidates:
        found = shutil.which(candidate)
        if found and Path(found).suffix.casefold() not in {".bat", ".cmd"}:
            return found
    return None


def _child_environment() -> dict[str, str]:
    """Pass runtime paths only; do not copy application credentials into the CLI."""
    allowed = {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "LOCALAPPDATA",
        "APPDATA",
        "USERPROFILE",
        "HOME",
        "LANG",
        "LC_ALL",
    }
    return {key: value for key, value in os.environ.items() if key.upper() in allowed}


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    """Stop the estimator if it is still running, tolerating a process exit race."""
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    await process.wait()


async def estimate_model_resources(
    model: str,
    *,
    host: str,
    context_length: int | None = None,
    timeout_seconds: float = 15.0,
) -> ModelResourceEstimate:
    """Use LM Studio's documented estimate-only CLI mode when it is installed."""
    executable = _find_lms_executable()
    if executable is None:
        return ModelResourceEstimate(
            model=model,
            context_length=context_length,
            status="unavailable",
            message=(
                "The LM Studio command-line estimator was not found. Load estimates remain "
                "available in LM Studio; X.A.A. did not start a model."
            ),
        )

    try:
        address = urlsplit(host)
        address_port = address.port
        ip_address = (
            ipaddress.ip_address(address.hostname)
            if address.hostname and address.hostname != "localhost"
            else None
        )
    except ValueError:
        address_port = None
        ip_address = None
        address = urlsplit("")
    if (
        address.scheme != "http"
        or not address.hostname
        or (address.hostname != "localhost" and (ip_address is None or not ip_address.is_loopback))
        or address.username is not None
        or address.password is not None
        or address.path not in {"", "/"}
        or address.query
        or address.fragment
    ):
        return ModelResourceEstimate(
            model=model,
            context_length=context_length,
            status="failed",
            message="LM Studio's resource estimator requires a valid HTTP server address. No model was loaded.",
        )

    arguments = [executable, "load", "--estimate-only", model, "--host", address.hostname]
    arguments.extend(("--port", str(address_port or 80)))
    if context_length is not None:
        arguments.extend(("--context-length", str(context_length)))
    process: asyncio.subprocess.Process | None = None
    try:
        process = await asyncio.create_subprocess_exec(
            *arguments,
            stdin=subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_child_environment(),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout_seconds)
    except TimeoutError:
        if process is not None:
            await _stop_process(process)
        return ModelResourceEstimate(
            model=model,
            context_length=context_length,
            status="failed",
            message="LM Studio's resource estimate timed out. No model was loaded.",
        )
    except asyncio.CancelledError:
        if process is not None:
            await _stop_process(process)
        raise
    except OSError:
        return ModelResourceEstimate(
            model=model,
            context_length=context_length,
            status="unavailable",
            message="LM Studio's command-line estimator could not be started.",
        )

    if process.returncode != 0:
        return ModelResourceEstimate(
            model=model,
            context_length=context_length,
            status="failed",
            message=(
                "LM Studio could not estimate this model. Confirm it is installed and that "
                "the LM Studio CLI supports estimate-only mode. No model was loaded."
            ),
        )
    output = "\n".join(part.decode("utf-8", errors="replace") for part in (stdout, stderr))
    parsed = _parse_estimate(model, context_length, output)
    if parsed is None:
        return ModelResourceEstimate(
            model=model,
            context_length=context_length,
            status="failed",
            message=(
                "LM Studio returned no recognized memory estimate. No model was loaded; "
                "review the selected model in LM Studio."
            ),
        )
    return parsed
