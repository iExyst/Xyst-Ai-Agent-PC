"""AppContainer worker for bounded, streamed PyPI wheel acquisition."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any, cast

from xaa.extensions.procedure_dependencies import ProcedurePythonDependencyLock
from xaa.extensions.pypi_resolver_worker import (
    PyPIBrokerTransport,
    write_protocol_frame,
)
from xaa.extensions.pypi_wheels import PyPIAcquisitionError, PyPIWheelAcquirer

MAX_ACQUISITION_REQUEST_BYTES = 4 * 1024 * 1024


def _load_lock() -> ProcedurePythonDependencyLock:
    path = Path.cwd() / "request.json"
    if path.is_symlink() or not path.is_file():
        raise ValueError("Wheel acquisition input is missing.")
    raw = path.read_bytes()
    if len(raw) > MAX_ACQUISITION_REQUEST_BYTES:
        raise ValueError("Wheel acquisition input exceeded its size limit.")
    value: object = json.loads(raw)
    if not isinstance(value, dict) or set(cast(dict[str, Any], value)) != {"lock"}:
        raise ValueError("Wheel acquisition input is malformed.")
    return ProcedurePythonDependencyLock.model_validate(
        cast(dict[str, Any], value)["lock"], strict=True
    )


async def _acquire(lock: ProcedurePythonDependencyLock) -> tuple[str, ...]:
    paths = await PyPIWheelAcquirer(transport=PyPIBrokerTransport()).acquire_to_directory(
        lock, Path.cwd() / "wheels"
    )
    return tuple(sorted(paths))


def main() -> int:
    try:
        lock = _load_lock()
        filenames = asyncio.run(_acquire(lock))
        write_protocol_frame(
            {
                "type": "result",
                "ok": True,
                "lock_sha256": hashlib.sha256(lock.canonical_json().encode("utf-8")).hexdigest(),
                "filenames": list(filenames),
            }
        )
        return 0
    except PyPIAcquisitionError as exc:
        write_protocol_frame({"type": "result", "ok": False, "error": str(exc)[:512]})
        return 2
    except Exception:  # noqa: BLE001 - do not disclose worker paths or host details.
        write_protocol_frame(
            {"type": "result", "ok": False, "error": "Isolated wheel acquisition failed."}
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
