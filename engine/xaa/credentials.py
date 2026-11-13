"""OS-backed credentials; never fall back to plaintext storage."""

import os
import re
from typing import Protocol, cast

API_LAUNCH_TOKEN_PROFILE = "api-launch"
API_LAUNCH_URL_PROFILE = "api-launch-url"


class OSVault(Protocol):
    def get_password(self, service: str, username: str) -> str | None: ...
    def set_password(self, service: str, username: str, password: str) -> None: ...
    def delete_password(self, service: str, username: str) -> None: ...


class CredentialStore:
    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("OS credential storage currently supports Windows only.")
        namespace = os.environ.get("XAA_CREDENTIAL_NAMESPACE", "")
        if namespace and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", namespace) is None:
            raise ValueError("Credential namespace must contain 1–64 letters, digits, '_' or '-'.")
        self.service = "Xyst.AI.Agent" + (f".{namespace}" if namespace else "")
        from keyring.backends.Windows import WinVaultKeyring

        self.backend = cast(OSVault, WinVaultKeyring())

    def get(self, profile: str = "lm-studio") -> str | None:
        return self.backend.get_password(self.service, profile)

    def set(self, token: str, profile: str = "lm-studio") -> None:
        if not token:
            raise ValueError("Credential token cannot be empty.")
        self.backend.set_password(self.service, profile, token)

    def delete(self, profile: str = "lm-studio") -> None:
        if self.get(profile) is not None:
            self.backend.delete_password(self.service, profile)
