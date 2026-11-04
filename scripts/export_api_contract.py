"""Export the actual FastAPI contract; --check detects stale generated files."""

import argparse
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import SecretStr
from xaa.api.app import create_app
from xaa.providers.lm_studio import LMStudioProvider
from xaa.storage.runs import RunStore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    temporary_root = Path(".test-tmp")
    temporary_root.mkdir(exist_ok=True)
    provider = LMStudioProvider()
    try:
        with TemporaryDirectory(dir=temporary_root) as directory:
            app = create_app(
                provider,
                SecretStr("contract-export-nonsecret-placeholder"),
                store=RunStore(Path(directory) / "schema.sqlite3"),
            )
            encoded = json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"
    finally:
        asyncio.run(provider.close())
    target = Path("contracts/openapi.json")
    if args.check:
        if not target.exists() or target.read_text(encoding="utf-8") != encoded:
            raise SystemExit("API contract is stale; run scripts/export_api_contract.py.")
        print("API contract is current.")
    else:
        target.write_text(encoded, encoding="utf-8")
        print(f"Exported {target}.")


if __name__ == "__main__":
    main()
