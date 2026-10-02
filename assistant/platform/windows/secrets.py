"""Use Windows Credential Manager explicitly; never a plaintext fallback."""


def vault():
    from keyring.backends.Windows import WinVaultKeyring

    return WinVaultKeyring()
