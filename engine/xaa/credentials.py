"""OS-backed credentials; never fall back to plaintext storage."""

import os
from typing import Protocol, cast


class OSVault(Protocol):
    def get_password(self, service: str, username: str) -> str | None: ...
    def set_password(self, service: str, username: str, password: str) -> None: ...
    def delete_password(self, service: str, username: str) -> None: ...


class CredentialStore:
    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("OS credential storage currently supports Windows only.")
        from keyring.backends.Windows import WinVaultKeyring

        self.backend = cast(OSVault, WinVaultKeyring())

    def get(self, profile: str = "lm-studio") -> str | None:
        return self.backend.get_password("Xyst.AI.Agent", profile)

    def set(self, token: str, profile: str = "lm-studio") -> None:
        if not token:
            raise ValueError("Credential token cannot be empty.")
        self.backend.set_password("Xyst.AI.Agent", profile, token)

    def delete(self, profile: str = "lm-studio") -> None:
        if self.get(profile) is not None:
            self.backend.delete_password("Xyst.AI.Agent", profile)
