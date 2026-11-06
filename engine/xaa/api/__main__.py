"""Run the loopback engine API."""

from __future__ import annotations

import logging
import os

import uvicorn
from pydantic import SecretStr

from xaa.api.app import create_app
from xaa.credentials import API_LAUNCH_TOKEN_PROFILE, API_LAUNCH_URL_PROFILE, CredentialStore
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
    port = int(os.environ.get("XAA_API_PORT", "8765"))
    api_url = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            timeout_graceful_shutdown=3,
        )
    )
    shared_profiles: list[tuple[str, str]] = []
    try:
        credential_store.set(token, API_LAUNCH_TOKEN_PROFILE)
        shared_profiles.append((API_LAUNCH_TOKEN_PROFILE, token))
        credential_store.set(api_url, API_LAUNCH_URL_PROFILE)
        shared_profiles.append((API_LAUNCH_URL_PROFILE, api_url))
    except Exception:
        logging.getLogger(__name__).warning(
            "ACP API token handoff is unavailable because Windows Credential Manager could not be updated.",
            exc_info=True,
        )
    try:
        server.run()
    finally:
        for profile, value in shared_profiles:
            try:
                if credential_store.get(profile) == value:
                    credential_store.delete(profile)
            except Exception:
                logging.getLogger(__name__).warning(
                    "Could not remove a temporary ACP API connection value from Windows Credential Manager.",
                    exc_info=True,
                )


if __name__ == "__main__":
    main()
