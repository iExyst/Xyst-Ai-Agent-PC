"""Forward optional GitHub Actions signing secrets to the Windows installer builder."""

import base64
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

MAX_CERTIFICATE_BYTES = 16 * 1024 * 1024


def _certificate_bytes(encoded: str) -> bytes:
    compact = "".join(encoded.split())
    if not compact or len(compact) > ((MAX_CERTIFICATE_BYTES + 2) // 3) * 4:
        raise ValueError("The configured signing certificate is empty or too large.")
    try:
        content = base64.b64decode(compact, validate=True)
    except ValueError as exc:
        raise ValueError("The configured signing certificate is not valid base64.") from exc
    if not content or len(content) > MAX_CERTIFICATE_BYTES or content[:1] != b"\x30":
        raise ValueError("The configured signing certificate is not a bounded PFX file.")
    return content


def _write_github_output(environment: dict[str, str], signing_mode: str) -> None:
    output_path = environment.get("GITHUB_OUTPUT")
    if output_path:
        with Path(output_path).open("a", encoding="utf-8", newline="\n") as output:
            output.write(f"signing_mode={signing_mode}\n")


def build(environment: dict[str, str] | None = None) -> str:
    source_environment = dict(os.environ if environment is None else environment)
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("The pinned uv executable is required to build the installer.")
    command = [uv, "run", "python", "scripts/build_installer.py"]
    encoded_certificate = source_environment.get("XAA_SIGNING_CERTIFICATE_BASE64", "")
    if not encoded_certificate.strip():
        build_environment = dict(source_environment)
        build_environment.pop("XAA_SIGNING_CERTIFICATE", None)
        build_environment.pop("XAA_SIGNING_PASSWORD", None)
        build_environment.pop("XAA_SIGNING_CERTIFICATE_BASE64", None)
        subprocess.run(command, check=True, env=build_environment)
        _write_github_output(source_environment, "unsigned")
        return "unsigned"

    if not source_environment.get("XAA_SIGNING_PASSWORD", "").strip():
        raise SystemExit("Configure XAA_SIGNING_PASSWORD alongside the certificate secret.")
    try:
        certificate_content = _certificate_bytes(encoded_certificate)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    temp_root = source_environment.get("RUNNER_TEMP")
    if not temp_root or not Path(temp_root).is_dir():
        temp_root = None
    with tempfile.TemporaryDirectory(prefix="xaa-signing-", dir=temp_root) as temporary:
        certificate_path = Path(temporary) / "signing.pfx"
        certificate_path.write_bytes(certificate_content)
        build_environment = dict(source_environment)
        build_environment["XAA_SIGNING_CERTIFICATE"] = str(certificate_path.resolve())
        build_environment.pop("XAA_SIGNING_CERTIFICATE_BASE64", None)
        subprocess.run(command, check=True, env=build_environment)
    _write_github_output(source_environment, "signed")
    return "signed"


def main() -> None:
    build()


if __name__ == "__main__":
    main()
