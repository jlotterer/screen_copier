"""Tests for API-key resolution and masking (TASK-04, REQ-SES-02, REQ-NFR-03)."""

from __future__ import annotations

import pytest
from keyring.errors import KeyringError, NoKeyringError, PasswordDeleteError

from screen_copier.config import secrets
from screen_copier.config.secrets import (
    api_key_from_env,
    clear_api_key,
    get_api_key,
    keyring_available,
    mask_key,
    set_api_key,
)

ENV_KEY = "sk-ant-test-0000"
STORED_KEY = "sk-ant-stored-1234"


class FakeKeyring:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service_name: str, username: str) -> str | None:
        return self.store.get((service_name, username))

    def set_password(self, service_name: str, username: str, password: str) -> None:
        self.store[(service_name, username)] = password

    def delete_password(self, service_name: str, username: str) -> None:
        try:
            del self.store[(service_name, username)]
        except KeyError:
            raise PasswordDeleteError("not found") from None


class BrokenKeyring:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def get_password(self, service_name: str, username: str) -> str | None:
        raise self.error

    def set_password(self, service_name: str, username: str, password: str) -> None:
        raise self.error

    def delete_password(self, service_name: str, username: str) -> None:
        raise self.error


@pytest.fixture
def stored() -> FakeKeyring:
    kr = FakeKeyring()
    kr.set_password("screen-copier", "anthropic_api_key", STORED_KEY)
    return kr


def test_req_ses_02_env_takes_priority_over_keyring(stored: FakeKeyring) -> None:
    assert get_api_key(stored, {"ANTHROPIC_API_KEY": ENV_KEY}) == ENV_KEY
    assert api_key_from_env({"ANTHROPIC_API_KEY": ENV_KEY})


def test_keyring_only(stored: FakeKeyring) -> None:
    assert get_api_key(stored, {}) == STORED_KEY
    assert not api_key_from_env({})


def test_empty_env_var_falls_through_to_keyring(stored: FakeKeyring) -> None:
    assert get_api_key(stored, {"ANTHROPIC_API_KEY": "  "}) == STORED_KEY


def test_neither_gives_none() -> None:
    assert get_api_key(FakeKeyring(), {}) is None


@pytest.mark.parametrize("error", [KeyringError("locked"), NoKeyringError("no backend")])
def test_keyring_error_means_unavailable(error: Exception) -> None:
    broken = BrokenKeyring(error)
    assert keyring_available(broken) is False
    assert get_api_key(broken, {}) is None
    # The environment variable still works without a keyring.
    assert get_api_key(broken, {"ANTHROPIC_API_KEY": ENV_KEY}) == ENV_KEY


def test_keyring_available_with_working_backend() -> None:
    assert keyring_available(FakeKeyring()) is True


def test_reads_os_environ_by_default(stored: FakeKeyring, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", ENV_KEY)
    assert get_api_key(stored) == ENV_KEY
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert get_api_key(stored) == STORED_KEY


def test_default_backend_is_keyring_module(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeKeyring()
    monkeypatch.setattr(secrets, "keyring", fake)
    set_api_key(STORED_KEY)
    assert get_api_key(env={}) == STORED_KEY


def test_set_and_clear() -> None:
    kr = FakeKeyring()
    set_api_key("  " + STORED_KEY + "\n", kr)
    assert get_api_key(kr, {}) == STORED_KEY
    clear_api_key(kr)
    assert get_api_key(kr, {}) is None
    clear_api_key(kr)  # clearing twice is fine


def test_set_rejects_empty_key() -> None:
    with pytest.raises(ValueError):
        set_api_key("   ", FakeKeyring())


def test_set_and_clear_surface_keyring_errors() -> None:
    broken = BrokenKeyring(KeyringError("locked"))
    with pytest.raises(KeyringError):
        set_api_key(STORED_KEY, broken)
    with pytest.raises(KeyringError):
        clear_api_key(broken)


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("sk-ant-abcdefgh-abcd", "••••abcd"),
        (STORED_KEY, "••••1234"),
        ("abcd", "••••"),
        ("", ""),
        (None, ""),
    ],
)
def test_mask_key_shows_only_last_four(key: str | None, expected: str) -> None:
    assert mask_key(key) == expected
