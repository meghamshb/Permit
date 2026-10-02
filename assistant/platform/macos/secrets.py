"""Use macOS Keychain explicitly; never a plaintext fallback."""


def vault():
    from keyring.backends.macOS import Keyring

    return Keyring()
