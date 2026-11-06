"""API-key resolution and storage (architecture §8.2, REQ-SES-02, REQ-NFR-03).

The key comes from ``ANTHROPIC_API_KEY`` if set and non-empty, else from the OS keyring (Windows
Credential Manager). It is never written to settings, sessions or logs, and is never shown with
more than its last 4 characters.

Pure logic: must not import PySide6. Every function takes an optional ``backend`` (anything with
keyring's ``get_password``/``set_password``/``delete_password``) and ``env`` for tests.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Protocol

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

logger = logging.getLogger(__name__)

API_KEY_ENV = "ANTHROPIC_API_KEY"
KEYRING_SERVICE = "screen-copier"
KEYRING_USERNAME = "anthropic_api_key"
MASK = "••••"


class KeyringBackend(Protocol):
    def get_password(self, service_name: str, username: str) -> str | None: ...

    def set_password(self, service_name: str, username: str, password: str) -> None: ...

    def delete_password(self, service_name: str, username: str) -> None: ...


def _backend(backend: KeyringBackend | None) -> KeyringBackend:
    # The keyring module itself exposes the three functions, so it satisfies the protocol.
    return backend if backend is not None else keyring  # type: ignore[return-value]


def _env_key(env: Mapping[str, str] | None) -> str | None:
    value = (os.environ if env is None else env).get(API_KEY_ENV, "").strip()
    return value or None


def _keyring_key(backend: KeyringBackend | None) -> str | None:
    try:
        value = _backend(backend).get_password(KEYRING_SERVICE, KEYRING_USERNAME)
    except (KeyringError, RuntimeError):
        # NoKeyringError is both; RuntimeError also covers misconfigured backends.
        logger.warning("Secure key storage is unavailable")
        return None
    return value or None


def get_api_key(
    backend: KeyringBackend | None = None, env: Mapping[str, str] | None = None
) -> str | None:
    """Return the API key: the environment variable first, then the keyring, else ``None``."""
    return _env_key(env) or _keyring_key(backend)


def api_key_from_env(env: Mapping[str, str] | None = None) -> bool:
    """True if the key in use comes from ``ANTHROPIC_API_KEY`` (so Settings can say so)."""
    return _env_key(env) is not None


def set_api_key(key: str, backend: KeyringBackend | None = None) -> None:
    """Store the key in the keyring.

    Raises ``ValueError`` for an empty key, and ``keyring.errors.KeyringError`` if storage fails.
    """
    key = key.strip()
    if not key:
        raise ValueError("API key must not be empty")
    _backend(backend).set_password(KEYRING_SERVICE, KEYRING_USERNAME, key)
    logger.info("Stored API key %s", mask_key(key))


def clear_api_key(backend: KeyringBackend | None = None) -> None:
    """Remove the stored key. Does nothing if none is stored.

    Raises ``keyring.errors.KeyringError`` if the keyring itself fails.
    """
    try:
        _backend(backend).delete_password(KEYRING_SERVICE, KEYRING_USERNAME)
    except PasswordDeleteError:
        return
    logger.info("Removed the stored API key")


def keyring_available(backend: KeyringBackend | None = None) -> bool:
    """True if the keyring can be read without error."""
    try:
        _backend(backend).get_password(KEYRING_SERVICE, KEYRING_USERNAME)
    except (KeyringError, RuntimeError):
        return False
    return True


def mask_key(key: str | None) -> str:
    """Return ``••••`` plus the key's last 4 characters (or just ``••••`` for a short key)."""
    if not key:
        return ""
    key = key.strip()
    return MASK + key[-4:] if len(key) > 4 else MASK
