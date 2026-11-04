"""Stage the compiled desktop and engine together for native development checks."""

import hashlib
import json
import shutil
from pathlib import Path

destination = Path("build/native-dev")
destination.mkdir(parents=True, exist_ok=True)
sources = {
    "xaa-desktop.exe": Path("apps/desktop/src-tauri/target/debug/xaa-desktop.exe"),
    "xaa-engine.exe": Path("apps/desktop/src-tauri/binaries/xaa-engine-x86_64-pc-windows-msvc.exe"),
}
manifest: dict[str, str] = {}
for name, source in sources.items():
    target = destination / name
    shutil.copy2(source, target)
    manifest[name] = hashlib.sha256(target.read_bytes()).hexdigest()
(destination / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
print(f"Staged native development build: {destination.resolve()}")
