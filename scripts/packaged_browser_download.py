"""Verify a managed download through a packaged Windows engine worker."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, cast

from xaa.runtime.browser import BrowserWorkerResponse


def verify_packaged_browser_download(engine: Path) -> dict[str, Any]:
    host = "www.rfc-editor.org"
    url = "https://www.rfc-editor.org/rfc/rfc9110"
    with tempfile.TemporaryDirectory(prefix="xaa-packaged-browser-download-") as temporary:
        staging = Path(temporary).resolve(strict=True)
        request = {
            "url": url,
            "approved_hosts": [host],
            "download_link_name": "Plain Text",
            "download_directory": str(staging),
        }
        completed = subprocess.run(
            [str(engine), "--browser-worker"],
            input=json.dumps(request, separators=(",", ":")).encode(),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=60,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if completed.returncode != 0 or len(completed.stdout) > 1_048_576:
            raise RuntimeError("Packaged download worker exited without a bounded result.")
        response = BrowserWorkerResponse.model_validate_json(completed.stdout)
        if not response.ok:
            raise RuntimeError(response.error or "Packaged download worker failed.")
        result = cast(dict[str, Any] | None, response.result)
        if not result:
            raise RuntimeError("Packaged download worker returned no result.")
        metadata_value = result.get("download")
        if not isinstance(metadata_value, dict):
            raise TypeError("Packaged worker returned no download metadata.")
        metadata = cast(dict[str, Any], metadata_value)
        downloaded = (staging / "download.bin").resolve(strict=True)
        if downloaded.parent != staging or not downloaded.is_file():
            raise PermissionError("Packaged worker wrote outside its managed staging directory.")
        payload = downloaded.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        if (
            not payload
            or len(payload) > 8 * 1024 * 1024
            or len(payload) != metadata.get("size_bytes")
            or digest != metadata.get("sha256")
            or b"RFC 9110" not in payload
        ):
            raise AssertionError(
                "Packaged download bytes exceeded limits or failed integrity checks."
            )
        return {
            "status": "ok",
            "url": result.get("url"),
            "suggested_filename": metadata.get("suggested_filename"),
            "size_bytes": len(payload),
            "sha256": digest,
            "artifact_integrity_verified": True,
            "rfc_9110_marker_verified": True,
        }
