"""C's shared credential contract for providers and D's connectors."""

import secrets
import sys


class Credentials:
    def __init__(self, backend=None):
        if backend is None:
            if sys.platform == "win32":
                from assistant.platform.windows.secrets import vault
            elif sys.platform == "darwin":
                from assistant.platform.macos.secrets import vault
            else:
                raise RuntimeError("Windows Credential Manager or macOS Keychain is required.")
            backend = vault()
        self.backend = backend

    def get(self, provider: str) -> str:
        key = self.backend.get_password("Permit/providers", provider)
        if not key:
            raise RuntimeError("Provider credential is not configured in the OS credential store.")
        return key

    def set(self, provider: str, value: str):
        if not value.strip():
            raise ValueError("Credential cannot be empty.")
        self.backend.set_password("Permit/providers", provider, value)

    def journal_key(self) -> bytes:
        key = self.backend.get_password("Permit/internal", "journal-hmac")
        if not key:
            key = secrets.token_hex(32)
            self.backend.set_password("Permit/internal", "journal-hmac", key)
        return bytes.fromhex(key)
