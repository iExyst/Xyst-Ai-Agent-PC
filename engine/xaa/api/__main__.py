"""Run the loopback engine API."""

from __future__ import annotations

import os

import uvicorn
from pydantic import SecretStr

from xaa.api.app import create_app
from xaa.credentials import CredentialStore
from xaa.providers.lm_studio import LMStudioProvider

token = os.environ.get("XAA_API_TOKEN", "")
if len(token) < 32:
    raise SystemExit("Set XAA_API_TOKEN to a random value of at least 32 characters.")

server: uvicorn.Server
credential_store = CredentialStore()


def request_shutdown() -> None:
    server.should_exit = True


app = create_app(
    LMStudioProvider(
        os.environ.get("LM_STUDIO_URL", "http://127.0.0.1:1234"),
        credential_store.get(),
        exact_context=os.environ.get("XAA_EXACT_CONTEXT") == "1",
    ),
    SecretStr(token),
    shutdown_callback=request_shutdown,
    credential_store=credential_store,
    stream_responses=os.environ.get("XAA_STREAM_RESPONSES") == "1",
    trusted_origins=("http://tauri.localhost", "https://tauri.localhost")
    + ((os.environ["XAA_UI_ORIGIN"],) if os.environ.get("XAA_UI_ORIGIN") else ()),
)


def main() -> None:
    global server
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=int(os.environ.get("XAA_API_PORT", "8765")),
            timeout_graceful_shutdown=3,
        )
    )
    server.run()


if __name__ == "__main__":
    main()
